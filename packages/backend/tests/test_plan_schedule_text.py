"""作息桥接单元测试 - _build_schedule_text / _world_hour（Plan 层级体系 WS-D）"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.core.character.tick import _build_schedule_text, _world_hour


class StubScheduleSystem:
    def __init__(self, level: str, sleeping: bool) -> None:
        self._level = level
        self._sleeping = sleeping

    def get_schedule_from_traits(self, traits: dict[str, Any]) -> str:
        return str(traits.get("schedule", "normal"))

    def get_activity_level(self, schedule: str, hour: int) -> str:
        return self._level

    def is_sleeping(self, schedule: str, hour: int) -> bool:
        return self._sleeping


def _patched(monkeypatch: Any, system: Any) -> None:  # noqa: ANN001
    # tick.py 顶层 from-import 绑定了名字，需 patch 其模块命名空间
    import src.core.character.tick as tick_mod

    monkeypatch.setattr(tick_mod, "get_schedule_system", lambda: system)


class TestWorldHour:
    def test_iso_format(self) -> None:
        assert _world_hour({"world_time": "2026-08-24T14:30:00"}) == 14

    def test_space_separator(self) -> None:
        assert _world_hour({"world_time": "2026-08-24 09:15"}) == 9

    def test_plain_hhmm(self) -> None:
        assert _world_hour({"world_time": "21:00"}) == 21

    def test_garbage_falls_back_to_now(self) -> None:
        hour = _world_hour({"world_time": "not-a-time"})
        assert 0 <= hour <= 23


class TestBuildScheduleText:
    def test_no_system_returns_placeholder(self, monkeypatch: Any) -> None:
        _patched(monkeypatch, None)
        traits_obj: dict[str, Any] = {}
        text = _build_schedule_text(traits_obj, {})
        assert "无作息档案" in text

    def test_active_level_rendered(self, monkeypatch: Any) -> None:
        _patched(monkeypatch, StubScheduleSystem("peak", sleeping=False))
        character = type("C", (), {"traits": {"schedule": "early_bird"}, "id": uuid4()})()
        text = _build_schedule_text(character, {"world_time": "2026-08-24T10:00:00"})
        assert "高峰" in text
        assert "睡眠" not in text

    def test_sleeping_constraint_hinted(self, monkeypatch: Any) -> None:
        _patched(monkeypatch, StubScheduleSystem("sleeping", sleeping=True))
        character = type("C", (), {"traits": {}})()
        text = _build_schedule_text(character, {"world_time": "2026-08-24T02:00:00"})
        assert "睡眠" in text
        assert "不宜外出" in text
