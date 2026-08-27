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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from structlog import get_logger

from src import runtime
from src.actions import ActionRegistry, register_all
from src.adapters import OneBotAdapter
from src.api.actions import router as actions_router
from src.api.admin import router as admin_router
from src.api.characters import router as characters_router
from src.api.exceptions import register_exception_handlers
from src.api.memory import router as memory_ext_router
from src.api.messages import router as messages_router
from src.api.notifications import router as notifications_router
from src.api.system import router as system_router
from src.api.tools import router as tools_router
from src.api.town import router as town_router
from src.api.world import router as world_router
from src.auth.middleware import AuthMiddleware
from src.config import settings
from src.core import WorldEngine
from src.cost_control.budget_manager import set_budget_manager
from src.cost_control.circuit_breaker import set_circuit_breaker
from src.db.repositories import (
    CharacterRepository,
)
from src.llm import LLMClient, PromptTemplates
from src.memory.embedding_worker import EmbeddingWorker
from src.messaging import WebSocketManager
from src.messaging.proactive_sharing import run_tick_proactive_share
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
from src.paths import find_project_root
from src.scheduler import PartitionScheduler
from src.scheduler.loops import (
    character_tick_loop,
    daily_plan_loop,
    diary_scheduler_loop,
    hnsw_reindex_loop,
    memory_retention_loop,
    person_memory_compaction_loop,
    person_memory_heat_decay_loop,
    reconciliation_loop,
    redis_health_loop,
)
from src.security.rate_limiter import RateLimiter
from src.security.startup_checks import check_cors_origins, check_default_secrets

# 尝试导入 CharacterTickEngine（可能尚未创建）
try:
    from src.core.character import CharacterTickEngine

    CHARACTER_ENGINE_AVAILABLE = True
except ImportError:
    CharacterTickEngine = None  # type: ignore
    CHARACTER_ENGINE_AVAILABLE = False

logger = get_logger(__name__)

# WebSocket 连接管理器（单例）- 用于 Web 客户端实时聊天
ws_manager = WebSocketManager()

# OneBot v12 适配器（QQ 机器人接入）
onebot_adapter = OneBotAdapter()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理

    初始化顺序：
    1. Redis 连接
    2. LLM 客户端
    2.5 场景加载器（Phase 2 模块，供 Action scene_tags 注册期解析）
    3. Action Registry
    4. World Engine
    5. Character Tick Engine（如果可用）
    """
    # === 模块降级策略 ===
    # 必须模块（失败则中断启动）:
    #   - Redis（状态真相源）
    #   - LLM 客户端（核心能力）
    #   - 场景加载器（场景清单是 Action scene_tags 的真相源）
    #   - Action Registry（行为系统）
    #   - World Engine（世界推进）
    # 可选模块（失败则降级，继续启动）:
    #   - Embedding Worker（异步向量化，降级后记忆不生成向量）
    #   - Partition Scheduler（分区预创建，降级后需手动创建）
    #   - Character Tick Engine（角色推进，降级后世界仍运行）
    #   - Phase 2 模块（作息/移动/耗时计算，降级后角色行为受限）
    #   - OneBot 适配器（QQ 接入，降级后仅 Web 可用）

    logger.info("ai_town_backend_starting")

    # 安全检查（S-3）：默认弱凭据在生产模式（ENVIRONMENT=production）下 fail-fast，
    # 开发模式仅告警；CORS_ORIGINS 生产必填（P0-7）
    check_default_secrets()
    check_cors_origins()

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

    # 1.2 加载运行时配置覆盖（从 Redis 读取，Pydantic 校验后覆盖 settings 对象）
    # 使用 src.config_runtime 统一管理：类型校验 + 范围检查 + Redis 持久化
    try:
        from src.config_runtime import load_runtime_config

        config = await load_runtime_config(redis)
        logger.info(
            "runtime_config_loaded",
            keys=list(config.model_dump().keys()),
        )
    except Exception as e:
        logger.warning("runtime_config_load_failed", error=str(e))

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

    # 预算 gauge 只在启动时镜像一次配置值（告警规则以
    # ai_town_llm_daily_budget_usd 为分母计算预算消耗比）
    from src.observability.metrics import LLM_DAILY_BUDGET_USD

    LLM_DAILY_BUDGET_USD.set(settings.llm_daily_budget_usd)

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

    # 1.6 启动时从 PG 回灌 Redis 缺失的实时状态（P0-3）
    # Redis 重启/清空后恢复角色与世界状态，避免从零开始
    try:
        from src.core.rehydration import rehydrate_states

        await rehydrate_states(redis)
        logger.info("state_rehydration_completed")
    except Exception as e:
        logger.warning("state_rehydration_failed", error=str(e), exc_info=True)
        # 不中断启动，引擎可从空状态重建

    # 1.5 EMBEDDING_DIM 与物理列一致性校验（R4-H7）：错配即 fail-fast，
    # 否则潜伏到首次向量写入才以运行时报错暴露
    from src.db.session import db as _db
    from src.security.startup_checks import check_embedding_dim

    await check_embedding_dim(_db.session)

    # 2. 初始化 LLM 客户端
    try:
        llm = LLMClient()
        prompts = PromptTemplates()
        runtime.set_llm(llm)
        runtime.set_prompts(prompts)
        # 装配层注册主动分享处理器，core 层经 runtime 回调解耦对 messaging 的依赖
        runtime.set_proactive_share_handler(run_tick_proactive_share)
        logger.info("llm_initialized", model=settings.model_chat)
    except Exception as e:
        logger.error("llm_initialization_failed", error=str(e), exc_info=True)
        raise

    # 2.1 Embedding 实时维度探针（R6-L4）：MODEL_EMBEDDING 输出维度与 EMBEDDING_DIM
    # 一致性校验——静态 DDL 校验（check_embedding_dim）管不到「换模型输出维度漂移」，
    # 错配会在向量写入时逐行失败并熔断；开关见 EMBEDDING_PROBE_ENABLED
    from src.security.startup_checks import probe_embedding_dimension

    await probe_embedding_dimension(llm)

    # 2.5 初始化场景加载器（解析 configs/scenes.yaml）
    # 场景清单是 Action scene_tags 的真相源，须先于 Registry 加载：
    # 注册期把标签解析为具体场景集，标签未命中任何场景时 fail-fast
    scene_loader: SceneLoader | None = None
    try:
        scene_loader = SceneLoader(redis)
        # 经 find_project_root 兼容仓库/容器两种布局
        project_root = find_project_root()
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
        logger.error("scene_loader_init_failed", error=str(e), exc_info=True)

    # 3. 初始化 Action Registry
    try:
        registry = ActionRegistry(scene_loader=scene_loader)
        register_all(registry)
        runtime.set_registry(registry)
        logger.info("action_registry_initialized", count=len(registry.list_all()))
    except Exception as e:
        logger.error("action_registry_initialization_failed", error=str(e), exc_info=True)
        raise

    # 3.5 启动 Embedding Worker（异步向量化后台任务）
    embedding_task: asyncio.Task[None] | None = None
    try:
        embedding_worker = EmbeddingWorker(
            session_factory=db.session,
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
    character_tick_task: asyncio.Task[None] | None = None
    if CHARACTER_ENGINE_AVAILABLE and CharacterTickEngine is not None:
        try:
            character_engine = CharacterTickEngine(
                redis=redis,
                registry=registry,
                llm=llm,  # 修正参数名：llm_client → llm
                prompts=prompts,
            )
            # 启动后台任务：定期对所有活跃角色执行 Tick
            character_tick_task = asyncio.create_task(character_tick_loop())
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
    diary_scheduler_task: asyncio.Task[None] | None = None
    try:
        diary_scheduler_task = asyncio.create_task(diary_scheduler_loop())
        logger.info("diary_scheduler_started")
    except Exception as e:
        logger.error("diary_scheduler_start_failed", error=str(e), exc_info=True)

    # 5.6 启动每日计划生成器（round-7 F1b）
    daily_plan_task: asyncio.Task[None] | None = None
    try:
        daily_plan_task = asyncio.create_task(daily_plan_loop())
        logger.info("daily_plan_loop_started")
    except Exception as e:
        logger.error("daily_plan_loop_start_failed", error=str(e), exc_info=True)

    # 5.55 启动 Person Memory 热度衰减循环（后台任务）
    pm_heat_task: asyncio.Task[None] | None = None
    try:
        pm_heat_task = asyncio.create_task(person_memory_heat_decay_loop())
        logger.info("person_memory_heat_decay_started")
    except Exception as e:
        logger.error("person_memory_heat_decay_start_failed", error=str(e), exc_info=True)

    # 5.58 启动记忆生命周期治理循环（后台任务）
    retention_task: asyncio.Task[None] | None = None
    try:
        retention_task = asyncio.create_task(memory_retention_loop())
        logger.info("memory_retention_started")
    except Exception as e:
        logger.error("memory_retention_start_failed", error=str(e), exc_info=True)

    # 5.59 启动 Person Memory 主档压缩循环（后台任务）
    pm_compact_task: asyncio.Task[None] | None = None
    try:
        pm_compact_task = asyncio.create_task(person_memory_compaction_loop())
        logger.info("person_memory_compaction_started")
    except Exception as e:
        logger.error("person_memory_compaction_start_failed", error=str(e), exc_info=True)

    # 5.6 启动 Redis vs PG 状态对账循环（后台任务）
    reconcile_task: asyncio.Task[None] | None = None
    try:
        reconcile_task = asyncio.create_task(reconciliation_loop())
        logger.info("reconciliation_loop_started")
    except Exception as e:
        logger.error("reconciliation_loop_start_failed", error=str(e), exc_info=True)

    # 5.65 启动 Redis 周期探活循环（连接状态 gauge + Streams 队列深度）
    redis_health_task: asyncio.Task[None] | None = None
    try:
        redis_health_task = asyncio.create_task(redis_health_loop())
        logger.info("redis_health_loop_started")
    except Exception as e:
        logger.error("redis_health_loop_start_failed", error=str(e), exc_info=True)

    # 5.66 启动 HNSW 索引周期重建（P1-1：DELETE 后索引项不被 VACUUM 回收）
    hnsw_reindex_task: asyncio.Task[None] | None = None
    try:
        hnsw_reindex_task = asyncio.create_task(hnsw_reindex_loop())
        logger.info("hnsw_reindex_loop_started")
    except Exception as e:
        logger.error("hnsw_reindex_loop_start_failed", error=str(e), exc_info=True)

    # 5.6 启动时同步活跃角色数指标（避免重启后指标面板显示 0）
    try:
        from src.db.session import db as _db

        async with _db.session() as session:
            repo = CharacterRepository(session)
            active_chars = await repo.get_active_characters()
        from src.observability.metrics import ACTIVE_CHARACTERS

        ACTIVE_CHARACTERS.set(len(active_chars))
        logger.info("active_characters_metric_set", count=len(active_chars))
    except Exception as e:
        logger.warning("active_characters_metric_set_failed", error=str(e))

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

    # 统一取消散落的 fire-and-forget 任务（P-2：注册表持有强引用，
    # lifespan 在 yield 前抛异常时也能被本段清理）
    from src.core.background import shutdown_background_tasks

    await shutdown_background_tasks()

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

    # 取消每日计划生成器（round-7 F1b）
    if daily_plan_task:
        daily_plan_task.cancel()
        try:
            await daily_plan_task
        except asyncio.CancelledError:
            pass

    # 取消记忆生命周期治理循环
    if retention_task:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass

    # 取消 Person Memory 热度衰减循环
    if pm_heat_task:
        pm_heat_task.cancel()
        try:
            await pm_heat_task
        except asyncio.CancelledError:
            pass

    # 取消对账循环
    if reconcile_task:
        reconcile_task.cancel()
        try:
            await reconcile_task
        except asyncio.CancelledError:
            pass

    # 取消 Redis 探活循环
    if redis_health_task:
        redis_health_task.cancel()
        try:
            await redis_health_task
        except asyncio.CancelledError:
            pass

    # 取消 HNSW 重建循环
    if hnsw_reindex_task:
        hnsw_reindex_task.cancel()
        try:
            await hnsw_reindex_task
        except asyncio.CancelledError:
            pass

    # 取消 Person Memory 主档压缩循环
    if pm_compact_task:
        pm_compact_task.cancel()
        try:
            await pm_compact_task
        except asyncio.CancelledError:
            pass

    # 停止 World Engine
    if world_engine:
        await world_engine.stop()
        logger.info("world_engine_stopped")

    # 关闭 LLM HTTP 客户端（close 存在但此前未接入 shutdown，连接靠进程退出回收）
    llm_client = runtime.get_llm()
    if llm_client is not None:
        await llm_client.close()
        logger.info("llm_client_closed")

    # 释放数据库连接池（进程退出兜底之外的显式回收）
    await db.engine.dispose()
    logger.info("db_engine_disposed")

    # 关闭 Redis 连接
    if redis:
        await redis.close()
        logger.info("redis_connection_closed")


# === FastAPI 应用实例 ===
app = FastAPI(
    title="AI Town Backend",
    description="二次元 AI 小镇陪伴智能体 - World Engine + LLM",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 中间件（S-2）：allow_credentials=True 与通配符 * 组合在浏览器规范下无效，
# 等于放弃跨域防护。来源必须显式配置；未配置时仅放行同源（空列表）。
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    logger.warning("cors_origins_not_configured", hint="设置 CORS_ORIGINS 为前端域名列表以启用跨域")


app.add_middleware(AuthMiddleware)

# 注册 WebSocket 路由（/ws/chat/{character_id}）
app.include_router(ws_router)

# 注册 OneBot v12 反向 WebSocket 路由（/ws/onebot/v12）
app.include_router(onebot_adapter.router)

# 注册通知中心 API 路由（/api/v1/notifications）
app.include_router(notifications_router)

# 注册工具管理 API 路由（/api/v1/tools）
app.include_router(tools_router)

# 注册记忆扩展 API 路由（日记 + 角色对用户的记忆）
app.include_router(memory_ext_router)

# Phase 4: 可观测性初始化（OTel Trace + Prometheus Metrics + Langfuse）
setup_tracing(app)
setup_metrics(app)
setup_langfuse()
logger.info("observability_initialized")

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
