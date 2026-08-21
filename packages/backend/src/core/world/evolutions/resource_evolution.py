"""资源演化器 - 商店库存增减与物价波动

每 Tick 对各商品执行自然消耗、低库存补货，并基于库存相对基础量的偏离
进行价格波动（供不应求涨价，供过于求降价）。
状态存储于 Redis Hash: `world:state:resources`（field: good_id → JSON{inventory, price, base_price}）。
"""

import random
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.core.world.evolutions.base import WorldEvolution

logger = get_logger(__name__)

# 资源状态在 Redis 中的 Key
RESOURCES_KEY = "world:state:resources"

# 默认商品：基础库存、基础价格、单 Tick 消耗量、补货目标
DEFAULT_GOODS: dict[str, dict[str, Any]] = {
    "food": {"base_inventory": 100, "base_price": 10, "consumption": 5, "restock_to": 100},
    "energy": {"base_inventory": 80, "base_price": 15, "consumption": 4, "restock_to": 80},
    "coffee": {"base_inventory": 50, "base_price": 8, "consumption": 3, "restock_to": 50},
    "book": {"base_inventory": 30, "base_price": 25, "consumption": 1, "restock_to": 30},
}


class ResourceEvolution(WorldEvolution):
    """资源演化器

    每 Tick 推进各商品库存与价格：
    1. 库存按消耗量随机扰动后递减；
    2. 库存低于基础量 30% 时补货至 `restock_to`；
    3. 价格随库存紧缺程度上浮，充裕时下调，并叠加小幅随机波动。
    """

    name = "resource"

    def __init__(self, goods: dict[str, dict[str, Any]] | None = None) -> None:
        self.goods = goods or DEFAULT_GOODS

    async def setup(self, redis: Redis) -> None:
        """首次运行时初始化各商品库存与价格"""
        existing = await redis.hgetall(RESOURCES_KEY)
        if not existing:
            mapping = {
                gid: {"inventory": g["base_inventory"], "price": g["base_price"]} for gid, g in self.goods.items()
            }
            await self.hset_json(redis, RESOURCES_KEY, mapping)
            logger.info("resource_evolution_initialized", goods=list(self.goods.keys()))

    async def evolve(self, redis: Redis, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        """推进一轮库存与物价"""
        current = await self.hgetall_json(redis, RESOURCES_KEY)
        new_state: dict[str, dict[str, Any]] = {}

        for gid, g in self.goods.items():
            # 读取当前库存（缺失则用基础值）
            existing = current.get(gid) if current else None
            inv = int(existing.get("inventory", g["base_inventory"])) if existing else g["base_inventory"]

            # 1. 自然消耗（带随机扰动）
            consume = max(0, int(g["consumption"] * random.uniform(0.5, 1.5)))
            inv = max(0, inv - consume)

            # 2. 低于阈值补货
            restock_threshold = g["base_inventory"] * 0.3
            if inv < restock_threshold:
                inv = g["restock_to"]

            # 3. 价格波动：库存越低价格越高
            base_inv = max(1, g["base_inventory"])
            supply_ratio = inv / base_inv  # >1 充裕，<1 紧缺
            price_multiplier = 1.0 + (1.0 - supply_ratio) * 0.5
            price_multiplier = max(0.5, min(2.0, price_multiplier))
            price_multiplier *= random.uniform(0.95, 1.05)  # 小幅随机波动
            price = max(1.0, round(g["base_price"] * price_multiplier, 1))

            new_state[gid] = {
                "inventory": inv,
                "price": price,
                "base_price": g["base_price"],
            }

        await self.hset_json(redis, RESOURCES_KEY, new_state)

        logger.info("resources_updated", tick_id=tick_id, goods=new_state)
        return {"resources": new_state}
