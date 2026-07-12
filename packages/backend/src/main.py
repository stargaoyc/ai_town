"""AI Town Backend - FastAPI 入口

启动流程：
1. 初始化 Redis / LLM / Action Registry / Memory Services
2. 预创建数据库分区（pre_create_partitions）
3. 启动 Embedding Worker（异步向量化后台任务）
4. 启动 World Engine（后台任务）
5. 启动 Character Tick Engine（后台任务）
6. 注册 API 路由
7. 监听 shutdown 信号，优雅停止

API 路由：
- /health - 健康检查
- /api/v1/characters - 角色管理
- /api/v1/world - 世界状态
- /api/v1/actions - Action 查询
- /api/v1/memories - 记忆查询
- /api/v1/admin - 管理接口（强制 Tick、快照回放等）
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from structlog import get_logger

from src.actions import ActionRegistry, register_all
from src.auth import auth_dependency, create_token
from src.config import settings
from src.core import WorldEngine
from src.cost_control.budget_manager import set_budget_manager
from src.cost_control.circuit_breaker import set_circuit_breaker
from src.db.repositories import (
    ActionRepository,
    CharacterRepository,
    ConversationRepository,
    MemoryRepository,
    MessageRepository,
    PlanRepository,
    ReflectionRepository,
    WorldEventRepository,
)
from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.memory import EpisodeService, ReflectionService, RetrievalService
from src.memory.embedding_worker import EmbeddingWorker
from src.messaging import MessageService, WebSocketManager
from src.messaging.websocket import router as ws_router
from src.modules import (
    CharacterImporter,
    DurationCalculator,
    MovementSystem,
    RelationGraph,
    ScheduleSystem,
    SceneLoader,
)
from src.observability import (
    setup_langfuse,
    setup_logging,
    setup_metrics,
    setup_tracing,
)
from src.scheduler import PartitionScheduler
from src.security.rate_limiter import RateLimiter
from src.adapters import OneBotAdapter

# 尝试导入 CharacterTickEngine（可能尚未创建）
try:
    from src.core.character_tick import CharacterTickEngine

    CHARACTER_ENGINE_AVAILABLE = True
except ImportError:
    CharacterTickEngine = None  # type: ignore
    CHARACTER_ENGINE_AVAILABLE = False

logger = get_logger(__name__)

# === 全局实例 ===
redis: Redis | None = None
world_engine: WorldEngine | None = None
character_engine: CharacterTickEngine | None = None  # type: ignore
registry: ActionRegistry | None = None
llm: LLMClient | None = None
prompts: PromptTemplates | None = None
embedding_worker: EmbeddingWorker | None = None
partition_scheduler: PartitionScheduler | None = None
rate_limiter: RateLimiter | None = None

# WebSocket 连接管理器（单例）- 用于 Web 客户端实时聊天
ws_manager = WebSocketManager()

# OneBot v12 适配器（QQ 机器人接入）
onebot_adapter = OneBotAdapter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理

    初始化顺序：
    1. Redis 连接
    2. LLM 客户端
    3. Action Registry
    4. World Engine
    5. Character Tick Engine（如果可用）
    """
    global redis, world_engine, character_engine, registry, llm, prompts
    global scene_loader, schedule_system, duration_calculator, movement_system
    global embedding_worker, partition_scheduler, rate_limiter

    logger.info("ai_town_backend_starting")

    # 0.5 初始化可观测性（日志/Trace/Metrics/Langfuse）
    setup_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger.info("logging_configured", format=settings.log_format, level=settings.log_level)

    # 1. 初始化 Redis
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        # 测试连接
        await redis.ping()
        logger.info("redis_connected", url=settings.redis_url)
        # 设置 Prometheus Redis 连接状态指标
        from src.observability.metrics import REDIS_CONNECTED
        REDIS_CONNECTED.set(1)
    except Exception as e:
        logger.error("redis_connection_failed", error=str(e), exc_info=True)
        from src.observability.metrics import REDIS_CONNECTED
        REDIS_CONNECTED.set(0)
        raise

    # 1.1 初始化成本控制 + 速率限制器（依赖 Redis）
    set_budget_manager(redis, daily_budget_usd=settings.llm_daily_budget_usd)
    set_circuit_breaker(
        redis,
        failure_threshold=settings.llm_circuit_breaker_threshold,
        recovery_timeout=settings.llm_circuit_breaker_recovery_timeout,
    )
    rate_limiter = RateLimiter(redis)
    logger.info(
        "cost_control_initialized",
        daily_budget=settings.llm_daily_budget_usd,
        circuit_threshold=settings.llm_circuit_breaker_threshold,
    )

    # 1.5 预创建分区（确保月初写入不报错）
    try:
        from src.db.session import db
        from sqlalchemy import text
        async with db.session() as session:
            await session.execute(text("SELECT pre_create_partitions(3);"))
            await session.commit()
        logger.info("partitions_pre_created", months_ahead=3)
    except Exception as e:
        logger.warning("partition_pre_create_failed", error=str(e), exc_info=True)
        # 不中断启动，分区可能已存在或由运维手动创建

    # 2. 初始化 LLM 客户端
    try:
        llm = LLMClient()
        prompts = PromptTemplates()
        logger.info("llm_initialized", model=settings.model_chat)
    except Exception as e:
        logger.error("llm_initialization_failed", error=str(e), exc_info=True)
        raise

    # 3. 初始化 Action Registry
    try:
        registry = ActionRegistry()
        register_all(registry)
        logger.info("action_registry_initialized", count=len(registry.list_all()))
    except Exception as e:
        logger.error("action_registry_initialization_failed", error=str(e), exc_info=True)
        raise

    # 3.5 启动 Embedding Worker（异步向量化后台任务）
    embedding_task: asyncio.Task | None = None
    try:
        embedding_worker = EmbeddingWorker(
            session_factory=db.session,  # type: ignore[union-attr]
            llm_client=llm,
            batch_size=20,
            poll_interval=5.0,
        )
        embedding_task = asyncio.create_task(embedding_worker.run())
        logger.info("embedding_worker_started", batch_size=20, poll_interval=5.0)
    except Exception as e:
        logger.error("embedding_worker_start_failed", error=str(e), exc_info=True)
        embedding_worker = None

    # 3.6 启动分区预创建调度器（每月 25 号 03:00 自动执行）
    # 解决 v8 P1 #68：原仅启动时执行，长期运行 >3 月漏建分区
    try:
        partition_scheduler = PartitionScheduler()
        await partition_scheduler.start()
        logger.info("partition_scheduler_started")
    except Exception as e:
        logger.error(
            "partition_scheduler_start_failed",
            error=str(e),
            exc_info=True,
        )
        partition_scheduler = None

    # 4. 启动 World Engine
    try:
        world_engine = WorldEngine(redis)
        await world_engine.start()
        logger.info("world_engine_started")
    except Exception as e:
        logger.error("world_engine_start_failed", error=str(e), exc_info=True)
        raise

    # 5. 启动 Character Tick Engine（如果模块可用）
    character_tick_task: asyncio.Task | None = None
    if CHARACTER_ENGINE_AVAILABLE and CharacterTickEngine is not None:
        try:
            character_engine = CharacterTickEngine(
                redis=redis,
                registry=registry,
                llm=llm,  # 修正参数名：llm_client → llm
                prompts=prompts,
            )
            # 启动后台任务：定期对所有活跃角色执行 Tick
            character_tick_task = asyncio.create_task(_character_tick_loop())
            logger.info("character_engine_started")
        except Exception as e:
            logger.error(
                "character_engine_start_failed",
                error=str(e),
                exc_info=True,
            )
            character_engine = None
    else:
        logger.warning(
            "character_tick_engine_not_available",
            message="CharacterTickEngine module not found, character tick loop disabled",
        )

    # 6. 初始化 Phase 2 模块
    try:
        scene_loader = SceneLoader(redis)
        # 尝试加载场景配置（文件可能不存在）
        # 使用项目根目录定位配置文件（运行目录为 packages/backend/）
        project_root = Path(__file__).resolve().parents[3]
        scenes_path = project_root / "configs" / "scenes.yaml"
        map_path = project_root / "configs" / "world-map.yaml"
        if scenes_path.exists() and map_path.exists():
            await scene_loader.load_from_files(scenes_path, map_path)
            logger.info("scene_loader_initialized", scenes=len(scene_loader.get_all_scenes()))
        else:
            logger.warning("scene_config_not_found", path=str(scenes_path))

        schedule_system = ScheduleSystem()
        duration_calculator = DurationCalculator()
        movement_system = MovementSystem(scene_loader)
        logger.info("phase2_modules_initialized")
    except Exception as e:
        logger.error("phase2_init_failed", error=str(e), exc_info=True)

    # 7. WebSocket 管理器就绪（单例已实例化，记录日志）
    logger.info(
        "ws_manager_ready",
        endpoint="/ws/chat/{character_id}",
        manager=type(ws_manager).__name__,
    )

    # 8. 启动 OneBot 适配器（QQ 机器人反向 WebSocket）
    try:
        await onebot_adapter.start()
        logger.info("onebot_adapter_started", endpoint="/ws/onebot/v12")
    except Exception as e:
        logger.error("onebot_adapter_start_failed", error=str(e), exc_info=True)

    yield

    # === Shutdown ===
    logger.info("ai_town_backend_shutting_down")

    # 刷新 Langfuse 缓冲区，确保追踪数据已发送
    from src.observability.langfuse_tracing import flush_langfuse
    flush_langfuse()

    # 停止 OneBot 适配器
    try:
        await onebot_adapter.stop()
        logger.info("onebot_adapter_stopped")
    except Exception as e:
        logger.error("onebot_adapter_stop_failed", error=str(e))

    # 停止分区调度器
    if partition_scheduler:
        await partition_scheduler.stop()
        logger.info("partition_scheduler_stopped")

    # 停止 Embedding Worker
    if embedding_worker:
        await embedding_worker.stop()
        logger.info("embedding_worker_stopped")
    if embedding_task:
        embedding_task.cancel()
        try:
            await embedding_task
        except asyncio.CancelledError:
            pass

    # 取消 Character Tick 循环
    if character_tick_task:
        character_tick_task.cancel()
        try:
            await character_tick_task
        except asyncio.CancelledError:
            pass

    # 停止 World Engine
    if world_engine:
        await world_engine.stop()
        logger.info("world_engine_stopped")

    # 关闭 Redis 连接
    if redis:
        await redis.close()
        logger.info("redis_connection_closed")


async def _character_tick_loop() -> None:
    """Character Tick 后台循环

    定期对所有活跃角色执行 Tick，推进角色状态。
    遇到 LLM 限流 (429) 时自动退避，避免抢占消息处理的 API 配额。
    """
    logger.info("character_tick_loop_started", interval=settings.character_tick_seconds)

    backoff_multiplier = 1  # 限流退避倍数
    max_backoff = 10        # 最大退避倍数

    while True:
        try:
            await asyncio.sleep(settings.character_tick_seconds * backoff_multiplier)

            if not character_engine or not redis:
                continue

            # 获取所有活跃角色
            async with db.session() as session:
                repo = CharacterRepository(session)
                characters = await repo.get_active_characters()

            if not characters:
                logger.debug("no_active_characters")
                continue

            # 更新活跃角色数 Gauge
            from src.observability.metrics import ACTIVE_CHARACTERS
            ACTIVE_CHARACTERS.set(len(characters))

            logger.info("character_tick_batch_start", count=len(characters), backoff=backoff_multiplier)

            # 对每个角色执行 Tick
            success_count = 0
            rate_limited = False
            for char in characters:
                try:
                    await character_engine.tick_character(char.id)
                    success_count += 1
                except Exception as e:
                    error_str = str(e)
                    # 记录 Character Tick 错误指标
                    from src.observability.metrics import CHARACTER_TICK_ERRORS
                    CHARACTER_TICK_ERRORS.labels(character_id=str(char.id)).inc()
                    # 检测 LLM 限流 (429)，立即停止当前批次并退避
                    if "429" in error_str or "RateLimitError" in error_str:
                        logger.warning(
                            "character_tick_rate_limited",
                            character_id=str(char.id),
                            character_name=char.name,
                            backoff_multiplier=backoff_multiplier,
                        )
                        rate_limited = True
                        break
                    logger.error(
                        "character_tick_failed",
                        character_id=str(char.id),
                        character_name=char.name,
                        error=error_str,
                        exc_info=True,
                    )

            # 限流退避：逐次增加等待时间，成功后逐步恢复
            if rate_limited:
                backoff_multiplier = min(backoff_multiplier * 2, max_backoff)
                logger.warning("character_tick_backoff", multiplier=backoff_multiplier)
            elif success_count > 0:
                backoff_multiplier = 1  # 全部或部分成功，恢复正常间隔

            logger.info(
                "character_tick_batch_complete",
                total=len(characters),
                success=success_count,
                failed=len(characters) - success_count,
                rate_limited=rate_limited,
            )

        except asyncio.CancelledError:
            logger.info("character_tick_loop_cancelled")
            raise
        except Exception as e:
            logger.error("character_tick_loop_error", error=str(e), exc_info=True)
            # 继续循环，不中断


# === FastAPI 应用实例 ===
app = FastAPI(
    title="AI Town Backend",
    description="二次元 AI 小镇陪伴智能体 - World Engine + LangGraph",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鉴权中间件（ASGI 层面，兼容 WebSocket）
from starlette.types import ASGIApp, Receive, Scope, Send


class AuthMiddleware:
    """ASGI 鉴权中间件：仅 /api/ 路径需要鉴权，WebSocket 和其他路径豁免

    鉴权策略：
    - 非 /api/ 路径（/health, /metrics, /docs 等）→ 豁免
    - /api/v1/auth/login → 豁免（登录接口）
    - GET /api/v1/ 只读公开端点 → 豁免（Dashboard 无需登录可查看）
    - 其他 /api/ 请求（POST/PUT/DELETE）→ 需要 JWT 或 API Key
    """

    # 公开只读 GET 路径前缀（无需登录即可查看）
    PUBLIC_GET_PREFIXES = (
        "/api/v1/world",
        "/api/v1/characters",
        "/api/v1/actions",
        "/api/v1/town/scenes",
        "/api/v1/memories",
        "/api/v1/messages/history",
        "/api/v1/conversations",
        "/api/v1/admin/onebot/messages",
        "/api/v1/admin/proactive-shares",
        "/api/v1/admin/world/snapshots",
        "/api/v1/mcp/servers",
        "/api/v1/mcp/tools",
        "/api/v1/modules",
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan 直接透传
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # 豁免：非 /api/ 路径、登录接口
        if not path.startswith("/api/") or path == "/api/v1/auth/login":
            await self.app(scope, receive, send)
            return

        # 豁免：GET 只读公开端点（Dashboard 无需登录可查看）
        if method == "GET":
            for prefix in self.PUBLIC_GET_PREFIXES:
                if path.startswith(prefix):
                    await self.app(scope, receive, send)
                    return

        # 从 headers 中提取 Authorization
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        api_key_header = headers.get(b"x-api-key", b"").decode()

        # 验证 JWT 或 API Key
        from src.auth import decode_token
        from src.auth.middleware import _validate_api_key

        authenticated = False
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                decode_token(token)
                authenticated = True
            except Exception:
                pass
        elif api_key_header and _validate_api_key(api_key_header):
            authenticated = True

        if not authenticated:
            # 返回 401
            await _send_401(send)
            return

        await self.app(scope, receive, send)


async def _send_401(send: Send) -> None:
    body = b'{"detail":"Not authenticated"}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


app.add_middleware(AuthMiddleware)

# 注册 WebSocket 路由（/ws/chat/{character_id}）
app.include_router(ws_router)

# 注册 OneBot v12 反向 WebSocket 路由（/ws/onebot/v12）
app.include_router(onebot_adapter.router)

# Phase 4: 可观测性初始化（OTel Trace + Prometheus Metrics + Langfuse）
setup_tracing(app)
setup_metrics(app)
setup_langfuse()
logger.info("observability_initialized")


# === API 路由 ===


@app.get("/health")
async def health():
    """健康检查

    返回服务状态、World Tick ID、Redis 连接状态
    """
    return {
        "status": "ok",
        "world_tick": world_engine.tick_id if world_engine else 0,
        "redis": "connected" if redis else "disconnected",
        "character_engine": "available" if character_engine else "unavailable",
    }


@app.post("/api/v1/auth/login")
async def login(body: dict):
    """登录接口 - 账号密码换取 JWT Token

    请求体: {"username": "admin", "password": "admin123"}
    返回: {"token": "jwt_token", "user_id": "admin", "expires_in": 86400}
    """
    import secrets

    username = body.get("username", "")
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")

    # 校验账号密码
    if not secrets.compare_digest(username, settings.admin_username) or not secrets.compare_digest(password, settings.admin_password):
        logger.warning("auth_login_failed", username=username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # 生成 JWT Token
    token = create_token(username)
    logger.info("auth_login_success", user_id=username)

    return {
        "token": token,
        "user_id": username,
        "expires_in": settings.jwt_expire_hours * 3600,
    }


@app.get("/api/v1/characters")
async def list_characters(limit: int = 20, active_only: bool = False):
    """获取角色列表

    Args:
        limit: 返回数量限制（默认 20）
        active_only: 是否只返回活跃角色（默认 False）

    Returns:
        角色列表
    """
    async with db.session() as session:
        repo = CharacterRepository(session)
        if active_only:
            characters = await repo.get_active_characters()
        else:
            characters = await repo.list_all(limit)

    return {
        "data": [
            {
                "id": str(c.id),
                "name": c.name,
                "age": c.age,
                "occupation": c.occupation,
                "is_active": c.is_active,
            }
            for c in characters
        ],
        "total": len(characters),
    }


@app.get("/api/v1/characters/{character_id}")
async def get_character(character_id: str):
    """获取角色详情

    Args:
        character_id: 角色 UUID

    Returns:
        角色档案 + 实时状态
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        repo = CharacterRepository(session)
        result = await repo.get_character_with_state(cid)

    if not result:
        raise HTTPException(status_code=404, detail="Character not found")

    character, state = result

    return {
        "character": {
            "id": str(character.id),
            "name": character.name,
            "age": character.age,
            "occupation": character.occupation,
            "personality": character.traits.get("personality", []),
            "traits": character.traits,
            "backstory": character.backstory,
            "is_active": character.is_active,
        },
        "state": {
            "location": state.location,
            "stamina": state.stamina,
            "satiety": state.satiety,
            "mood": state.mood,
            "money": state.money,
            "phone_battery": state.phone_battery,
            "social_energy": state.social_energy,
            "version": state.version,
        },
    }


@app.get("/api/v1/world")
async def get_world_state():
    """获取世界状态

    Returns:
        世界当前状态（与前端 WorldState 接口对齐）
    """
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    import json

    state = await redis.hgetall("world:state")
    tick_id = int(state.get("tick_id", 0)) if state.get("tick_id") else 0
    world_time_raw = str(state.get("world_time", ""))
    # 兼容历史数据：world_time 可能被 JSON 序列化过两次
    try:
        world_time = json.loads(world_time_raw)
        if not isinstance(world_time, str):
            world_time = world_time_raw
    except (json.JSONDecodeError, TypeError):
        world_time = world_time_raw
    weather = str(state.get("weather", "sunny"))
    temperature = state.get("temperature")

    # 查询活跃角色数
    active_characters = 0
    try:
        async with db.session() as session:
            repo = CharacterRepository(session)
            active_characters = len(await repo.get_active_characters())
    except Exception as e:
        logger.warning("world_state_active_characters_failed", error=str(e))

    return {
        "tick_id": tick_id,
        "world_time": world_time,
        "weather": weather,
        "temperature": int(temperature) if temperature is not None else None,
        "active_characters": active_characters,
    }


@app.get("/api/v1/world/events/{tick_id}")
async def get_world_events(tick_id: int):
    """获取指定 Tick 的世界事件

    Args:
        tick_id: Tick ID

    Returns:
        该 Tick 的所有世界事件（差分记录）
    """
    async with db.session() as session:
        repo = WorldEventRepository(session)
        events = await repo.get_by_tick(tick_id)

    if not events:
        raise HTTPException(status_code=404, detail="No events found for this tick")

    return {
        "tick_id": tick_id,
        "events": [
            {
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


@app.get("/api/v1/actions")
async def list_actions():
    """获取所有 Action

    Returns:
        所有已注册的 Action 列表
    """
    if not registry:
        raise HTTPException(status_code=503, detail="Action registry not initialized")

    actions = registry.list_all()
    return {
        "data": [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "category": a.category.value if hasattr(a.category, "value") else str(a.category),
                "duration_minutes": a.duration_minutes,
                "energy_cost": a.energy_cost,
            }
            for a in actions
        ],
        "total": len(actions),
    }


@app.get("/api/v1/actions/{action_id}")
async def get_action(action_id: str):
    """获取单个 Action 详情

    Args:
        action_id: Action ID

    Returns:
        Action 详情
    """
    if not registry:
        raise HTTPException(status_code=503, detail="Action registry not initialized")

    action = registry.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    return {
        "id": action.id,
        "name": action.name,
        "description": action.description,
        "category": action.category.value if hasattr(action.category, "value") else str(action.category),
        "duration_minutes": action.duration_minutes,
        "energy_cost": action.energy_cost,
        "preconditions": getattr(action, "preconditions", {}),
        "effects": getattr(action, "effects", {}),
    }


@app.get("/api/v1/memories/{character_id}")
async def get_memories(character_id: str, limit: int = 20):
    """获取角色记忆

    Args:
        character_id: 角色 UUID
        limit: 返回数量限制（默认 20）

    Returns:
        角色最近的记忆片段
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        repo = MemoryRepository(session)
        episodes = await repo.recent(cid, limit)

    return {
        "data": [
            {
                "id": str(e.id),
                "content": e.content,
                "timestamp": e.timestamp.isoformat(),
                "importance": e.importance,
                "is_reflected": e.is_reflected,
            }
            for e in episodes
        ],
        "total": len(episodes),
    }


@app.get("/api/v1/characters/{character_id}/reflections")
async def get_reflections(character_id: str, limit: int = 10):
    """获取角色反思记录

    Args:
        character_id: 角色 UUID
        limit: 返回数量限制（默认 10）

    Returns:
        角色最近的反思记录（按创建时间倒序）
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        repo = ReflectionRepository(session)
        reflections = await repo.get_by_character(cid, limit)

    return {
        "data": [
            {
                "id": str(r.id),
                "content": r.content,
                "created_at": r.created_at.isoformat(),
            }
            for r in reflections
        ],
        "total": len(reflections),
    }


@app.get("/api/v1/characters/{character_id}/plans")
async def get_plans(character_id: str):
    """获取角色进行中的计划

    Args:
        character_id: 角色 UUID

    Returns:
        角色所有 active 状态的计划（按优先级降序）
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        repo = PlanRepository(session)
        plans = await repo.get_active_plans(cid)

    return {
        "data": [
            {
                "id": str(p.id),
                "type": p.type,
                "title": p.title,
                "description": p.description,
                "status": p.status,
                "priority": p.priority,
                "progress": p.progress,
                "deadline": p.deadline.isoformat() if p.deadline else None,
                "created_at": p.created_at.isoformat(),
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in plans
        ],
        "total": len(plans),
    }


@app.get("/api/v1/characters/{character_id}/actions")
async def get_action_history(character_id: str, limit: int = 50):
    """获取角色行为历史

    Args:
        character_id: 角色 UUID
        limit: 返回数量限制（默认 50）

    Returns:
        角色行为时间线（按时间倒序）
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        repo = ActionRepository(session)
        actions = await repo.get_by_character(cid, limit)

    return {
        "data": [
            {
                "id": str(a.id),
                "action_id": a.action_id,
                "action_name": a.action_name,
                "params": a.params,
                "reason": a.reason,
                "result": a.result,
                "duration_minutes": a.duration_minutes,
                "location": a.location,
                "related_characters": a.related_characters,
                "timestamp": a.timestamp.isoformat(),
            }
            for a in actions
        ],
        "total": len(actions),
    }


@app.post("/api/v1/admin/tick")
async def force_tick(character_id: str | None = None):
    """强制触发 Tick（管理接口）

    Args:
        character_id: 可选，指定角色 ID。如果为空则对所有活跃角色执行 Tick

    Returns:
        执行结果
    """
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
            raise HTTPException(status_code=400, detail="Invalid UUID format")

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


@app.post("/api/v1/admin/world/tick")
async def force_world_tick():
    """强制触发 World Tick（管理接口）

    Returns:
        执行结果
    """
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
        raise HTTPException(status_code=500, detail=f"World tick failed: {str(e)}")


@app.get("/api/v1/admin/status")
async def get_admin_status():
    """获取系统状态（管理接口）

    Returns:
        各组件运行状态
    """
    return {
        "redis": "connected" if redis else "disconnected",
        "world_engine": {
            "running": world_engine is not None,
            "tick_id": world_engine.tick_id if world_engine else 0,
            "is_leader": world_engine.is_leader if world_engine else False,
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


@app.post("/api/v1/admin/characters/import")
async def import_character_card(
    yaml_file: UploadFile | None = File(None),
    yaml: str | None = Body(None),
):
    """导入角色卡 YAML 文件

    支持两种方式：
    1. multipart 文件上传（yaml_file 字段）
    2. JSON body（yaml 字段，值为 YAML 字符串）

    Args:
        yaml_file: YAML 文件上传（可选）
        yaml: YAML 文本内容（可选，JSON body）

    Returns:
        创建的角色信息
    """
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    import yaml as yaml_lib

    # 优先从文件上传读取，否则从 body 的 yaml 字段读取
    if yaml_file is not None:
        content = await yaml_file.read()
        yaml_text = content.decode("utf-8")
    elif yaml is not None:
        yaml_text = yaml
    else:
        raise HTTPException(
            status_code=422,
            detail="请通过 multipart 上传 yaml_file 或在 JSON body 中提供 yaml 字段",
        )

    try:
        data = yaml_lib.safe_load(yaml_text)
    except yaml_lib.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败: {e}")

    async with db.session() as session:
        importer = CharacterImporter(session, redis)
        try:
            character = await importer.import_from_dict(data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"角色卡校验失败: {e}")

    return {
        "message": "角色导入成功",
        "character": {
            "id": str(character.id),
            "name": character.name,
            "age": character.age,
            "occupation": character.occupation,
        },
    }


@app.post("/api/v1/admin/characters/import-batch")
async def import_characters_batch(directory: str = "configs/characters"):
    """批量导入角色卡目录

    Args:
        directory: 角色卡目录路径（默认 configs/characters，相对于项目根目录）

    Returns:
        导入结果统计
    """
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    # 自动定位项目根目录：从 packages/backend 上两级到项目根
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[3]  # src/main.py -> backend -> packages -> aitown
    resolved = project_root / directory
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"目录不存在: {resolved}")

    async with db.session() as session:
        importer = CharacterImporter(session, redis)
        try:
            characters = await importer.import_directory(str(resolved))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {
        "message": f"批量导入完成: {len(characters)} 个角色",
        "characters": [
            {"id": str(c.id), "name": c.name} for c in characters
        ],
        "total": len(characters),
    }


# === Phase 2 API：小镇场景 ===


@app.get("/api/v1/town/scenes")
async def list_scenes():
    """获取所有场景列表"""
    if not scene_loader:
        raise HTTPException(status_code=503, detail="Scene loader not initialized")

    scenes = scene_loader.get_all_scenes()
    return {
        "data": [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type.value,
                "open_hours": s.open_hours,
                "capacity": s.capacity,
                "activities": s.activities,
                "workday_only": s.workday_only,
            }
            for s in scenes.values()
        ],
        "total": len(scenes),
    }


@app.get("/api/v1/town/scenes/{scene_id}")
async def get_scene_detail(scene_id: str):
    """获取场景详情（含实时状态）"""
    if not scene_loader:
        raise HTTPException(status_code=503, detail="Scene loader not initialized")

    scene = scene_loader.get_scene(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    crowdedness = await scene_loader.get_crowdedness(scene_id)
    present_chars = await scene_loader.get_present_characters(scene_id)

    return {
        "scene": {
            "id": scene.id,
            "name": scene.name,
            "type": scene.type.value,
            "open_hours": scene.open_hours,
            "capacity": scene.capacity,
            "activities": scene.activities,
            "workday_only": scene.workday_only,
        },
        "runtime": {
            "crowdedness": crowdedness,
            "present_characters": present_chars,
            "present_count": len(present_chars),
        },
    }


# === Phase 2 API：移动系统 ===


@app.post("/api/v1/characters/{character_id}/move")
async def move_character(character_id: str, to_scene: str, hour: int | None = None):
    """角色移动到指定场景

    Args:
        character_id: 角色 ID
        to_scene: 目标场景 ID
        hour: 当前小时（用于开放判断），默认从世界状态获取
    """
    if not movement_system or not redis:
        raise HTTPException(status_code=503, detail="Movement system not initialized")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    # 获取角色当前位置
    current_state = await redis.hgetall(f"char:{cid}:state")
    from_scene = str(current_state.get("location", "home"))

    # 获取当前小时（如果未提供）
    if hour is None:
        world_state = await redis.hgetall("world:state")
        world_time = str(world_state.get("time", "08:00"))
        try:
            hour = int(world_time.split(":")[0])
        except (ValueError, IndexError):
            hour = 8

    # 执行移动
    result = await movement_system.execute_move(
        str(cid), from_scene, to_scene, hour=hour
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.reason)

    # 更新角色位置
    await redis.hset(f"char:{cid}:state", "location", to_scene)

    return {
        "success": True,
        "from": from_scene,
        "to": to_scene,
        "duration_minutes": result.total_minutes,
        "path": result.path,
    }


# === Phase 2 API：作息系统 ===


@app.get("/api/v1/characters/{character_id}/schedule")
async def get_character_schedule(character_id: str, hour: int | None = None):
    """获取角色作息状态

    Args:
        character_id: 角色 ID
        hour: 查询的小时（默认当前小时）
    """
    if not schedule_system:
        raise HTTPException(status_code=503, detail="Schedule system not initialized")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    # 获取角色 traits
    async with db.session() as session:
        repo = CharacterRepository(session)
        char_data = await repo.get_by_id(cid)

    if not char_data:
        raise HTTPException(status_code=404, detail="Character not found")

    schedule_type = schedule_system.get_schedule_from_traits(char_data.traits or {})

    if hour is None:
        world_state = await redis.hgetall("world:state") if redis else {}
        world_time = str(world_state.get("time", "08:00"))
        try:
            hour = int(world_time.split(":")[0])
        except (ValueError, IndexError):
            hour = 8

    level = schedule_system.get_activity_level(schedule_type, hour)
    is_sleeping = schedule_system.is_sleeping(schedule_type, hour)
    regen_rate = schedule_system.get_stamina_regen_rate(schedule_type, hour)

    return {
        "character_id": character_id,
        "schedule_type": schedule_type,
        "hour": hour,
        "activity_level": level.value,
        "is_sleeping": is_sleeping,
        "stamina_regen_rate": regen_rate,
    }


# === Phase 2 API：角色关系 ===


@app.get("/api/v1/characters/{character_id}/relations")
async def get_character_relations(character_id: str):
    """获取角色的所有关系"""
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        graph = RelationGraph(session, redis)
        relations = await graph.get_all_relations(cid)

    return {
        "data": [
            {
                "target_id": str(r.target_id),
                "relationship_type": r.relationship_type,
                "strength": r.strength,
                "last_interaction_at": r.last_interaction_at.isoformat() if r.last_interaction_at else None,
                "notes": r.notes,
            }
            for r in relations
        ],
        "total": len(relations),
    }


@app.post("/api/v1/characters/{character_id}/relations/{target_id}/interact")
async def record_interaction(
    character_id: str,
    target_id: str,
    strength_delta: int = 0,
    notes: str | None = None,
):
    """记录角色间互动（更新关系）"""
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    try:
        cid = UUID(character_id)
        tid = UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    async with db.session() as session:
        graph = RelationGraph(session, redis)
        try:
            snap_a, snap_b = await graph.update_on_interaction(
                cid, tid, strength_delta, notes
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {
        "a_to_b": {
            "relationship_type": snap_a.relationship_type,
            "strength": snap_a.strength,
        },
        "b_to_a": {
            "relationship_type": snap_b.relationship_type,
            "strength": snap_b.strength,
        },
    }


# === Phase 2 API：动态耗时 ===


@app.get("/api/v1/duration/calculate")
async def calculate_duration(
    base_duration: int,
    weather: str = "sunny",
    is_outdoor: bool = True,
    crowdedness: float = 0.0,
    stamina: int = 100,
    mood: str = "calm",
):
    """计算动态耗时

    Args:
        base_duration: 基础耗时（分钟）
        weather: 天气
        is_outdoor: 是否户外
        crowdedness: 拥挤度 0-1
        stamina: 体力 0-100
        mood: 情绪
    """
    if not duration_calculator:
        raise HTTPException(status_code=503, detail="Duration calculator not initialized")

    modifiers = duration_calculator.compute_modifiers(
        weather, is_outdoor, crowdedness, stamina, mood
    )
    actual = duration_calculator.calculate_duration(
        base_duration, weather, is_outdoor, crowdedness, stamina, mood
    )

    return {
        "base_duration": base_duration,
        "actual_duration": actual,
        "modifiers": {
            "weather": modifiers.weather,
            "crowdedness": modifiers.crowdedness,
            "stamina": modifiers.stamina,
            "mood": modifiers.mood,
            "total": modifiers.total_multiplier(),
        },
    }


# === Phase 3 API：消息服务 ===


@app.post("/api/v1/messages/send")
async def send_message(
    character_id: str = Body(...),
    user_id: str = Body(...),
    platform: str = Body("web"),
    content: str = Body(""),
):
    """发送消息给角色并获取回复

    Args:
        character_id: 角色 UUID
        user_id: 用户标识
        platform: 来源平台（web/qq/lark/internal）
        content: 用户消息内容

    Returns:
        角色回复内容与元数据（token/cost/conversation_id）
    """
    if not llm or not prompts:
        raise HTTPException(status_code=503, detail="LLM client not initialized")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    if platform not in ("web", "qq", "lark", "internal"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform: {platform}",
        )

    async with db.session() as session:
        svc = MessageService(
            session=session,
            llm=llm,
            prompts=prompts,
        )
        try:
            result = await svc.handle_user_message(
                character_id=cid,
                user_id=user_id,
                platform=platform,
                content=content,
            )
        except Exception as e:
            logger.error(
                "message_handle_failed",
                character_id=character_id,
                user_id=user_id,
                error=str(e),
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Message handling failed: {str(e)}",
            )

    return {
        "data": {
            "conversation_id": str(result["conversation_id"]),
            "message_id": str(result["message_id"]) if result["message_id"] else None,
            "content": result["content"],
            "tokens": result["tokens"],
            "cost": result["cost"],
            "error": result["error"],
        }
    }


@app.get("/api/v1/messages/history")
async def get_message_history(
    conversation_id: str,
    limit: int = 50,
    before: str | None = None,
):
    """获取会话消息历史（支持游标分页）

    Args:
        conversation_id: 会话 UUID
        limit: 返回数量上限（默认 50）
        before: 游标时间（ISO 8601），仅返回该时间点之前的消息

    Returns:
        消息列表（按时间倒序）
    """
    try:
        conv_id = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    before_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid 'before' datetime format (use ISO 8601)",
            )

    async with db.session() as session:
        repo = MessageRepository(session)
        messages = await repo.list_by_conversation(
            conversation_id=conv_id,
            limit=limit,
            before=before_dt,
            order_desc=True,
        )

    return {
        "data": [
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "tokens": m.tokens,
                "cost": float(m.cost) if m.cost else None,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "total": len(messages),
        "has_more": len(messages) == limit,
    }


@app.get("/api/v1/conversations")
async def list_conversations(
    character_id: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
):
    """查询会话列表

    Args:
        character_id: 可选，按角色过滤
        user_id: 可选，按用户过滤
        limit: 返回数量上限

    Returns:
        会话列表（按 last_message_at 倒序）
    """
    async with db.session() as session:
        repo = ConversationRepository(session)

        if character_id and user_id:
            # 精确查询：单会话（仅查询不创建）
            try:
                cid = UUID(character_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format")
            conv = await repo.get_by_user_character(
                user_id=user_id,
                character_id=cid,
            )
            conversations = [conv] if conv else []
        elif character_id:
            try:
                cid = UUID(character_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format")
            conversations = await repo.list_by_character(cid, limit=limit)
        else:
            # 无过滤条件：返回所有会话（按 last_message_at 倒序）
            from sqlalchemy import select as _select

            from src.db.models import Conversation as _Conv

            stmt = (
                _select(_Conv)
                .order_by(_Conv.last_message_at.desc().nullslast())
                .limit(limit)
            )
            result = await session.execute(stmt)
            conversations = list(result.scalars())

    return {
        "data": [
            {
                "id": str(c.id),
                "character_id": str(c.character_id),
                "user_id": c.user_id,
                "platform": c.platform,
                "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in conversations
        ],
        "total": len(conversations),
    }


@app.get("/api/v1/messages/stats")
async def get_message_stats(
    character_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """获取消息统计（token/cost 累计，供成本监控）

    Args:
        character_id: 可选，按角色过滤
        start_date: 可选，起始日期（ISO 8601）
        end_date: 可选，结束日期（ISO 8601）

    Returns:
        累计消息数、token 数与 cost（USD），含按角色/按日期分组
    """
    from sqlalchemy import func as _func, select as _select

    from src.db.models import Character as _Char, Conversation as _Conv, Message as _Msg

    async with db.session() as session:
        # 总计
        base = _select(
            _func.count(_Msg.id).label("total_messages"),
            _func.coalesce(_func.sum(_Msg.tokens), 0).label("total_tokens"),
            _func.coalesce(_func.sum(_Msg.cost), 0).label("total_cost"),
        ).select_from(_Msg)
        if character_id:
            try:
                cid = UUID(character_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format")
            base = base.join(_Conv, _Msg.conversation_id == _Conv.id).where(
                _Conv.character_id == cid
            )

        result = await session.execute(base)
        row = result.one()
        total_messages = int(row.total_messages or 0)
        total_tokens = int(row.total_tokens or 0)
        total_cost = float(row.total_cost or 0)

        # 按角色分组
        by_char_stmt = (
            _select(
                _Char.name.label("name"),
                _func.count(_Msg.id).label("messages"),
                _func.coalesce(_func.sum(_Msg.tokens), 0).label("tokens"),
                _func.coalesce(_func.sum(_Msg.cost), 0).label("cost"),
            )
            .select_from(_Msg)
            .join(_Conv, _Msg.conversation_id == _Conv.id)
            .join(_Char, _Conv.character_id == _Char.id)
            .group_by(_Char.name)
        )
        try:
            char_result = await session.execute(by_char_stmt)
            by_character = {
                r.name: {
                    "messages": int(r.messages),
                    "tokens": int(r.tokens),
                    "cost": float(r.cost),
                }
                for r in char_result
            }
        except Exception:
            by_character = {}

        # 按日期分组
        by_day_stmt = (
            _select(
                _func.to_char(_Msg.created_at, "YYYY-MM-DD").label("date"),
                _func.count(_Msg.id).label("messages"),
                _func.coalesce(_func.sum(_Msg.tokens), 0).label("tokens"),
                _func.coalesce(_func.sum(_Msg.cost), 0).label("cost"),
            )
            .select_from(_Msg)
            .group_by("date")
            .order_by("date")
        )
        try:
            day_result = await session.execute(by_day_stmt)
            by_day = {
                r.date: {
                    "messages": int(r.messages),
                    "tokens": int(r.tokens),
                    "cost": float(r.cost),
                }
                for r in day_result
            }
        except Exception:
            by_day = {}

    return {
        "total_messages": total_messages,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "by_character": by_character,
        "by_day": by_day,
    }


# === Phase 3 API：模块与 MCP Server 管理 ===

# MCP Server 配置映射（环境变量 → 服务器元数据）
_MCP_SERVERS_CONFIG = [
    {
        "name": "code-executor",
        "env_key": "MCP_CODE_SERVER",
        "default_port": 8001,
        "type": "self-developed",
        "tools": ["execute_python", "list_allowed_modules"],
        "description": "Python 代码沙箱执行（subprocess 隔离 + 模块白名单）",
    },
    {
        "name": "web-search",
        "env_key": "MCP_SEARCH_SERVER",
        "default_port": 8002,
        "type": "community",
        "tools": ["search", "search_news"],
        "description": "网络搜索（Tavily API 集成）",
    },
    {
        "name": "weather",
        "env_key": "MCP_WEATHER_SERVER",
        "default_port": 8003,
        "type": "community",
        "tools": ["get_current_weather", "get_forecast", "get_weather_by_coords"],
        "description": "天气查询（OpenWeatherMap 集成）",
    },
    {
        "name": "shop-simulator",
        "env_key": "MCP_SHOP_SERVER",
        "default_port": 8004,
        "type": "self-developed",
        "tools": ["list_items", "get_item_details", "buy_item", "sell_item", "get_shop_categories"],
        "description": "商店模拟（小镇经济系统，24 件默认商品）",
    },
    {
        "name": "knowledge-base",
        "env_key": "MCP_KB_SERVER",
        "default_port": 8005,
        "type": "self-developed",
        "tools": ["query_kb", "list_categories"],
        "description": "小镇设定库查询（世界规则/角色/场景/行动/记忆系统）",
    },
    {
        "name": "character-social",
        "env_key": "MCP_SOCIAL_SERVER",
        "default_port": 8006,
        "type": "self-developed",
        "tools": ["give_gift", "invite_date", "resolve_conflict"],
        "description": "角色社交系统（送礼/约会/冲突解决）",
    },
]


@app.get("/api/v1/mcp/servers")
async def list_mcp_servers():
    """列出所有已配置的 MCP Server

    Returns:
        MCP Server 列表（含连接地址、工具清单、类型）
    """
    servers = []
    for cfg in _MCP_SERVERS_CONFIG:
        endpoint = getattr(settings, cfg["env_key"].lower(), None)
        if not endpoint:
            # 尝试从环境变量读取（settings 中可能未定义该字段）
            import os
            endpoint = os.environ.get(cfg["env_key"], f"http://localhost:{cfg['default_port']}")

        servers.append({
            "name": cfg["name"],
            "endpoint": endpoint,
            "type": cfg["type"],
            "description": cfg["description"],
            "tools": cfg["tools"],
            "tool_count": len(cfg["tools"]),
        })

    return {
        "data": servers,
        "total": len(servers),
    }


@app.get("/api/v1/mcp/servers/{server_name}")
async def get_mcp_server_detail(server_name: str):
    """获取单个 MCP Server 详情

    Args:
        server_name: Server 名称

    Returns:
        Server 详细信息（含工具清单）
    """
    cfg = next((c for c in _MCP_SERVERS_CONFIG if c["name"] == server_name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")

    import os
    endpoint = os.environ.get(cfg["env_key"], f"http://localhost:{cfg['default_port']}")

    return {
        "name": cfg["name"],
        "endpoint": endpoint,
        "type": cfg["type"],
        "description": cfg["description"],
        "tools": [
            {"name": tool_name, "server": cfg["name"]}
            for tool_name in cfg["tools"]
        ],
        "tool_count": len(cfg["tools"]),
    }


@app.get("/api/v1/mcp/tools")
async def list_all_mcp_tools():
    """列出所有 MCP Server 提供的工具

    Returns:
        所有可用工具的扁平列表（含所属 Server）
    """
    tools = []
    for cfg in _MCP_SERVERS_CONFIG:
        for tool_name in cfg["tools"]:
            tools.append({
                "name": tool_name,
                "server": cfg["name"],
                "server_type": cfg["type"],
            })

    return {
        "data": tools,
        "total": len(tools),
    }


@app.get("/api/v1/modules")
async def list_modules():
    """列出所有系统模块及其运行状态

    Returns:
        模块列表（含类型、状态、依赖）
    """
    modules = [
        {
            "name": "world_engine",
            "type": "core",
            "status": "running" if world_engine else "stopped",
            "description": "世界引擎（Tick 演化 + 事件系统）",
        },
        {
            "name": "character_tick",
            "type": "core",
            "status": "running" if character_engine else "stopped",
            "description": "角色 Tick 引擎（五阶段闭环）",
        },
        {
            "name": "action_registry",
            "type": "core",
            "status": "running" if registry else "stopped",
            "description": "Action 注册与执行系统",
        },
        {
            "name": "llm_client",
            "type": "core",
            "status": "running" if llm else "stopped",
            "description": "LLM 客户端（LangChain 集成）",
        },
        {
            "name": "embedding_worker",
            "type": "background",
            "status": "running" if embedding_worker and embedding_worker._running else "stopped",
            "description": "异步 Embedding 生成 Worker",
        },
        {
            "name": "partition_scheduler",
            "type": "background",
            "status": "running" if partition_scheduler and partition_scheduler.running else "stopped",
            "description": "数据库分区预创建调度器",
        },
        {
            "name": "rate_limiter",
            "type": "security",
            "status": "running" if rate_limiter else "stopped",
            "description": "API 速率限制器",
        },
        {
            "name": "scene_loader",
            "type": "phase2",
            "status": "running" if scene_loader else "stopped",
            "description": "小镇场景加载器",
        },
        {
            "name": "schedule_system",
            "type": "phase2",
            "status": "running" if schedule_system else "stopped",
            "description": "角色作息系统",
        },
        {
            "name": "movement_system",
            "type": "phase2",
            "status": "running" if movement_system else "stopped",
            "description": "移动系统（路径规划 + Dijkstra）",
        },
        {
            "name": "duration_calculator",
            "type": "phase2",
            "status": "running" if duration_calculator else "stopped",
            "description": "动态耗时计算器",
        },
    ]

    # 计算 MCP Server 模块状态
    for cfg in _MCP_SERVERS_CONFIG:
        modules.append({
            "name": cfg["name"],
            "type": "mcp",
            "status": "configured",
            "description": cfg["description"],
        })

    return {
        "data": modules,
        "total": len(modules),
    }


# =========================================================
# 扩展 API 端点（前端功能支持）
# =========================================================


@app.get("/api/v1/characters/{character_id}/state-history")
async def get_character_state_history(character_id: UUID, limit: int = 50):
    """获取角色状态历史记录（用于状态图表）

    Args:
        character_id: 角色 ID
        limit: 返回记录数（默认 50）

    Returns:
        状态历史列表（按时间正序，便于前端绘制曲线）
    """
    from sqlalchemy import desc, select

    from src.db.models import CharacterStateHistory

    async with db.session() as session:
        # 优先从 character_state_history 表查询（每次状态更新都会写入快照）
        stmt = (
            select(CharacterStateHistory)
            .where(CharacterStateHistory.character_id == character_id)
            .order_by(desc(CharacterStateHistory.recorded_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        history_records = list(result.scalars())

        if history_records:
            return {
                "data": [
                    {
                        "stamina": h.stamina,
                        "satiety": h.satiety,
                        "mood": h.mood,
                        "money": h.money,
                        "phone_battery": h.phone_battery,
                        "social_energy": h.social_energy,
                        "location": h.location,
                        "action_id": h.action_id,
                        "updated_at": h.recorded_at.isoformat() if h.recorded_at else None,
                    }
                    for h in reversed(history_records)
                ],
                "total": len(history_records),
                "source": "history",
            }

        # 回退：历史表暂无数据时返回当前状态（至少一个点）
        from src.db.models import CharacterState
        cur_stmt = (
            select(CharacterState)
            .where(CharacterState.character_id == character_id)
        )
        cur_result = await session.execute(cur_stmt)
        state = cur_result.scalar_one_or_none()

    if state is None:
        return {"data": [], "total": 0, "source": "empty"}

    return {
        "data": [
            {
                "stamina": state.stamina,
                "satiety": state.satiety,
                "mood": state.mood,
                "money": state.money,
                "phone_battery": state.phone_battery,
                "social_energy": state.social_energy,
                "location": state.location,
                "action_id": None,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }
        ],
        "total": 1,
        "source": "current",
    }


@app.get("/api/v1/characters/{character_id}/messages")
async def get_character_messages(character_id: UUID, limit: int = 50):
    """获取角色的所有消息历史（跨会话）

    Args:
        character_id: 角色 ID
        limit: 返回数量上限

    Returns:
        消息列表（按时间正序）
    """
    async with db.session() as session:
        conv_repo = ConversationRepository(session)
        msg_repo = MessageRepository(session)
        conversations = await conv_repo.list_by_character(character_id, limit=100)
        if not conversations:
            return {"data": [], "total": 0}
        all_messages = []
        for conv in conversations:
            msgs = await msg_repo.list_by_conversation(
                conversation_id=conv.id,
                limit=limit,
                order_desc=True,
            )
            all_messages.extend(msgs)
        # 按时间倒序排序后截断
        all_messages.sort(
            key=lambda m: m.created_at or datetime.min,
            reverse=True,
        )
        all_messages = all_messages[:limit]
        # 返回正序（旧到新）
        all_messages.reverse()
    return {
        "data": [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "sender": m.sender,
                "content": m.content,
                "timestamp": m.created_at.isoformat() if m.created_at else None,
            }
            for m in all_messages
        ],
        "total": len(all_messages),
    }


@app.get("/api/v1/world/events")
async def get_world_events_range(
    start_tick: int = 0,
    end_tick: int = 0,
    event_type: str | None = None,
    limit: int = 100,
):
    """查询 Tick 区间内的所有世界事件（用于事件时间线）

    Args:
        start_tick: 起始 Tick（默认 0）
        end_tick: 结束 Tick（默认 0 表示当前 tick_id）
        event_type: 事件类型过滤（可选）
        limit: 返回数量上限

    Returns:
        世界事件列表（按 tick_id, created_at 排序）
    """
    if end_tick == 0 and world_engine:
        end_tick = world_engine.tick_id

    from sqlalchemy import select

    from src.db.models import WorldEvent

    async with db.session() as session:
        stmt = (
            select(WorldEvent)
            .where(
                WorldEvent.tick_id >= start_tick,
                WorldEvent.tick_id <= end_tick,
            )
            .order_by(WorldEvent.tick_id, WorldEvent.created_at)
            .limit(limit)
        )
        if event_type:
            stmt = stmt.where(WorldEvent.event_type == event_type)

        result = await session.execute(stmt)
        events = list(result.scalars())

    return {
        "data": [
            {
                "id": str(e.id),
                "tick_id": e.tick_id,
                "event_type": e.event_type,
                "event_key": e.event_key,
                "payload": e.payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "total": len(events),
    }


@app.get("/api/v1/admin/onebot/messages")
async def get_onebot_messages(limit: int = 50):
    """获取 QQ 消息记录（用于 QQ 消息监控）

    查询 platform=qq 的会话中的最近消息，包含发送者和内容。

    Returns:
        消息列表（按时间倒序）
    """
    from sqlalchemy import desc, select

    from src.db.models import Conversation, Message

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


@app.get("/api/v1/admin/proactive-shares")
async def get_proactive_shares(limit: int = 50):
    """获取主动分享历史记录

    查询 sender=character 且 content 以特定标记开头的消息，
    或 source_type 包含 proactive 的记忆片段。

    Returns:
        分享记录列表
    """
    from sqlalchemy import desc, select

    from src.db.models import Message

    async with db.session() as session:
        # 查询角色主动发送的消息（sender=character）
        stmt = (
            select(Message)
            .where(Message.sender == "character")
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        messages = list(result.scalars())

    return {
        "data": [
            {
                "message_id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "sender": m.sender,
                "content": m.content,
                "tokens": m.tokens,
                "cost": m.cost,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "total": len(messages),
    }


@app.post("/api/v1/admin/vector-search")
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
    if not llm:
        raise HTTPException(503, "LLM client not initialized")

    if not query.strip():
        raise HTTPException(400, "Query text is required")

    try:
        # 生成查询向量
        query_embedding = await llm.embed(query)

        # 使用 MemoryRepository 进行向量检索
        from src.db.repositories import MemoryRepository

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
                    "id": str(ep.id),
                    "content": ep.content,
                    "importance": ep.importance,
                    "timestamp": ep.timestamp.isoformat() if ep.timestamp else None,
                    "similarity": float(ep.similarity) if hasattr(ep, "similarity") else 0.0,
                    "is_reflected": ep.is_reflected,
                    "source_type": ep.source_type,
                }
                for ep in results
            ],
            "total": len(results),
        }
    except Exception as e:
        raise HTTPException(500, f"Vector search failed: {e}")


@app.get("/api/v1/admin/world/snapshots")
async def get_world_snapshots(limit: int = 20):
    """获取世界快照列表（用于冷启动恢复管理）

    Returns:
        快照列表（按 tick_id 倒序）
    """
    from sqlalchemy import desc, select

    from src.db.models import WorldSnapshot

    async with db.session() as session:
        stmt = (
            select(WorldSnapshot)
            .order_by(desc(WorldSnapshot.tick_id))
            .limit(limit)
        )
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