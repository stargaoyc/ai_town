"""P0-2 回归测试：world:state 时间字段名与小时解析

WorldEngine 写入的字段名是 "world_time"；此前误读 "time" 导致
hour 恒回退 8 点、场景开放时段判断失真。
"""

from src.api.characters import _world_hour_from_state


def test_parses_world_time_field() -> None:
    assert _world_hour_from_state({"world_time": "14:30"}) == 14


def test_falls_back_when_field_missing() -> None:
    assert _world_hour_from_state({}) == 8


def test_falls_back_on_malformed_value() -> None:
    assert _world_hour_from_state({"world_time": "not-a-time"}) == 8


def test_ignores_legacy_time_field() -> None:
    assert _world_hour_from_state({"time": "22:00"}) == 8
