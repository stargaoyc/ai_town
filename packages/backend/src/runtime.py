"""运行时依赖容器

集中持有所有运行时实例，消除业务模块对 main.py 的反向依赖。
main.py 的 lifespan 初始化后通过 set_* 方法写入，其他模块通过 get_* 方法读取。

使用方式：
    from src.runtime import get_redis, get_llm, get_prompts
    redis = get_redis()  # 返回 Redis | None
    llm = get_llm()      # 返回 LLMClient | None
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from redis.asyncio import Redis

    from src.actions import ActionRegistry
    from src.adapters import OneBotAdapter
    from src.core import WorldEngine
    from src.core.character.tick import CharacterTickEngine
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
_character_engine: "CharacterTickEngine | None" = None
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

# 主动分享处理器（由 main.py 装配层注册，core 层经此解耦对 messaging 的直接依赖）
_proactive_share_handler: "Callable[[UUID], Awaitable[None]] | None" = None


# === Setter 方法（仅 main.py lifespan 调用）===


def set_redis(value: "Redis | None") -> None:
    global _redis
    _redis = value


def set_world_engine(value: "WorldEngine | None") -> None:
    global _world_engine
    _world_engine = value


def set_character_engine(value: "CharacterTickEngine | None") -> None:
    global _character_engine
    _character_engine = value


def set_registry(value: "ActionRegistry | None") -> None:
    global _registry
    _registry = value


def set_llm(value: "LLMClient | None") -> None:
    global _llm
    _llm = value


def set_prompts(value: "PromptTemplates | None") -> None:
    global _prompts
    _prompts = value


def set_embedding_worker(value: "EmbeddingWorker | None") -> None:
    global _embedding_worker
    _embedding_worker = value


def set_partition_scheduler(value: "PartitionScheduler | None") -> None:
    global _partition_scheduler
    _partition_scheduler = value


def set_rate_limiter(value: "RateLimiter | None") -> None:
    global _rate_limiter
    _rate_limiter = value


def set_ws_manager(value: "WebSocketManager | None") -> None:
    global _ws_manager
    _ws_manager = value


def set_onebot_adapter(value: "OneBotAdapter | None") -> None:
    global _onebot_adapter
    _onebot_adapter = value


def set_scene_loader(value: "SceneLoader | None") -> None:
    global _scene_loader
    _scene_loader = value


def set_schedule_system(value: "ScheduleSystem | None") -> None:
    global _schedule_system
    _schedule_system = value


def set_duration_calculator(value: "DurationCalculator | None") -> None:
    global _duration_calculator
    _duration_calculator = value


def set_movement_system(value: "MovementSystem | None") -> None:
    global _movement_system
    _movement_system = value


def set_proactive_share_handler(handler: "Callable[[UUID], Awaitable[None]] | None") -> None:
    global _proactive_share_handler
    _proactive_share_handler = handler


def set_backend_port(port: int) -> None:
    global _backend_port
    _backend_port = port


# === Getter 方法（业务模块调用）===


def get_redis() -> "Redis | None":
    """获取 Redis 客户端实例"""
    return _redis


def get_world_engine() -> "WorldEngine | None":
    """获取世界引擎实例"""
    return _world_engine


def get_character_engine() -> "CharacterTickEngine | None":
    """获取角色 Tick 引擎实例"""
    return _character_engine


def get_registry() -> "ActionRegistry | None":
    """获取 Action Registry 实例"""
    return _registry


def get_llm() -> "LLMClient | None":
    """获取 LLM 客户端实例"""
    return _llm


def get_prompts() -> "PromptTemplates | None":
    """获取 Prompt 模板实例"""
    return _prompts


def get_embedding_worker() -> "EmbeddingWorker | None":
    """获取 Embedding Worker 实例"""
    return _embedding_worker


def get_partition_scheduler() -> "PartitionScheduler | None":
    """获取分区调度器实例"""
    return _partition_scheduler


def get_rate_limiter() -> "RateLimiter | None":
    """获取速率限制器实例"""
    return _rate_limiter


def get_ws_manager() -> "WebSocketManager | None":
    """获取 WebSocket 管理器实例"""
    return _ws_manager


def get_onebot_adapter() -> "OneBotAdapter | None":
    """获取 OneBot 适配器实例"""
    return _onebot_adapter


def get_scene_loader() -> "SceneLoader | None":
    """获取场景加载器实例"""
    return _scene_loader


def get_schedule_system() -> "ScheduleSystem | None":
    """获取作息系统实例"""
    return _schedule_system


def get_duration_calculator() -> "DurationCalculator | None":
    """获取动态耗时计算器实例"""
    return _duration_calculator


def get_movement_system() -> "MovementSystem | None":
    """获取移动系统实例"""
    return _movement_system


def get_proactive_share_handler() -> "Callable[[UUID], Awaitable[None]] | None":
    """获取主动分享处理器（未注册时返回 None，调用方静默跳过）"""
    return _proactive_share_handler


def get_backend_port() -> int:
    """获取后端运行端口"""
    return _backend_port


# === 业务工具函数（依赖运行时单例）===


def notification_key(user_id: str) -> str:
    """Redis 通知列表键"""
    return f"notifications:{user_id}"


async def create_notification(
    user_id: str,
    notif_type: str,
    title: str,
    content: str,
) -> dict[str, Any]:
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
    await redis.lpush(notification_key(user_id), json.dumps(notif))
    # 保留最近 200 条
    await redis.ltrim(notification_key(user_id), 0, 199)
    return notif
