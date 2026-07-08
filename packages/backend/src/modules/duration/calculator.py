"""动态耗时计算器

根据天气、拥挤度、角色状态等动态因素调整 Action 耗时。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Weather(str, Enum):
    """天气类型"""

    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    SNOWY = "snowy"
    STORMY = "stormy"
    FOGGY = "foggy"


# 天气对耗时的影响倍率
WEATHER_DURATION_MULTIPLIER: dict[Weather, float] = {
    Weather.SUNNY: 1.0,
    Weather.CLOUDY: 1.0,
    Weather.RAINY: 1.2,   # 雨天 +20%
    Weather.SNOWY: 1.3,   # 雪天 +30%
    Weather.STORMY: 1.5,  # 暴风 +50%
    Weather.FOGGY: 1.15,  # 雾天 +15%
}

# 天气对户外场景的影响（室内场景不受影响）
WEATHER_AFFECTS_OUTDOOR_ONLY: bool = True


@dataclass(frozen=True)
class DurationModifiers:
    """耗时修正因子

    每个因子为乘数，1.0 表示无影响。
    """

    weather: float = 1.0
    crowdedness: float = 1.0
    stamina: float = 1.0
    mood: float = 1.0

    def total_multiplier(self) -> float:
        """综合乘数（所有因子相乘）"""
        return self.weather * self.crowdedness * self.stamina * self.mood


class DurationCalculator:
    """动态耗时计算器

    用法：
        calc = DurationCalculator()
        modifiers = calc.compute_modifiers(
            weather="rainy",
            is_outdoor=True,
            crowdedness=0.7,
            stamina=30,
            mood="tired",
        )
        actual_duration = base_duration * modifiers.total_multiplier()
    """

    def compute_modifiers(
        self,
        weather: str | Weather = Weather.SUNNY,
        is_outdoor: bool = True,
        crowdedness: float = 0.0,
        stamina: int = 100,
        mood: str = "calm",
    ) -> DurationModifiers:
        """计算所有耗时修正因子

        Args:
            weather: 天气
            is_outdoor: 是否户外场景
            crowdedness: 拥挤度（0.0-1.0）
            stamina: 体力（0-100）
            mood: 情绪

        Returns:
            DurationModifiers
        """
        # 1. 天气影响（仅户外）
        weather_mod = self._weather_multiplier(weather, is_outdoor)

        # 2. 拥挤度影响（>50% 开始影响）
        crowd_mod = self._crowdedness_multiplier(crowdedness)

        # 3. 体力影响（<30 开始影响）
        stamina_mod = self._stamina_multiplier(stamina)

        # 4. 情绪影响
        mood_mod = self._mood_multiplier(mood)

        return DurationModifiers(
            weather=weather_mod,
            crowdedness=crowd_mod,
            stamina=stamina_mod,
            mood=mood_mod,
        )

    def calculate_duration(
        self,
        base_duration: int,
        weather: str | Weather = Weather.SUNNY,
        is_outdoor: bool = True,
        crowdedness: float = 0.0,
        stamina: int = 100,
        mood: str = "calm",
    ) -> int:
        """计算最终耗时（分钟）

        Args:
            base_duration: 基础耗时
            其他参数同 compute_modifiers

        Returns:
            调整后的耗时（向上取整，至少 1 分钟）
        """
        modifiers = self.compute_modifiers(
            weather, is_outdoor, crowdedness, stamina, mood
        )
        actual = base_duration * modifiers.total_multiplier()
        return max(1, int(actual + 0.5))  # 四舍五入，最小 1

    def _weather_multiplier(
        self, weather: str | Weather, is_outdoor: bool
    ) -> float:
        """天气对耗时的影响"""
        if not is_outdoor and WEATHER_AFFECTS_OUTDOOR_ONLY:
            return 1.0  # 室内不受影响

        if isinstance(weather, str):
            try:
                weather = Weather(weather)
            except ValueError:
                logger.warning("未知天气: %s，使用默认倍率", weather)
                return 1.0

        return WEATHER_DURATION_MULTIPLIER.get(weather, 1.0)

    @staticmethod
    def _crowdedness_multiplier(crowdedness: float) -> float:
        """拥挤度对耗时的影响

        crowdedness > 0.5 后线性增加，最高 +50%。
        """
        if crowdedness <= 0.5:
            return 1.0
        # 0.5 -> 1.0, 1.0 -> 1.5
        return 1.0 + (crowdedness - 0.5) * 1.0

    @staticmethod
    def _stamina_multiplier(stamina: int) -> float:
        """体力对耗时的影响

        stamina < 30 后线性增加，最低体力时 +50%。
        """
        if stamina >= 30:
            return 1.0
        # 30 -> 1.0, 0 -> 1.5
        return 1.0 + (30 - stamina) / 30 * 0.5

    @staticmethod
    def _mood_multiplier(mood: str) -> float:
        """情绪对耗时的影响

        负面情绪降低效率（增加耗时），积极情绪无影响。
        """
        # 降低效率的情绪
        if mood in ("tired", "sad", "anxious", "angry"):
            return 1.1  # +10%
        if mood in ("sick", "exhausted"):
            return 1.25  # +25%
        # happy / calm / excited 等正常
        return 1.0
