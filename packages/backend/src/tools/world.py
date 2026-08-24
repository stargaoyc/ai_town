"""世界信息查询工具模块 - 只读世界状态查询

从 MCP Server 概念迁移为直接工具调用，消除 HTTP/SSE 网络开销。
为 AI Town 角色（LLM）在 Tick 决策时提供小镇世界状态的只读查询能力。

设计：
- 只读：所有函数不修改 Redis/PG 状态，仅查询
- 直接调用：通过 src.runtime.get_redis() / src.db.session.db 直达存储层
- 场景配置从 configs/scenes.yaml 读取（项目根目录相对路径）
- 场景出口来自 SceneLoader 内存中的连通矩阵（runtime 注入）

覆盖能力：
- get_world_info: 世界当前状态（tick/时间/天气/季节/时段）
- find_character_by_name: 按名查询角色档案（不暴露 location 等 Redis 私有状态）
- get_scene_info: 单场景详情 + 邻接出口（移动矩阵）
- list_scenes: 全部场景摘要
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from src.db.repositories import CharacterRepository
from src.db.session import db
from src.runtime import get_redis, get_scene_loader

logger = structlog.get_logger()

# Redis 键名
WORLD_STATE_KEY = "world:state"
WORLD_TIME_KEY = "world:state:time"

# 场景配置文件路径
# 文件位于 packages/backend/src/tools/world.py，parents[4] 为项目根目录 aitown
_SCENES_PATH = Path(__file__).resolve().parents[4] / "configs" / "scenes.yaml"


def _decode(value: Any) -> str:
    """Redis 返回值统一解码为 str

    兼容 decode_responses=True/False 两种配置：bytes/bytearray/memoryview 解码，其余转 str。
    world:state hash 中的值可能被 JSON 编码（如 '"spring"'），去除首尾引号还原原始字符串。
    """
    if isinstance(value, (bytes, bytearray)):
        s = value.decode("utf-8")
    elif isinstance(value, memoryview):
        s = value.tobytes().decode("utf-8")
    else:
        s = str(value)
    # 去除 JSON 字符串的首尾引号（如 '"spring"' -> 'spring'）
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s


def _load_scenes() -> list[dict[str, Any]]:
    """从 configs/scenes.yaml 加载场景列表"""
    with open(_SCENES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data:
        return []
    scenes: list[dict[str, Any]] = data.get("scenes", [])
    return scenes


async def get_world_info() -> dict[str, Any]:
    """查询世界当前状态

    读取 Redis world:state 哈希获取 tick_id/world_time/weather/temperature/updated_at，
    并读取 world:state:time 哈希补充 season/day_phase。

    Returns:
        {
            "success": bool,
            "tick_id": int,
            "world_time": str,
            "weather": str,
            "temperature": str | None,
            "season": str | None,
            "day_phase": str | None,
            "updated_at": str,
        }
        Redis 未连接时返回 success=True 的空字段；读取异常时返回 {"success": False, "error": str}。
    """
    r = get_redis()
    if r is None:
        # Redis 未连接时返回空字段，避免阻塞 LLM 工具调用
        return {
            "success": True,
            "tick_id": 0,
            "world_time": "",
            "weather": "",
            "temperature": None,
            "season": None,
            "day_phase": None,
            "updated_at": "",
        }

    try:
        raw_state = await r.hgetall(WORLD_STATE_KEY)
        state = {k: _decode(v) for k, v in raw_state.items()}

        # 详细时间状态（季节/时段）单独存储在 world:state:time
        raw_time = await r.hgetall(WORLD_TIME_KEY)
        time_state = {k: _decode(v) for k, v in raw_time.items()} if raw_time else {}

        tick_id = int(state.get("tick_id") or 0)

        logger.info(
            "get_world_info_called",
            tick_id=tick_id,
            weather=state.get("weather", ""),
            season=time_state.get("season"),
        )

        return {
            "success": True,
            "tick_id": tick_id,
            "world_time": state.get("world_time", ""),
            "weather": state.get("weather", ""),
            "temperature": state.get("temperature") or None,
            "season": time_state.get("season"),
            "day_phase": time_state.get("day_phase"),
            "updated_at": state.get("updated_at", ""),
        }
    except Exception as e:
        logger.warning("get_world_info_failed", error=str(e))
        return {"success": False, "error": str(e)}


async def find_character_by_name(query_name: str) -> dict[str, Any]:
    """按角色名查询角色档案

    仅返回静态档案信息（性格特征/背景/活跃状态），不暴露 location 等 Redis 私有实时状态。

    Args:
        query_name: 角色名

    Returns:
        未找到时 {"success": False, "error": "Character not found", "query": str}；
        找到时 {"success": True, "character_id": str, "name": str, "personality": dict,
                "backstory": str | None, "is_active": bool}。
    """
    async with db.session() as session:
        repo = CharacterRepository(session)
        char = await repo.get_by_name(query_name)

    if char is None:
        logger.info("find_character_by_name_not_found", query=query_name)
        return {
            "success": False,
            "error": "Character not found",
            "query": query_name,
        }

    logger.info(
        "find_character_by_name_found",
        character_id=str(char.id),
        name=char.name,
    )

    return {
        "success": True,
        "character_id": str(char.id),
        "name": char.name,
        "personality": char.traits,
        "backstory": char.backstory,
        "is_active": char.is_active,
    }


async def get_scene_info(scene_id: str) -> dict[str, Any]:
    """查询单个场景详情及其邻接出口

    场景静态信息来自 configs/scenes.yaml；出口与移动耗时来自 SceneLoader
    内存中的连通矩阵（configs/world-map.yaml 的运行时形态）。

    Args:
        scene_id: 场景 ID

    Returns:
        场景不存在时 {"success": False, "error": "Scene not found", "scene_id": str}；
        存在时 {"success": True, "scene_id": str, "name": str, "type": str,
                "open_hours": [start, end], "capacity": int, "activities": list[str],
                "exits": {neighbor_scene_id: minutes, ...}}。
    """
    scenes = _load_scenes()
    scene = next((s for s in scenes if s.get("id") == scene_id), None)

    if scene is None:
        logger.info("get_scene_info_not_found", scene_id=scene_id)
        return {
            "success": False,
            "error": "Scene not found",
            "scene_id": scene_id,
        }

    # 出口取自 SceneLoader 的连通矩阵
    exits: dict[str, int] = {}
    loader = get_scene_loader()
    if loader is not None:
        exits = dict(loader.get_neighbors(scene_id))

    logger.info(
        "get_scene_info_called",
        scene_id=scene_id,
        exits_count=len(exits),
    )

    return {
        "success": True,
        "scene_id": scene_id,
        "name": scene.get("name", ""),
        "type": scene.get("type", ""),
        "open_hours": scene.get("open_hours", [0, 24]),
        "capacity": scene.get("capacity", 0),
        "activities": scene.get("activities", []),
        "exits": exits,
    }


async def list_scenes() -> dict[str, Any]:
    """列出全部场景摘要

    Returns:
        {"success": True, "scenes": [{"id", "name", "type"}, ...], "total": int}
    """
    scenes = _load_scenes()
    summaries = [
        {
            "id": s.get("id", ""),
            "name": s.get("name", ""),
            "type": s.get("type", ""),
        }
        for s in scenes
    ]

    logger.info("list_scenes_called", total=len(summaries))

    return {
        "success": True,
        "scenes": summaries,
        "total": len(summaries),
    }
