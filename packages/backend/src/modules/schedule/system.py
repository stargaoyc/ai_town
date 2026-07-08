"""作息系统

根据角色 traits.schedule（early_bird / normal / night_owl），
判断角色在给定时间的活动状态。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ActivityLevel(str, Enum):
    """活动水平等级"""

    SLEEPING = "sleeping"       # 睡眠中
    DROWSY = "drowsy"           # 困倦（过渡期）
    ACTIVE = "active"           # 活跃
    PEAK = "peak"               # 高峰（最佳状态）


@dataclass(frozen=True)
class ScheduleProfile:
    """作息档案

    每种作息类型对应一个时间窗口：
    - sleep_start: 入睡时间（小时，0-23）
    - sleep_end: 起床时间（小时，0-23）
    - peak_start: 高峰期开始
    - peak_end: 高峰期结束
    - stamina_regen_multiplier: 睡眠时体力恢复倍率
    """

    sleep_start: int
    sleep_end: int
    peak_start: int
    peak_end: int
    stamina_regen_multiplier: float = 1.0


# 三种作息类型的档案配置
SCHEDULE_PROFILES: dict[str, ScheduleProfile] = {
    "early_bird": ScheduleProfile(
        sleep_start=22,    # 22:00 入睡
        sleep_end=6,       # 6:00 起床
        peak_start=7,      # 7:00 进入高峰
        peak_end=11,       # 11:00 高峰结束
        stamina_regen_multiplier=1.2,  # 早睡早起体力恢复加成
    ),
    "normal": ScheduleProfile(
        sleep_start=23,    # 23:00 入睡
        sleep_end=7,       # 7:00 起床
        peak_start=9,      # 9:00 进入高峰
        peak_end=17,       # 17:00 高峰结束
        stamina_regen_multiplier=1.0,
    ),
    "night_owl": ScheduleProfile(
        sleep_start=2,     # 2:00 入睡（次日凌晨）
        sleep_end=10,      # 10:00 起床
        peak_start=14,     # 14:00 进入高峰
        peak_end=24,       # 24:00（午夜）高峰结束
        stamina_regen_multiplier=0.9,  # 熬夜体力恢复略低
    ),
}


class ScheduleSystem:
    """作息系统

    用法：
        system = ScheduleSystem()
        level = system.get_activity_level("early_bird", hour=8)
        # -> ActivityLevel.PEAK

        is_sleeping = system.is_sleeping("early_bird", hour=3)
        # -> True
    """

    def __init__(self, profiles: dict[str, ScheduleProfile] | None = None):
        self._profiles = profiles or SCHEDULE_PROFILES

    def get_profile(self, schedule: str) -> ScheduleProfile:
        """获取作息档案

        未知类型默认使用 normal。
        """
        return self._profiles.get(schedule, self._profiles["normal"])

    def get_activity_level(
        self, schedule: str, hour: int
    ) -> ActivityLevel:
        """获取角色在指定时间的活动水平

        Args:
            schedule: 作息类型（early_bird/normal/night_owl）
            hour: 当前小时（0-23）

        Returns:
            ActivityLevel 枚举值
        """
        profile = self.get_profile(schedule)

        if self._is_in_sleep_window(profile, hour):
            return ActivityLevel.SLEEPING

        # 检查是否在高峰期
        if self._is_in_window(hour, profile.peak_start, profile.peak_end):
            return ActivityLevel.PEAK

        # 睡前/起床后 1 小时为困倦期
        if hour == profile.sleep_start or hour == profile.sleep_end:
            return ActivityLevel.DROWSY

        return ActivityLevel.ACTIVE

    def is_sleeping(self, schedule: str, hour: int) -> bool:
        """判断角色是否在睡眠中"""
        return self.get_activity_level(schedule, hour) == ActivityLevel.SLEEPING

    def is_peak_hours(self, schedule: str, hour: int) -> bool:
        """判断是否处于活动高峰期"""
        return self.get_activity_level(schedule, hour) == ActivityLevel.PEAK

    def get_stamina_regen_rate(self, schedule: str, hour: int) -> float:
        """获取体力恢复速率

        睡眠时使用 profile.stamina_regen_multiplier，
        其他时段正常恢复（1.0 倍率）。
        """
        profile = self.get_profile(schedule)
        if self._is_in_sleep_window(profile, hour):
            return profile.stamina_regen_multiplier
        return 1.0

    def get_schedule_from_traits(self, traits: dict[str, Any]) -> str:
        """从角色 traits 字典中提取作息类型

        无 schedule 字段时默认 normal。
        """
        schedule = traits.get("schedule", "normal")
        if schedule not in self._profiles:
            logger.warning("未知作息类型 %s，使用 normal", schedule)
            return "normal"
        return schedule

    def _is_in_sleep_window(
        self, profile: ScheduleProfile, hour: int
    ) -> bool:
        """判断是否在睡眠时段

        处理跨午夜的情况（如 night_owl 的 2:00-10:00）。
        """
        return self._is_in_window(hour, profile.sleep_start, profile.sleep_end)

    @staticmethod
    def _is_in_window(hour: int, start: int, end: int) -> bool:
        """判断 hour 是否在 [start, end) 窗口内

        支持跨午夜窗口（start > end），如 22-6 表示 22:00 到次日 6:00。
        """
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        # 跨午夜：[start, 24) ∪ [0, end)
        return hour >= start or hour < end
