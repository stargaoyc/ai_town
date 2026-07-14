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
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from structlog import get_logger

from src.actions import ActionRegistry, register_all
from src.adapters import OneBotAdapter
from src.api.actions import router as actions_router
from src.api.admin import router as admin_router
from src.api.characters import router as characters_router
from src.api.mcp import router as mcp_router
from src.api.memory import router as memory_ext_router
from src.api.messages import router as messages_router
from src.api.notifications import router as notifications_router
from src.api.system import router as system_router
from src.api.town import router as town_router
from src.api.world import router as world_router
from src.config import settings
from src.core import WorldEngine
from src.cost_control.budget_manager import set_budget_manager
from src.cost_control.circuit_breaker import set_circuit_breaker
from src.db.repositories import (
    CharacterRepository,
)
from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.memory.diary_service import DiaryService
from src.memory.embedding_worker import EmbeddingWorker
from src.messaging import WebSocketManager
from src.messaging.websocket import router as ws_router
from src.modules import (
    DurationCalculator,
    MovementSystem,
    SceneLoader,
    ScheduleSystem,
)
from src.observability import (
    setup_langfuse,
    setup_logging,
    setup_metrics,
    setup_tracing,
)
from src.observability.sanitizer import sanitize_url
from src.scheduler import PartitionScheduler
from src.security.rate_limiter import RateLimiter

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

from src import runtime


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
    # === 模块降级策略 ===
    # 必须模块（失败则中断启动）:
    #   - Redis（状态真相源）
    #   - LLM 客户端（核心能力）
    #   - Action Registry（行为系统）
    #   - World Engine（世界推进）
    # 可选模块（失败则降级，继续启动）:
    #   - Embedding Worker（异步向量化，降级后记忆不生成向量）
    #   - Partition Scheduler（分区预创建，降级后需手动创建）
    #   - Character Tick Engine（角色推进，降级后世界仍运行）
    #   - Phase 2 模块（场景/作息/移动，降级后角色行为受限）
    #   - OneBot 适配器（QQ 接入，降级后仅 Web 可用）
    global redis, world_engine, character_engine, registry, llm, prompts
    global scene_loader, schedule_system, duration_calculator, movement_system
    global embedding_worker, partition_scheduler, rate_limiter

    logger.info("ai_town_backend_starting")

    # 安全检查：默认密码警告
    if settings.admin_password == "admin123":
        logger.warning(
            "insecure_default_password",
            message="ADMIN_PASSWORD 仍为默认值 'admin123'，请在 .env 中修改为强密码",
        )

    # 同步全局实例到 runtime 容器
    runtime.set_ws_manager(ws_manager)
    runtime.set_onebot_adapter(onebot_adapter)

    # 0.5 初始化可观测性（日志/Trace/Metrics/Langfuse）
    setup_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger.info("logging_configured", format=settings.log_format, level=settings.log_level)

    # 1. 初始化 Redis
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        runtime.set_redis(redis)
        # 测试连接
        await redis.ping()
        logger.info("redis_connected", url=sanitize_url(settings.redis_url))
        # 设置 Prometheus Redis 连接状态指标
        from src.observability.metrics import REDIS_CONNECTED

        REDIS_CONNECTED.set(1)
    except Exception as e:
        logger.error("redis_connection_failed", error=str(e), exc_info=True)
        from src.observability.metrics import REDIS_CONNECTED

        REDIS_CONNECTED.set(0)
        raise

    # 1.2 加载运行时配置覆盖（从 Redis 读取，覆盖 settings 对象）
    try:
        import json

        raw_overrides = await redis.get("config:overrides")
        if raw_overrides:
            overrides = json.loads(raw_overrides)  # type: ignore[arg-type]
            applied = 0
            for key, value in overrides.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
                    applied += 1
            if applied > 0:
                logger.info("runtime_config_overrides_loaded", count=applied, keys=list(overrides.keys()))
    except Exception as e:
        logger.warning("runtime_config_override_load_failed", error=str(e))

    # 1.1 初始化成本控制 + 速率限制器（依赖 Redis）
    set_budget_manager(redis, daily_budget_usd=settings.llm_daily_budget_usd)
    set_circuit_breaker(
        redis,
        failure_threshold=settings.llm_circuit_breaker_threshold,
        recovery_timeout=settings.llm_circuit_breaker_recovery_timeout,
    )
    rate_limiter = RateLimiter(redis)
    runtime.set_rate_limiter(rate_limiter)
    logger.info(
        "cost_control_initialized",
        daily_budget=settings.llm_daily_budget_usd,
        circuit_threshold=settings.llm_circuit_breaker_threshold,
    )

    # 1.5 预创建分区（确保月初写入不报错）
    try:
        from sqlalchemy import text

        from src.db.session import db

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
        runtime.set_llm(llm)
        runtime.set_prompts(prompts)
        logger.info("llm_initialized", model=settings.model_chat)
    except Exception as e:
        logger.error("llm_initialization_failed", error=str(e), exc_info=True)
        raise

    # 3. 初始化 Action Registry
    try:
        registry = ActionRegistry()
        register_all(registry)
        runtime.set_registry(registry)
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
        runtime.set_embedding_worker(embedding_worker)
        logger.info("embedding_worker_started", batch_size=20, poll_interval=5.0)
    except Exception as e:
        logger.error("embedding_worker_start_failed", error=str(e), exc_info=True)
        embedding_worker = None
        runtime.set_embedding_worker(embedding_worker)

    # 3.6 启动分区预创建调度器（每月 25 号 03:00 自动执行）
    # 解决 v8 P1 #68：原仅启动时执行，长期运行 >3 月漏建分区
    try:
        partition_scheduler = PartitionScheduler()
        await partition_scheduler.start()
        runtime.set_partition_scheduler(partition_scheduler)
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
        runtime.set_world_engine(world_engine)
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
            runtime.set_character_engine(character_engine)
            logger.info("character_engine_started")
        except Exception as e:
            logger.error(
                "character_engine_start_failed",
                error=str(e),
                exc_info=True,
            )
            character_engine = None
            runtime.set_character_engine(character_engine)
    else:
        logger.warning(
            "character_tick_engine_not_available",
            message="CharacterTickEngine module not found, character tick loop disabled",
        )

    # 5.5 启动日记自动生成调度器（后台任务）
    diary_scheduler_task: asyncio.Task | None = None
    try:
        diary_scheduler_task = asyncio.create_task(_diary_scheduler_loop())
        logger.info("diary_scheduler_started")
    except Exception as e:
        logger.error("diary_scheduler_start_failed", error=str(e), exc_info=True)

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
        runtime.set_scene_loader(scene_loader)
        runtime.set_schedule_system(schedule_system)
        runtime.set_duration_calculator(duration_calculator)
        runtime.set_movement_system(movement_system)
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

    # 取消日记自动生成调度器
    if diary_scheduler_task:
        diary_scheduler_task.cancel()
        try:
            await diary_scheduler_task
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
    max_backoff = 10  # 最大退避倍数

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


async def _diary_scheduler_loop() -> None:
    """日记自动生成后台循环

    每 1800 秒（30 分钟现实时间）检查一次世界时间，根据时段决定生成哪种周期的日记：
    - 每日：世界时间 22:00-06:00（一天结束时）
    - 每周：每 7 个世界日
    - 每月：每 30 个世界日
    - 每年：每 365 个世界日

    生成是幂等的：DiaryService 会跳过当前周期已存在日记的角色。
    循环内部捕获所有异常，保证不会崩溃退出。
    """
    interval = 1800
    logger.info("diary_scheduler_loop_started", interval=interval)

    while True:
        try:
            await asyncio.sleep(interval)

            if not redis:
                continue

            # 读取世界状态（world:state 主哈希中的 world_time 字段为 ISO 格式时间）
            world_state = await redis.hgetall("world:state")
            if not world_state:
                continue

            world_time_raw = str(world_state.get("world_time", ""))
            if not world_time_raw:
                continue

            # 兼容 world_time 被 JSON 双重序列化的情况
            try:
                parsed = json.loads(world_time_raw)
                if isinstance(parsed, str):
                    world_time_raw = parsed
            except (json.JSONDecodeError, TypeError):
                pass

            try:
                world_time = datetime.fromisoformat(world_time_raw)
            except ValueError:
                logger.warning("diary_scheduler_invalid_world_time", raw=world_time_raw)
                continue

            hour = world_time.hour
            day_of_year = world_time.timetuple().tm_yday

            # 根据世界时间确定需要生成的周期
            periods_to_generate: list[str] = []
            if hour >= 22 or hour < 6:
                periods_to_generate.append("day")
            if day_of_year % 7 == 0:
                periods_to_generate.append("week")
            if day_of_year % 30 == 0:
                periods_to_generate.append("month")
            if day_of_year % 365 == 0:
                periods_to_generate.append("year")

            if not periods_to_generate:
                continue

            logger.info(
                "diary_scheduler_trigger",
                periods=periods_to_generate,
                world_hour=hour,
                world_day_of_year=day_of_year,
            )

            service = DiaryService(session_factory=db.session)
            for period in periods_to_generate:
                try:
                    summary = await service.generate_diaries_for_all_characters(period)
                    logger.info("diary_scheduler_period_done", period=period, summary=summary)
                except Exception as e:
                    logger.error(
                        "diary_scheduler_period_failed",
                        period=period,
                        error=str(e),
                        exc_info=True,
                    )

        except asyncio.CancelledError:
            logger.info("diary_scheduler_loop_cancelled")
            raise
        except Exception as e:
            logger.error("diary_scheduler_loop_error", error=str(e), exc_info=True)
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
        "/api/v1/admin/status",
        "/api/v1/admin/metrics-detail",
        "/api/v1/admin/logs",
        "/api/v1/admin/config",
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
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


app.add_middleware(AuthMiddleware)

# 注册 WebSocket 路由（/ws/chat/{character_id}）
app.include_router(ws_router)

# 注册 OneBot v12 反向 WebSocket 路由（/ws/onebot/v12）
app.include_router(onebot_adapter.router)

# 注册通知中心 API 路由（/api/v1/notifications）
app.include_router(notifications_router)

# 注册 MCP Server 管理 API 路由（/api/v1/mcp）
app.include_router(mcp_router)

# 注册记忆扩展 API 路由（日记 + 角色对用户的记忆）
app.include_router(memory_ext_router)

# Phase 4: 可观测性初始化（OTel Trace + Prometheus Metrics + Langfuse）
setup_tracing(app)
setup_metrics(app)
setup_langfuse()
logger.info("observability_initialized")

# 注册全局异常处理器
from src.api.exceptions import register_exception_handlers

register_exception_handlers(app)
logger.info("exception_handlers_registered")

# === 注册 API 路由（从 src/api/ 模块加载） ===

# 系统路由（health, auth/login, modules, duration/calculate）
app.include_router(system_router)

# 角色路由（列表/详情/反思/计划/行为/移动/作息/关系/状态历史/消息）
app.include_router(characters_router)

# 世界状态路由（/world, /world/events）
app.include_router(world_router)

# Action 列表路由（/actions）
app.include_router(actions_router)

# 小镇场景路由（/town/scenes）
app.include_router(town_router)

# 消息服务路由（/messages, /conversations）
app.include_router(messages_router)

# 管理接口路由（/admin/*）
app.include_router(admin_router)
