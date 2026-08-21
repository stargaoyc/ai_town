"""世界演化器基类

定义所有演化器（Evolution）的统一契约。World Engine 在每个 Tick 依次调用
注册的演化器，读取其返回的字段并合并回 `world:state` 主哈希。
"""

import json
from abc import ABC, abstractmethod
from typing import Any

from redis.asyncio import Redis


class WorldEvolution(ABC):
    """世界演化器基类

    每个 Evolution 负责世界状态的某个维度的推进。
    World Engine 在每个 Tick 调用所有 Evolution 的 evolve() 方法。
    """

    name: str  # 演化器名称

    @abstractmethod
    async def evolve(self, redis: Redis, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        """推进一个 Tick，返回更新的世界状态字段

        Args:
            redis: Redis 异步客户端
            tick_id: 当前 Tick 序号
            world_state: 当前完整世界状态（只读参考，用于跨演化器联动）

        Returns:
            需要合并回 `world:state` 主哈希的字段字典
        """
        ...

    async def setup(self, redis: Redis) -> None:
        """初始化（可选实现）"""
        pass

    # ------------------------------------------------------------------
    # Redis JSON 辅助方法：统一各演化器对 Hash 的读写与序列化
    # ------------------------------------------------------------------

    @staticmethod
    async def hset_json(redis: Redis, key: str, mapping: dict[str, Any]) -> None:
        """将字典写入 Redis Hash，每个 value 以 JSON 字符串存储"""
        if not mapping:
            return
        encoded = {str(k): json.dumps(v, ensure_ascii=False) for k, v in mapping.items()}
        await redis.hset(key, mapping=encoded)  # type: ignore[arg-type]

    @staticmethod
    async def hgetall_json(redis: Redis, key: str) -> dict[str, Any]:
        """读取 Redis Hash 并反序列化 JSON 值，返回 dict"""
        raw = await redis.hgetall(key)
        if not raw:
            return {}
        result: dict[str, object] = {}
        for k, v in raw.items():
            if isinstance(k, bytes):
                k = k.decode()
            if isinstance(v, bytes):
                v = v.decode()
            try:
                result[str(k)] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[str(k)] = v
        return result
