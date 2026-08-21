"""天气演化器 - 按季节与间隔更新天气

每 `world_weather_interval` 个 Tick 更新一次天气；天气类型概率随季节变化，
并输出天气影响矩阵（移动耗时倍率、室外活动失败概率增量）。
状态存储于 Redis Hash: `world:state:weather`。
"""

import random
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.config import settings
from src.core.world.evolutions.base import WorldEvolution
from src.core.world.evolutions.time_evolution import TIME_KEY

logger = get_logger(__name__)

# 天气状态在 Redis 中的 Key
WEATHER_KEY = "world:state:weather"

# 天气类型
WEATHER_TYPES: tuple[str, ...] = ("sunny", "cloudy", "rainy", "snowy", "stormy")

# 季节 → 各天气权重（顺序对应 WEATHER_TYPES：晴/多云/雨/雪/暴风）
SEASON_WEIGHTS: dict[str, tuple[int, int, int, int, int]] = {
    "spring": (40, 30, 20, 0, 10),
    "summer": (50, 20, 20, 0, 10),
    "autumn": (30, 35, 25, 0, 10),
    "winter": (30, 25, 10, 25, 10),
}

# 天气影响矩阵：
#   move_multiplier       - 室外移动耗时倍率
#   outdoor_fail_bonus    - 室外活动 precondition 失败概率增量
# 参考 world-engine.md 2.9 天气影响矩阵
WEATHER_IMPACT: dict[str, dict[str, float]] = {
    "sunny": {"move_multiplier": 1.0, "outdoor_fail_bonus": 0.0},
    "cloudy": {"move_multiplier": 1.0, "outdoor_fail_bonus": 0.0},
    "rainy": {"move_multiplier": 1.5, "outdoor_fail_bonus": 0.2},
    "snowy": {"move_multiplier": 2.0, "outdoor_fail_bonus": 0.1},
    "stormy": {"move_multiplier": 1.5, "outdoor_fail_bonus": 0.3},
}


class WeatherEvolution(WorldEvolution):
    """天气演化器

    每 `world_weather_interval` 个 Tick 更新一次天气；根据季节抽样天气类型，
    并写入对应的影响矩阵，供 Character Tick 决策与耗时计算使用。
    """

    name = "weather"

    async def setup(self, redis: Redis) -> None:
        """首次运行时初始化为晴天"""
        existing = await redis.hgetall(WEATHER_KEY)
        if not existing:
            weather = "sunny"
            await self.hset_json(
                redis,
                WEATHER_KEY,
                {
                    "weather": weather,
                    "season": "spring",
                    "impact": WEATHER_IMPACT[weather],
                    "last_updated_tick": 0,
                },
            )
            logger.info("weather_evolution_initialized", weather=weather)

    async def evolve(self, redis: Redis, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        """按间隔更新或保持当前天气"""
        interval = settings.world_weather_interval
        state = await self.hgetall_json(redis, WEATHER_KEY)

        last_updated = state.get("last_updated_tick", 0) if state else 0
        current_weather = state.get("weather", "sunny") if state else "sunny"
        current_impact = (
            state.get("impact", WEATHER_IMPACT[current_weather]) if state else WEATHER_IMPACT[current_weather]
        )

        # 是否到达更新时机（首次或间隔满足）
        should_update = (not state) or (tick_id - last_updated) >= interval

        if should_update:
            season = await self._current_season(redis, world_state)
            weather = self._pick_weather(season)
            impact = WEATHER_IMPACT[weather]
            new_state = {
                "weather": weather,
                "season": season,
                "impact": impact,
                "last_updated_tick": tick_id,
            }
            await self.hset_json(redis, WEATHER_KEY, new_state)

            logger.info(
                "weather_updated",
                tick_id=tick_id,
                weather=weather,
                season=season,
                impact=impact,
            )
            return {
                "weather": weather,
                "temperature": self._temperature(season, weather),
                "weather_impact": impact,
            }

        # 未到更新时机，保持当前天气
        return {
            "weather": current_weather,
            "weather_impact": current_impact,
        }

    # ------------------------------------------------------------------
    # 私有工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_weather(season: str) -> str:
        """按季节权重随机抽取一种天气"""
        weights = SEASON_WEIGHTS.get(season, SEASON_WEIGHTS["spring"])
        return random.choices(WEATHER_TYPES, weights=weights, k=1)[0]

    @staticmethod
    def _temperature(season: str, weather: str) -> int:
        """简单估算当前温度（℃）"""
        base = {"spring": 18, "summer": 30, "autumn": 15, "winter": 2}.get(season, 20)
        if weather in ("rainy", "stormy"):
            base -= 3
        if weather == "snowy":
            base -= 5
        return base

    async def _current_season(self, redis: Redis, world_state: dict[str, Any]) -> str:
        """从 world_state 或时间哈希读取当前季节"""
        season = world_state.get("season")
        if season:
            return str(season)
        time_state = await self.hgetall_json(redis, TIME_KEY)
        return str(time_state.get("season", "spring"))
