"""资源演化器 - 商店库存增减与物价波动

每 Tick 对各商品执行自然消耗、低库存补货，并基于库存相对基础量的偏离
进行价格波动（供不应求涨价，供过于求降价）。
状态存储于 Redis Hash: `world:state:resources`（field: good_id → JSON{inventory, price, base_price}）。
商品配置唯一真相源为 configs/resources.yaml。
"""

import random
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.core.world.evolutions.base import WorldEvolution
from src.paths import find_project_root

logger = get_logger(__name__)

# 资源状态在 Redis 中的 Key
RESOURCES_KEY = "world:state:resources"

# 商品配置的必填数值字段
_GOOD_FIELDS = ("base_inventory", "base_price", "consumption", "restock_to")


def _load_goods_config(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """从 configs/resources.yaml 加载商品配置（round-6 L17b：真相源外置 + 启动校验）

    Args:
        path: 显式配置路径；缺省经 find_project_root 定位（测试注入用）

    Returns:
        {good_id: {base_inventory, base_price, consumption, restock_to}}

    Raises:
        FileNotFoundError: configs/resources.yaml 不存在（配置挂载缺失）
        ValueError: 商品条目缺失必填字段或数值非法
    """
    import yaml as _yaml

    config_path = path if path is not None else find_project_root() / "configs" / "resources.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"configs/resources.yaml not found at {config_path}; 容器部署需挂载 ./configs:/app/configs"
        )
    with open(config_path, encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}

    raw_goods = data.get("goods")
    if not isinstance(raw_goods, dict) or not raw_goods:
        raise ValueError(f"resources.yaml 必须包含非空 goods 映射: {config_path}")

    goods: dict[str, dict[str, Any]] = {}
    for gid, spec in raw_goods.items():
        if not isinstance(spec, dict):
            raise ValueError(f"resources.yaml 商品 {gid!r} 配置必须是映射: {spec!r}")
        for field in _GOOD_FIELDS:
            value = spec.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"resources.yaml 商品 {gid!r} 的 {field} 必须为非负数: {value!r}")
        if int(spec["base_inventory"]) < 1:
            raise ValueError(f"resources.yaml 商品 {gid!r} 的 base_inventory 必须为正整数")
        goods[str(gid)] = {field: spec[field] for field in _GOOD_FIELDS}

    logger.info("goods_config_loaded", source=str(config_path), goods=list(goods.keys()))
    return goods


class ResourceEvolution(WorldEvolution):
    """资源演化器

    每 Tick 推进各商品库存与价格：
    1. 库存按消耗量随机扰动后递减；
    2. 库存低于基础量 30% 时补货至 `restock_to`；
    3. 价格随库存紧缺程度上浮，充裕时下调，并叠加小幅随机波动。
    """

    name = "resource"

    def __init__(self, goods: dict[str, dict[str, Any]] | None = None) -> None:
        # 显式传入优先（测试注入）；否则构造时从 configs/resources.yaml 加载，
        # 缺失/损坏即失败——引擎在 app lifespan 构造演化器，等价于启动期 fail-fast
        self.goods = goods if goods is not None else _load_goods_config()

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
