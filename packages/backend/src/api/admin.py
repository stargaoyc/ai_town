"""管理接口 API 路由

提供强制 Tick、世界时间重置、系统状态查询、角色卡导入、
向量检索调试、日志/指标查询、运行时配置管理等管理功能。

所有接口前缀：/api/v1/admin
"""

import json
from collections import defaultdict
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException
from prometheus_client import REGISTRY
from prometheus_client.exposition import generate_latest
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import desc, select, text
from structlog import get_logger

from src.auth.rbac import require_role
from src.config import Settings, settings
from src.core.evolutions.time_evolution import (
    TIME_KEY,
    compute_day_phase,
    compute_season,
)
from src.db.models import Conversation, Message, WorldSnapshot
from src.db.repositories import CharacterRepository, MemoryRepository
from src.db.session import db
from src.modules import CharacterImporter
from src.observability.logging import _ensure_log_dir
from src.runtime import (
    get_character_engine,
    get_duration_calculator,
    get_embedding_worker,
    get_llm,
    get_movement_system,
    get_partition_scheduler,
    get_redis,
    get_registry,
    get_scene_loader,
    get_schedule_system,
    get_world_engine,
)
from src.security.rate_limit_dep import rate_limit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
logger = get_logger(__name__)

# 依赖类型别名（避免 B008：不在函数默认参数中调用 Depends/Body）
AdminOrOperator = Annotated[dict, Depends(require_role("admin", "operator"))]
Admin = Annotated[dict, Depends(require_role("admin"))]
BodyDict = Annotated[dict, Body(...)]


@router.post("/tick")
async def force_tick(
    user: AdminOrOperator,
    character_id: str | None = None,
):
    """强制触发 Tick（管理接口）

    Args:
        character_id: 可选，指定角色 ID。如果为空则对所有活跃角色执行 Tick

    Returns:
        执行结果
    """
    character_engine = get_character_engine()
    if not character_engine:
        raise HTTPException(
            status_code=503,
            detail="Character engine not initialized or unavailable",
        )

    if character_id:
        # 单个角色 Tick
        try:
            cid = UUID(character_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid UUID format") from None

        await character_engine.tick_character(cid)
        return {"message": f"Tick executed for character {character_id}", "count": 1}

    # 全量 Tick
    async with db.session() as session:
        repo = CharacterRepository(session)
        characters = await repo.get_active_characters()

    if not characters:
        return {"message": "No active characters found", "count": 0}

    success_count = 0
    for char in characters:
        try:
            await character_engine.tick_character(char.id)
            success_count += 1
        except Exception as e:
            logger.error(
                "force_tick_failed",
                character_id=str(char.id),
                error=str(e),
                exc_info=True,
            )

    return {
        "message": f"Tick executed for {success_count}/{len(characters)} characters",
        "total": len(characters),
        "success": success_count,
        "failed": len(characters) - success_count,
    }


@router.post("/world/tick")
async def force_world_tick(user: AdminOrOperator):
    """强制触发 World Tick（管理接口）

    Returns:
        执行结果
    """
    world_engine = get_world_engine()
    if not world_engine:
        raise HTTPException(status_code=503, detail="World engine not initialized")

    try:
        await world_engine._execute_tick()
        return {
            "message": "World tick executed",
            "tick_id": world_engine.tick_id,
        }
    except Exception as e:
        logger.error("force_world_tick_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"World tick failed: {str(e)}") from e


@router.post("/world/reset-time")
async def reset_world_time(
    user: Admin,
    new_time: str | None = None,
):
    """重置世界虚拟时间（管理接口）

    清除 Redis 中的旧时间状态，让时间演化器在下次 Tick 时重新初始化。
    用于修复初始时间设置错误或需要重置世界时间的场景。

    Args:
        new_time: 可选，ISO 格式的新初始时间（如 "2026-07-13T08:00:00"）。
                  留空则使用当前现实日期的 08:00 或环境变量配置。

    Returns:
        重置结果
    """
    redis = get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis not connected")

    # 读取旧时间用于日志
    old_state = await redis.hgetall("world:state:time")
    old_time = old_state.get("world_time", "unknown") if old_state else "none"

    # 清除时间状态
    await redis.delete("world:state:time")
    # 同时清除主哈希中的 world_time 字段
    await redis.hdel("world:state", "world_time")

    # 如果指定了新时间，直接写入
    if new_time:
        try:
            parsed = datetime.fromisoformat(new_time)
            await redis.hset(
                TIME_KEY,
                mapping={  # type: ignore[arg-type]
                    "world_time": parsed.isoformat(),
                    "tick_id": "0",
                    "day_phase": compute_day_phase(parsed.hour),
                    "season": compute_season(parsed.month),
                },
            )
            # 同步到主哈希
            await redis.hset(
                "world:state",
                mapping={  # type: ignore[arg-type]
                    "world_time": parsed.isoformat(),
                    "tick_id": "0",
                },
            )
            logger.info("world_time_reset", old_time=old_time, new_time=parsed.isoformat(), source="api_specified")
            return {
                "message": "World time reset successfully",
                "old_time": old_time,
                "new_time": parsed.isoformat(),
            }
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=f"Invalid time format: {new_time}") from None

    logger.info(
        "world_time_reset", old_time=old_time, new_time="(will reinitialize on next tick)", source="api_default"
    )
    return {
        "message": "World time cleared. Will reinitialize on next tick using WORLD_INITIAL_TIME env or current date.",
        "old_time": old_time,
    }


@router.get("/status")
async def get_admin_status():
    """获取系统状态（管理接口）

    Returns:
        各组件运行状态
    """
    redis = get_redis()
    world_engine = get_world_engine()
    character_engine = get_character_engine()
    registry = get_registry()
    llm = get_llm()
    embedding_worker = get_embedding_worker()
    partition_scheduler = get_partition_scheduler()
    scene_loader = get_scene_loader()
    schedule_system = get_schedule_system()
    duration_calculator = get_duration_calculator()
    movement_system = get_movement_system()

    # 读取当前世界时间
    current_world_time = None
    if redis:
        try:
            time_state = await redis.hgetall("world:state:time")
            if time_state:
                wt_raw = time_state.get("world_time", "")
                try:
                    parsed = json.loads(wt_raw)
                    current_world_time = parsed if isinstance(parsed, str) else wt_raw
                except (json.JSONDecodeError, TypeError):
                    current_world_time = wt_raw
        except Exception:
            pass
    return {
        "redis": "connected" if redis else "disconnected",
        "world_engine": {
            "running": world_engine is not None,
            "tick_id": world_engine.tick_id if world_engine else 0,
            "is_leader": world_engine.is_leader if world_engine else False,
            "current_world_time": current_world_time,
        },
        "character_engine": {
            "available": character_engine is not None,
            "tick_interval": settings.character_tick_seconds,
        },
        "action_registry": {
            "initialized": registry is not None,
            "action_count": len(registry.list_all()) if registry else 0,
        },
        "llm": {
            "initialized": llm is not None,
            "model": settings.model_chat,
        },
        "embedding_worker": {
            "running": embedding_worker is not None and embedding_worker._running,
        },
        "partition_scheduler": {
            "running": partition_scheduler is not None and partition_scheduler.running,
        },
        "phase2": {
            "scene_loader": scene_loader is not None,
            "schedule_system": schedule_system is not None,
            "duration_calculator": duration_calculator is not None,
            "movement_system": movement_system is not None,
            "scenes_loaded": len(scene_loader.get_all_scenes()) if scene_loader else 0,
        },
    }


# === Phase 2 API：角色卡导入 ===


@router.post("/characters/import", dependencies=[Depends(rate_limit("char_import", 10, 60))])
async def import_character_card(
    payload: BodyDict,
):
    """导入角色卡 YAML 文件

    通过 JSON body 提供 yaml 字段（值为 YAML 字符串）。

    Args:
        payload: JSON body，包含 yaml 字段

    Returns:
        创建的角色信息
    """
    redis = get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    yaml_text = payload.get("yaml")
    if not yaml_text:
        raise HTTPException(
            status_code=422,
            detail="请在 JSON body 中提供 yaml 字段",
        )

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败: {e}") from e

    async with db.session() as session:
        importer = CharacterImporter(session, redis)
        try:
            character = await importer.import_from_dict(data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"角色卡校验失败: {e}") from e

    return {
        "message": "角色导入成功",
        "character": {
            "id": str(character.id),
            "name": character.name,
            "age": character.age,
            "occupation": character.occupation,
        },
    }


@router.post("/characters/import-batch")
async def import_characters_batch(
    payload: BodyDict,
):
    """批量导入角色卡（多角色 YAML，用 --- 分隔）

    Args:
        payload: JSON body，包含 yaml 字段（多角色 YAML 文本）

    Returns:
        导入结果统计
    """
    redis = get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    yaml_text = payload.get("yaml")
    if not yaml_text:
        raise HTTPException(
            status_code=422,
            detail="请在 JSON body 中提供 yaml 字段",
        )

    # 解析多文档 YAML（--- 分隔）
    try:
        docs = list(yaml.safe_load_all(yaml_text))
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败: {e}") from e

    docs = [d for d in docs if d]  # 过滤空文档

    async with db.session() as session:
        importer = CharacterImporter(session, redis)
        characters = []
        for i, data in enumerate(docs):
            try:
                character = await importer.import_from_dict(data)
                characters.append(character)
            except Exception as e:
                logger.warning("batch_import_item_failed", index=i, error=str(e))

    return {
        "message": f"批量导入完成: {len(characters)} 个角色",
        "characters": [{"id": str(c.id), "name": c.name} for c in characters],
        "total": len(characters),
    }


@router.get("/onebot/messages")
async def get_onebot_messages(limit: int = 50):
    """获取 QQ 消息记录（用于 QQ 消息监控）

    查询 platform=qq 的会话中的最近消息，包含发送者和内容。

    Returns:
        消息列表（按时间倒序）
    """
    async with db.session() as session:
        # 联合查询 Conversation + Message
        stmt = (
            select(Message, Conversation)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.platform == "qq")
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()

    return {
        "data": [
            {
                "message_id": str(msg.id),
                "conversation_id": str(conv.id),
                "character_id": str(conv.character_id),
                "user_id": conv.user_id,
                "sender": msg.sender,
                "content": msg.content,
                "tokens": msg.tokens,
                "cost": msg.cost,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg, conv in rows
        ],
        "total": len(rows),
    }


@router.get("/proactive-shares")
async def get_proactive_shares(limit: int = 50):
    """获取主动分享历史记录

    仅查询 extra_data.share_type='proactive' 的消息，
    按 share_id 去重（同一次分享投递给多个用户只显示一条）。

    Returns:
        分享记录列表
    """
    async with db.session() as session:
        # 使用 DISTINCT ON 按 share_id 去重，每个 share_id 只取第一条
        stmt = (
            select(
                Message,
                Conversation.character_id,
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.sender == "character",
                text("extra_data->>'share_type' = 'proactive'"),
            )
            .order_by(
                text("extra_data->>'share_id'"),
                desc(Message.created_at),
            )
            .distinct(text("extra_data->>'share_id'"))
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = list(result.all())

    return {
        "data": [
            {
                "message_id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "character_id": str(char_id) if char_id else None,
                "character_name": (m.extra_data or {}).get("character_name", ""),
                "share_id": (m.extra_data or {}).get("share_id", ""),
                "sender": m.sender,
                "content": m.content,
                "tokens": m.tokens,
                "cost": m.cost,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, char_id in rows
        ],
        "total": len(rows),
    }


@router.post("/vector-search")
async def vector_search(
    character_id: UUID,
    query: str,
    top_k: int = 10,
):
    """向量检索测试 - 调试 pgvector 检索

    Args:
        character_id: 角色 ID
        query: 查询文本
        top_k: 返回结果数

    Returns:
        检索结果列表（含相似度分数）
    """
    llm = get_llm()
    if not llm:
        raise HTTPException(503, "LLM client not initialized")

    if not query.strip():
        raise HTTPException(400, "Query text is required")

    try:
        # 生成查询向量
        query_embedding = await llm.embed(query)

        # 使用 MemoryRepository 进行向量检索
        async with db.session() as session:
            repo = MemoryRepository(session)
            results = await repo.search_hybrid(
                character_id=character_id,
                query_vec=query_embedding,
                top_k=top_k,
            )

        return {
            "query": query,
            "character_id": str(character_id),
            "data": [
                {
                    "id": str(ep["id"]),
                    "content": ep["content"],
                    "importance": ep["importance"],
                    "timestamp": ep["timestamp"].isoformat() if ep.get("timestamp") else None,
                    "similarity": float(ep.get("sim_score", 0.0)),
                    "is_reflected": ep.get("is_reflected", False),
                    "source_type": ep.get("source_type", "action"),
                }
                for ep in results
            ],
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(500, f"Vector search failed: {e}") from e


@router.get("/world/snapshots")
async def get_world_snapshots(limit: int = 20):
    """获取世界快照列表（用于冷启动恢复管理）

    Returns:
        快照列表（按 tick_id 倒序）
    """
    async with db.session() as session:
        stmt = select(WorldSnapshot).order_by(desc(WorldSnapshot.tick_id)).limit(limit)
        result = await session.execute(stmt)
        snapshots = list(result.scalars())

    return {
        "data": [
            {
                "id": str(s.id),
                "tick_id": s.tick_id,
                "world_time": s.world_time.isoformat() if s.world_time else None,
                "weather": s.weather,
                "locations": s.locations if isinstance(s.locations, dict) else {},
                "resources": s.resources if isinstance(s.resources, dict) else {},
                "active_events": s.active_events if isinstance(s.active_events, dict) else {},
                "state": {
                    "weather": s.weather,
                    "locations": s.locations,
                    "resources": s.resources,
                    "active_events": s.active_events,
                },
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in snapshots
        ],
        "total": len(snapshots),
    }


@router.get("/logs")
async def get_recent_logs(
    lines: int = 100,
    level: Literal["debug", "info", "warning", "error"] | None = None,
):
    """获取最近的系统日志（从 data/logs/backend.log 读取）

    Args:
        lines: 返回的日志行数（最大 500）
        level: 日志级别过滤（debug/info/warning/error），不传则返回所有

    Returns:
        日志条目列表（按时间倒序，每条为 JSON 解析后的 dict）
    """
    lines = min(max(lines, 1), 500)

    try:
        log_dir = _ensure_log_dir()
        log_file = log_dir / "backend.log"
        if not log_file.exists():
            return {"data": [], "total": 0, "source": str(log_file)}

        # 读取最后 N 行（高效方式：从文件末尾向前读取）
        with open(str(log_file), encoding="utf-8") as f:
            # 读取所有行并取最后 N 行（文件不大时足够）
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        # 解析 JSON 日志行
        logs = []
        for line in reversed(recent_lines):  # 倒序：最新的在前
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                # 日志级别过滤
                if level:
                    entry_level = entry.get("level", "").lower()
                    if entry_level != level.lower():
                        continue
                logs.append(entry)
            except json.JSONDecodeError:
                # 非 JSON 行（如 ConsoleRenderer 输出），作为纯文本保留
                logs.append({"event": line, "level": "info", "timestamp": ""})

        return {
            "data": logs[:lines],
            "total": len(logs),
            "source": str(log_file),
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to read logs: {e}") from e


@router.get("/metrics-detail")
async def get_detailed_metrics():
    """获取详细的系统指标（解析 Prometheus 格式，返回结构化数据）

    返回比 /metrics/ 端点更易消费的 JSON 结构，包含：
    - 世界引擎指标（Tick 总数/耗时/错误/当前 ID）
    - 角色引擎指标（Tick 总数/耗时/错误，按角色分组）
    - Action 指标（执行次数/耗时，按 action_id 分组）
    - LLM 指标（调用次数/Token/费用，按 model 分组）
    - 消息指标（处理次数/耗时）
    - 数据库指标（查询耗时分布）
    - 系统状态（活跃角色数/Redis 状态）
    - HTTP 请求指标（请求数/耗时，按 path 分组）
    """
    try:
        # 从 /metrics 端点获取原始文本
        raw_text = generate_latest(REGISTRY).decode("utf-8")

        # 解析所有指标族
        families = list(text_string_to_metric_families(raw_text))

        result = {
            "world": {},
            "characters": {},
            "actions": {},
            "llm": {},
            "messages": {},
            "database": {},
            "system": {},
            "http": {},
        }

        for family in families:
            name = family.name
            for sample in family.samples:
                labels = sample.labels or {}
                value = sample.value

                # 世界引擎
                if name == "ai_town_world_tick_total":
                    result["world"]["tick_total"] = int(value)
                elif name == "ai_town_world_tick_errors_total":
                    result["world"]["errors_total"] = int(value)
                elif name == "ai_town_world_tick_id":
                    result["world"]["current_tick_id"] = int(value)
                elif name == "ai_town_world_tick_duration_seconds" and sample.name.endswith("_sum"):
                    result["world"]["duration_sum"] = value
                elif name == "ai_town_world_tick_duration_seconds" and sample.name.endswith("_count"):
                    result["world"]["duration_count"] = int(value)

                # 角色引擎
                elif name == "ai_town_character_tick_total":
                    char_id = labels.get("character_id", "unknown")
                    result["characters"].setdefault("by_character", defaultdict(int))
                    result["characters"]["by_character"][char_id] += int(value)
                elif name == "ai_town_character_tick_errors_total":
                    char_id = labels.get("character_id", "unknown")
                    result["characters"].setdefault("errors_by_character", defaultdict(int))
                    result["characters"]["errors_by_character"][char_id] += int(value)

                # Action
                elif name == "ai_town_action_execution_total":
                    action_id = labels.get("action_id", "unknown")
                    status = labels.get("status", "unknown")
                    result["actions"].setdefault("by_action", {})
                    result["actions"]["by_action"].setdefault(action_id, {"success": 0, "failed": 0})
                    result["actions"]["by_action"][action_id][status] += int(value)

                # LLM
                elif name == "ai_town_llm_call_total":
                    model = labels.get("model", "unknown")
                    status = labels.get("status", "unknown")
                    result["llm"].setdefault("calls", {})
                    result["llm"]["calls"].setdefault(model, {"success": 0, "failed": 0})
                    result["llm"]["calls"][model][status] += int(value)
                elif name == "ai_town_llm_tokens_total":
                    model = labels.get("model", "unknown")
                    token_type = labels.get("type", "unknown")
                    result["llm"].setdefault("tokens", {})
                    result["llm"]["tokens"].setdefault(model, {"prompt": 0, "completion": 0})
                    result["llm"]["tokens"][model][token_type] += int(value)
                elif name == "ai_town_llm_cost_total_usd":
                    result["llm"]["cost_total_usd"] = value

                # 消息
                elif name == "ai_town_message_processed_total":
                    platform = labels.get("platform", "unknown")
                    status = labels.get("status", "unknown")
                    result["messages"].setdefault("by_platform", {})
                    result["messages"]["by_platform"].setdefault(platform, {"success": 0, "failed": 0})
                    result["messages"]["by_platform"][platform][status] += int(value)

                # 系统
                elif name == "ai_town_active_characters":
                    result["system"]["active_characters"] = int(value)
                elif name == "ai_town_redis_connected":
                    result["system"]["redis_connected"] = int(value)

                # HTTP
                elif name == "ai_town_http_request_total":
                    path = labels.get("path", "/")
                    status = labels.get("status", "unknown")
                    result["http"].setdefault("requests", {})
                    result["http"]["requests"].setdefault(path, {"total": 0, "by_status": {}})
                    result["http"]["requests"][path]["total"] += int(value)
                    result["http"]["requests"][path]["by_status"][status] = result["http"]["requests"][path][
                        "by_status"
                    ].get(status, 0) + int(value)

        # 转换 defaultdict 为普通 dict
        if "by_character" in result["characters"]:
            result["characters"]["by_character"] = dict(result["characters"]["by_character"])
        if "errors_by_character" in result["characters"]:
            result["characters"]["errors_by_character"] = dict(result["characters"]["errors_by_character"])

        # 计算汇总
        result["characters"]["tick_total"] = sum(result["characters"].get("by_character", {}).values())
        result["llm"]["tokens_total"] = sum(sum(t.values()) for t in result["llm"].get("tokens", {}).values())
        result["llm"]["calls_total"] = sum(sum(c.values()) for c in result["llm"].get("calls", {}).values())

        return {"data": result}
    except Exception as e:
        raise HTTPException(500, f"Failed to parse metrics: {e}") from e


# === 运行时配置管理 ===

# 可通过前端动态调整的配置项白名单（键名 → 类型）
_RUNTIME_CONFIG_KEYS = {
    "share_cooldown_seconds": int,
    "share_daily_limit": int,
    "share_probability_action": float,
    "share_probability_mood": float,
    "share_probability_location": float,
    "share_probability_routine": float,
    "memory_llm_scoring_enabled": bool,
    "world_tick_seconds": int,
    "character_tick_seconds": int,
    "character_max_concurrent": int,
    "llm_daily_budget_usd": float,
    "log_level": str,
}

# 配置项中文说明
_CONFIG_LABELS = {
    "share_cooldown_seconds": "分享冷却时间（秒）",
    "share_daily_limit": "每日分享上限",
    "share_probability_action": "Action 分享概率",
    "share_probability_mood": "情绪分享概率",
    "share_probability_location": "位置变化分享概率",
    "share_probability_routine": "日常行为分享概率",
    "memory_llm_scoring_enabled": "LLM 记忆评分",
    "world_tick_seconds": "世界 Tick 间隔（秒）",
    "character_tick_seconds": "角色 Tick 间隔（秒）",
    "character_max_concurrent": "角色并发上限",
    "llm_daily_budget_usd": "LLM 日预算（美元）",
    "log_level": "日志级别",
}


@router.get("/config")
async def get_runtime_config():
    """获取运行时配置（环境变量默认值 + Redis 覆盖值）

    Returns:
        各配置项的当前值、默认值、类型和说明
    """
    redis = get_redis()

    # 从 Redis 读取运行时覆盖
    overrides = {}
    if redis:
        raw = await redis.get("config:overrides")
        if raw:
            try:
                overrides = json.loads(raw)  # type: ignore[arg-type]
            except (json.JSONDecodeError, TypeError):
                pass

    result = []
    for key, typ in _RUNTIME_CONFIG_KEYS.items():
        default_val = getattr(settings, key, None)
        current_val = overrides.get(key, default_val)
        result.append(
            {
                "key": key,
                "label": _CONFIG_LABELS.get(key, key),
                "type": typ.__name__,
                "default": default_val,
                "current": current_val,
                "overridden": key in overrides,
            }
        )

    return {"data": result, "total": len(result)}


@router.put("/config")
async def update_runtime_config(
    updates: BodyDict,
    user: Admin,
):
    """更新运行时配置（写入 Redis 覆盖值，无需重启）

    仅允许更新白名单中的配置项。
    更新后立即生效（后续读取会优先使用 Redis 覆盖值）。

    Args:
        updates: {key: value} 配置更新字典

    Returns:
        更新结果
    """
    redis = get_redis()
    if not redis:
        raise HTTPException(500, "Redis not available for config override")

    # 读取现有覆盖
    raw = await redis.get("config:overrides")
    overrides = {}
    if raw:
        try:
            overrides = json.loads(raw)  # type: ignore[arg-type]
        except (json.JSONDecodeError, TypeError):
            pass

    # 应用更新（仅白名单中的键）
    updated = []
    for key, value in updates.items():
        if key not in _RUNTIME_CONFIG_KEYS:
            continue
        typ = _RUNTIME_CONFIG_KEYS[key]
        try:
            if typ is bool:
                value = bool(value) if not isinstance(value, str) else value.lower() in ("true", "1", "yes")
            elif typ is int:
                value = int(value)
            elif typ is float:
                value = float(value)
            else:
                value = str(value)
            overrides[key] = value
            updated.append({"key": key, "value": value, "label": _CONFIG_LABELS.get(key, key)})
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"Invalid value for '{key}': {e}") from e

    # 写回 Redis
    await redis.set("config:overrides", json.dumps(overrides))

    # 同时更新 settings 对象（内存中立即生效）
    for item in updated:
        setattr(settings, item["key"], item["value"])

    return {
        "success": True,
        "updated": len(updated),
        "data": updated,
    }


@router.delete("/config/{key}")
async def reset_config_item(key: str):
    """重置单个配置项为默认值（删除 Redis 覆盖）

    Args:
        key: 配置项键名
    """
    redis = get_redis()
    if not redis:
        raise HTTPException(500, "Redis not available")

    if key not in _RUNTIME_CONFIG_KEYS:
        raise HTTPException(400, f"Unknown config key: {key}")

    raw = await redis.get("config:overrides")
    if raw:
        try:
            overrides = json.loads(raw)  # type: ignore[arg-type]
        except (json.JSONDecodeError, TypeError):
            overrides = {}

        if key in overrides:
            del overrides[key]
            await redis.set("config:overrides", json.dumps(overrides))

    # 恢复默认值到 settings 对象
    default_val = getattr(Settings(), key, None)  # type: ignore[call-arg]
    setattr(settings, key, default_val)

    return {"success": True, "key": key, "reset_to": default_val}
