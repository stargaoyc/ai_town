"""系统级 API 路由

包含：
- /health 健康检查（服务状态、模块运行状态、World Tick ID）
- /api/v1/auth/login 登录接口（账号密码换取 JWT Token）
- /api/v1/modules 系统模块列表与运行状态
- /api/v1/duration/calculate 动态耗时计算
"""

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from structlog import get_logger

from src.api.tools import _NAMESPACES as _TOOL_NAMESPACES
from src.auth import create_token
from src.config import settings
from src.runtime import (
    get_character_engine,
    get_duration_calculator,
    get_embedding_worker,
    get_llm,
    get_movement_system,
    get_onebot_adapter,
    get_partition_scheduler,
    get_rate_limiter,
    get_redis,
    get_registry,
    get_scene_loader,
    get_schedule_system,
    get_world_engine,
)
from src.security.rate_limit_dep import rate_limit

logger = get_logger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, Any]:
    """健康检查

    返回服务状态、各模块运行状态、World Tick ID。
    必须模块失败时 status 为 "degraded"。
    """
    redis = get_redis()
    world_engine = get_world_engine()
    llm = get_llm()
    registry = get_registry()
    character_engine = get_character_engine()
    embedding_worker = get_embedding_worker()
    partition_scheduler = get_partition_scheduler()
    onebot_adapter = get_onebot_adapter()

    # 实际检查 Redis 连接是否存活（而非仅检查对象是否存在）
    redis_alive = False
    if redis:
        try:
            await redis.ping()
            redis_alive = True
        except Exception:
            redis_alive = False

    must_modules = {
        "redis": redis_alive,
        "world_engine": world_engine is not None,
        "llm": llm is not None,
        "action_registry": registry is not None,
    }
    optional_modules = {
        "character_engine": character_engine is not None,
        "embedding_worker": embedding_worker is not None,
        "partition_scheduler": partition_scheduler is not None,
        "onebot_adapter": onebot_adapter._running if onebot_adapter else False,
    }
    all_must_ok = all(must_modules.values())

    return {
        "status": "ok" if all_must_ok else "degraded",
        "world_tick": world_engine.tick_id if world_engine else 0,
        "redis": "connected" if redis_alive else "disconnected",
        "must_modules": must_modules,
        "optional_modules": optional_modules,
        "current_world_time": _get_current_world_time(),
    }


def _get_current_world_time() -> dict[str, Any] | None:
    """读取当前世界时间（同步版本，用于 health 端点）"""
    # 这里返回 None，实际时间通过 /admin/status 异步获取
    return None


@router.post("/api/v1/auth/login", dependencies=[Depends(rate_limit("login", 5, 60, fail_closed=True))])
async def login(body: dict[str, Any]) -> dict[str, Any]:
    """登录接口 - 账号密码换取 JWT Token

    请求体: {"username": "admin", "password": "admin123"}
    返回: {"token": "jwt_token", "user_id": "admin", "expires_in": 86400}
    """
    username = body.get("username", "")
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")

    # 校验账号密码
    if not secrets.compare_digest(username, settings.admin_username) or not secrets.compare_digest(
        password, settings.admin_password
    ):
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


@router.get("/api/v1/modules")
async def list_modules() -> dict[str, Any]:
    """列出所有系统模块及其运行状态

    Returns:
        模块列表（含类型、状态、依赖）
    """
    world_engine = get_world_engine()
    character_engine = get_character_engine()
    registry = get_registry()
    llm = get_llm()
    embedding_worker = get_embedding_worker()
    partition_scheduler = get_partition_scheduler()
    rate_limiter = get_rate_limiter()
    scene_loader = get_scene_loader()
    schedule_system = get_schedule_system()
    movement_system = get_movement_system()
    duration_calculator = get_duration_calculator()

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

    # 计算工具命名空间模块状态（原 MCP Server，已内联到后端进程）
    for cfg in _TOOL_NAMESPACES:
        modules.append(
            {
                "name": f"tools.{cfg['name']}",
                "type": "tools",
                "status": "running",
                "description": cfg["description"],
            }
        )

    return {
        "data": modules,
        "total": len(modules),
    }


# === Phase 2 API：动态耗时 ===


@router.get("/api/v1/duration/calculate")
async def calculate_duration(
    base_duration: int,
    weather: str = "sunny",
    is_outdoor: bool = True,
    crowdedness: float = 0.0,
    stamina: int = 100,
    mood: str = "calm",
) -> dict[str, Any]:
    """计算动态耗时

    Args:
        base_duration: 基础耗时（分钟）
        weather: 天气
        is_outdoor: 是否户外
        crowdedness: 拥挤度 0-1
        stamina: 体力 0-100
        mood: 情绪
    """
    duration_calculator = get_duration_calculator()
    if not duration_calculator:
        raise HTTPException(status_code=503, detail="Duration calculator not initialized")

    modifiers = duration_calculator.compute_modifiers(weather, is_outdoor, crowdedness, stamina, mood)
    actual = duration_calculator.calculate_duration(base_duration, weather, is_outdoor, crowdedness, stamina, mood)

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
