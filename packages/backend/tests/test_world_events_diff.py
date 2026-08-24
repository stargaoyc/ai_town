"""世界事件差分纯函数单元测试 - 去重基线语义（T3）"""

from __future__ import annotations

from typing import Any

from src.core.world.engine import collect_changed_events


def _state(
    time: str = "08:00",
    weather: str = "sunny",
    scenes: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    events: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "time": {"world_time": time},
        "weather": weather,
        "scenes": scenes or {},
        "resources": resources or {},
        "events": events or {},
    }


class TestCollectChangedEvents:
    def test_first_run_writes_all_non_empty_dims(self) -> None:
        state = _state(scenes={"cafe": {"visitors": 2}}, resources={"coffee": 10})
        events, baseline = collect_changed_events(state, {}, tick_id=1)

        types = sorted(e.event_type for e in events)
        assert types == ["resource", "scene", "time", "weather"]
        assert baseline["time"] == "08:00"
        assert baseline["weather"] == "sunny"

    def test_unchanged_state_emits_nothing(self) -> None:
        state = _state(scenes={"cafe": {}}, resources={"coffee": 5})
        _, baseline = collect_changed_events(state, {}, tick_id=1)

        events, _ = collect_changed_events(state, baseline, tick_id=2)
        # 时间未变（08:00 == 08:00）且其余维度一致 -> 零事件
        assert events == []

    def test_time_change_only(self) -> None:
        state = _state()
        _, baseline = collect_changed_events(state, {}, tick_id=1)

        events, _ = collect_changed_events(_state(time="09:00"), baseline, tick_id=2)
        assert len(events) == 1
        assert events[0].event_type == "time"
        assert events[0].payload["virtual_time"] == "09:00"

    def test_weather_change_only(self) -> None:
        state = _state()
        _, baseline = collect_changed_events(state, {}, tick_id=1)

        events, _ = collect_changed_events(_state(weather="rainy"), baseline, tick_id=2)
        assert len(events) == 1
        assert events[0].event_type == "weather"

    def test_scene_change_detected_via_json(self) -> None:
        state = _state(scenes={"cafe": {"visitors": 1}})
        _, baseline = collect_changed_events(state, {}, tick_id=1)

        changed = _state(scenes={"cafe": {"visitors": 2}})
        events, _ = collect_changed_events(changed, baseline, tick_id=2)
        assert [e.event_type for e in events] == ["scene"]

    def test_active_events_always_written(self) -> None:
        state = _state(events={"festival": {"name": "夏日祭"}})
        _, baseline = collect_changed_events(state, {}, tick_id=1)

        # 与基线完全相同的 events 仍写入（活跃事件本身即"变化"）
        events, _ = collect_changed_events(state, baseline, tick_id=2)
        assert [e.event_type for e in events] == ["event"]

    def test_empty_dims_not_written_on_first_run(self) -> None:
        events, _ = collect_changed_events(_state(), {}, tick_id=1)
        # time/weather 有值会写；scenes/resources/events 为空不写
        assert sorted(e.event_type for e in events) == ["time", "weather"]
