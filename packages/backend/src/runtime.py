"""运行时依赖容器

集中持有所有运行时实例，消除业务模块对 main.py 的反向依赖。
main.py 的 lifespan 初始化后通过 set_* 方法写入，其他模块通过 get_* 方法读取。

使用方式：
    from src.runtime import get_redis, get_llm, get_prompts
    redis = get_redis()  # 返回 Redis | None
    llm = get_llm()      # 返回 LLMClient | None
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from src.actions import ActionRegistry
    from src.adapters import OneBotAdapter
    from src.core import WorldEngine
    from src.llm import LLMClient, PromptTemplates
    from src.memory.embedding_worker import EmbeddingWorker
    from src.messaging import WebSocketManager
    from src.modules import (
        DurationCalculator,
        MovementSystem,
        SceneLoader,
        ScheduleSystem,
    )
    from src.scheduler import PartitionScheduler
    from src.security.rate_limiter import RateLimiter

# 运行时实例（初始化为 None，由 main.py lifespan 设置）
_redis: "Redis | None" = None
_world_engine: "WorldEngine | None" = None
_character_engine = None  # CharacterTickEngine，类型可选
_registry: "ActionRegistry | None" = None
_llm: "LLMClient | None" = None
_prompts: "PromptTemplates | None" = None
_embedding_worker: "EmbeddingWorker | None" = None
_partition_scheduler: "PartitionScheduler | None" = None
_rate_limiter: "RateLimiter | None" = None
_ws_manager: "WebSocketManager | None" = None
_onebot_adapter: "OneBotAdapter | None" = None
_scene_loader: "SceneLoader | None" = None
_schedule_system: "ScheduleSystem | None" = None
_duration_calculator: "DurationCalculator | None" = None
_movement_system: "MovementSystem | None" = None

# 后端端口（由 main.py 设置）
_backend_port: int = 8001


# === Setter 方法（仅 main.py lifespan 调用）===


def set_redis(value) -> None:
    global _redis
    _redis = value


def set_world_engine(value) -> None:
    global _world_engine
    _world_engine = value


def set_character_engine(value) -> None:
    global _character_engine
    _character_engine = value


def set_registry(value) -> None:
    global _registry
    _registry = value


def set_llm(value) -> None:
    global _llm
    _llm = value


def set_prompts(value) -> None:
    global _prompts
    _prompts = value


def set_embedding_worker(value) -> None:
    global _embedding_worker
    _embedding_worker = value


def set_partition_scheduler(value) -> None:
    global _partition_scheduler
    _partition_scheduler = value


def set_rate_limiter(value) -> None:
    global _rate_limiter
    _rate_limiter = value


def set_ws_manager(value) -> None:
    global _ws_manager
    _ws_manager = value


def set_onebot_adapter(value) -> None:
    global _onebot_adapter
    _onebot_adapter = value


def set_scene_loader(value) -> None:
    global _scene_loader
    _scene_loader = value


def set_schedule_system(value) -> None:
    global _schedule_system
    _schedule_system = value


def set_duration_calculator(value) -> None:
    global _duration_calculator
    _duration_calculator = value


def set_movement_system(value) -> None:
    global _movement_system
    _movement_system = value


def set_backend_port(port: int) -> None:
    global _backend_port
    _backend_port = port


# === Getter 方法（业务模块调用）===


def get_redis():
    """获取 Redis 客户端实例"""
    return _redis


def get_world_engine():
    """获取世界引擎实例"""
    return _world_engine


def get_character_engine():
    """获取角色 Tick 引擎实例"""
    return _character_engine


def get_registry():
    """获取 Action Registry 实例"""
    return _registry


def get_llm():
    """获取 LLM 客户端实例"""
    return _llm


def get_prompts():
    """获取 Prompt 模板实例"""
    return _prompts


def get_embedding_worker():
    """获取 Embedding Worker 实例"""
    return _embedding_worker


def get_partition_scheduler():
    """获取分区调度器实例"""
    return _partition_scheduler


def get_rate_limiter():
    """获取速率限制器实例"""
    return _rate_limiter


def get_ws_manager():
    """获取 WebSocket 管理器实例"""
    return _ws_manager


def get_onebot_adapter():
    """获取 OneBot 适配器实例"""
    return _onebot_adapter


def get_scene_loader():
    """获取场景加载器实例"""
    return _scene_loader


def get_schedule_system():
    """获取作息系统实例"""
    return _schedule_system


def get_duration_calculator():
    """获取动态耗时计算器实例"""
    return _duration_calculator


def get_movement_system():
    """获取移动系统实例"""
    return _movement_system


def get_backend_port() -> int:
    """获取后端运行端口"""
    return _backend_port


# === 业务工具函数（依赖运行时单例）===


def _notif_key(user_id: str) -> str:
    """Redis 通知列表键"""
    return f"notifications:{user_id}"


async def create_notification(
    user_id: str,
    notif_type: str,
    title: str,
    content: str,
) -> dict:
    """创建通知并写入 Redis

    使用 runtime 持有的 Redis 客户端，消除业务模块对 main.py 的反向依赖。
    """
    import json
    from datetime import UTC, datetime

    from uuid6 import uuid7

    redis = get_redis()
    if redis is None:
        raise RuntimeError("Redis not initialized")

    notif = {
        "id": str(uuid7()),
        "type": notif_type,
        "title": title,
        "content": content,
        "created_at": datetime.now(UTC).isoformat(),
        "read": False,
    }
    await redis.lpush(_notif_key(user_id), json.dumps(notif))
    # 保留最近 200 条
    await redis.ltrim(_notif_key(user_id), 0, 199)
    return notif
