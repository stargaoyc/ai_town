"""MovementSystem 天气倍率与 workday_only 生效验证（round-6 M9a/M9b）

此前 WEATHER_IMPACT.move_multiplier 无消费方、is_workday 从未被 Tick 传入，
workday_only 场景限制是不可达死逻辑。本文件锁定两条链路的真实行为。
"""

from typing import cast

from redis.asyncio import Redis

from src.modules.movement.system import MovementSystem
from src.modules.town.loader import SceneLoader
from src.modules.town.schema import Scene, SceneType, WorldMap

_ADJACENCY = {
    "home": {"cafe": 12, "school": 10},
    "cafe": {"home": 12, "school": 5},
    "school": {"home": 10, "cafe": 5},
}

_MONDAY_10AM = ("2026-08-24T10:00:00+00:00", 10)  # 周一
_SATURDAY_10AM = ("2026-08-22T10:00:00+00:00", 10)  # 周六


class _NoCapacityRedis:
    async def hget(self, key: str, field: str) -> None:
        return None


def _make_movement_system() -> MovementSystem:
    loader = SceneLoader(cast(Redis, _NoCapacityRedis()))
    loader._scenes = {
        "home": Scene(id="home", name="家", type=SceneType.INDOOR, open_hours=[0, 24], capacity=5),
        "cafe": Scene(id="cafe", name="咖啡店", type=SceneType.INDOOR, open_hours=[7, 22], capacity=20),
        "school": Scene(
            id="school", name="学校", type=SceneType.INDOOR, open_hours=[8, 17], capacity=50, workday_only=True
        ),
    }
    loader._world_map = WorldMap(adjacency=_ADJACENCY)
    return MovementSystem(loader)


class TestWeatherMoveMultiplier:
    async def test_rainy_multiplier_raises_travel_minutes(self) -> None:
        result = await _make_movement_system().calculate_move("home", "cafe", weather_move_multiplier=1.5)
        assert result.success
        assert result.total_minutes == 18  # 矩阵 12 × 1.5

    async def test_default_multiplier_keeps_matrix_minutes(self) -> None:
        result = await _make_movement_system().calculate_move("home", "cafe")
        assert result.success
        assert result.total_minutes == 12

    async def test_final_minutes_clamped_to_at_least_one(self) -> None:
        # 倍率由调用方传入，退化值不得把耗时归零
        result = await _make_movement_system().calculate_move("home", "cafe", weather_move_multiplier=0.001)
        assert result.success
        assert result.total_minutes == 1


class TestWorkdayOnlySceneRestriction:
    async def test_workday_only_scene_rejected_on_weekend(self) -> None:
        _, hour = _SATURDAY_10AM
        result = await _make_movement_system().calculate_move("home", "school", hour=hour, is_workday=False)
        assert not result.success
        assert "未开放" in (result.reason or "")

    async def test_workday_only_scene_allowed_on_weekday(self) -> None:
        _, hour = _MONDAY_10AM
        result = await _make_movement_system().calculate_move("home", "school", hour=hour, is_workday=True)
        assert result.success
        assert result.total_minutes == 10


class TestOpenHoursStillEnforced:
    async def test_closed_hour_rejects_even_on_weekday(self) -> None:
        result = await _make_movement_system().calculate_move("home", "cafe", hour=23, is_workday=True)
        assert not result.success
