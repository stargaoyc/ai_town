"""DiaryService 世界时钟语义单元测试 - 窗口换算 / 触发矩阵 / 日期派生（round-3 review H1）"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config import settings
from src.memory.diary_service import _derive_diary_dates, _diary_trigger_periods, _world_real_window_seconds


@pytest.fixture
def default_world_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """钉住默认世界时钟参数，隔离 .env 覆盖（10 虚拟分 / 30 真实秒 → 1 世界日 = 72 真实分）"""
    monkeypatch.setattr(settings, "world_tick_seconds", 30)
    monkeypatch.setattr(settings, "world_tick_minutes", 10.0)


class TestWorldRealWindowSeconds:
    def test_day_equals_72_real_minutes_at_default_settings(self, default_world_clock: None) -> None:
        assert _world_real_window_seconds("day") == pytest.approx(72 * 60)

    def test_windows_scale_with_world_days(self, default_world_clock: None) -> None:
        day_seconds = _world_real_window_seconds("day")
        assert _world_real_window_seconds("week") == pytest.approx(day_seconds * 7)
        assert _world_real_window_seconds("month") == pytest.approx(day_seconds * 30)
        assert _world_real_window_seconds("year") == pytest.approx(day_seconds * 365)


class TestDiaryTriggerPeriods:
    def test_night_hours_trigger_day(self) -> None:
        for hour in (22, 23, 0, 5):
            world_now = datetime(2026, 8, 10, hour, 0, tzinfo=UTC)
            assert "day" in _diary_trigger_periods(world_now)

    def test_daytime_hours_do_not_trigger_day(self) -> None:
        for hour in (6, 12, 21):
            world_now = datetime(2026, 8, 10, hour, 0, tzinfo=UTC)
            assert "day" not in _diary_trigger_periods(world_now)

    def test_week_triggers_on_7th_world_day(self) -> None:
        # 第 14 世界日：%7==0 周中；%30==14、%365!=0 不触发月/年
        assert _diary_trigger_periods(datetime(2026, 1, 14, 23, 0, tzinfo=UTC)) == ["day", "week"]

    def test_month_triggers_on_30th_world_day(self) -> None:
        # 第 30 世界日：%7==2 不触发周
        assert _diary_trigger_periods(datetime(2026, 1, 30, 23, 0, tzinfo=UTC)) == ["day", "month"]

    def test_year_triggers_on_365th_world_day(self) -> None:
        # 平年末日第 365 天：%7==1、%30==5，只命中年
        assert _diary_trigger_periods(datetime(2026, 12, 31, 2, 0, tzinfo=UTC)) == ["day", "year"]


class TestDeriveDiaryDates:
    def test_diary_date_derives_from_world_now_not_wall_clock(self) -> None:
        world_now = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)
        diary_date, diary_end = _derive_diary_dates("day", world_now)
        # 幂等键 diary_date::date 与存储值都必须来自世界日期（H1：此前错用真实日期）
        assert diary_date == world_now
        assert diary_date.date() == datetime(2026, 3, 1).date()
        assert diary_end is None

    def test_period_end_backdated_by_world_days(self) -> None:
        world_now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        _, diary_end = _derive_diary_dates("week", world_now)
        assert diary_end == datetime(2026, 3, 3, 12, 0, tzinfo=UTC)

    def test_day_period_has_no_end(self) -> None:
        _, diary_end = _derive_diary_dates("month", datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
        assert diary_end == datetime(2026, 1, 30, 0, 0, tzinfo=UTC)
