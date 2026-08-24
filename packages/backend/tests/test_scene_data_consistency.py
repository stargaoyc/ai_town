"""P0-3 / P0-4 回归测试：场景数据单一真相源与幻影 matrix 键清理

- SceneEvolution 不再依赖硬编码 DEFAULT_SCENES：显式传入、经 SceneLoader
  （runtime 注入）解析、loader 缺失时降级为空操作三种路径
- tools.world.get_scene_info 的出口改读 SceneLoader 连通矩阵
"""

from typing import Any, cast

import pytest
from redis.asyncio import Redis

from src.core.world.evolutions.scene_evolution import SCENES_KEY, SceneEvolution, is_open
from src.runtime import set_scene_loader


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.store.setdefault(key, {}).update({k: str(v) for k, v in (mapping or {}).items()})


class FakeScene:
    def __init__(self, scene_id: str, open_hours: list[int], capacity: int) -> None:
        self.id = scene_id
        self.open_hours = open_hours
        self.capacity = capacity


class FakeLoader:
    def __init__(self, scenes: dict[str, FakeScene], neighbors: dict[str, dict[str, int]]) -> None:
        self._scenes = scenes
        self._neighbors = neighbors

    def get_all_scenes(self) -> dict[str, FakeScene]:
        return self._scenes

    def get_neighbors(self, scene_id: str) -> dict[str, int]:
        return self._neighbors.get(scene_id, {})

    def get_travel_time(self, from_scene: str, to_scene: str) -> int | None:
        return self._neighbors.get(from_scene, {}).get(to_scene)


def test_explicit_scenes_win_over_loader() -> None:
    explicit = {"cafe": {"open_hours": (7, 22), "capacity": 20}}
    evo = SceneEvolution(scenes=explicit)
    assert evo.scenes == explicit


def test_resolves_all_scenes_from_loader() -> None:
    loader = FakeLoader(
        {
            "home": FakeScene("home", [0, 24], 5),
            "school": FakeScene("school", [8, 17], 50),
            "cafe": FakeScene("cafe", [7, 22], 20),
        },
        {},
    )
    set_scene_loader(loader)  # type: ignore[arg-type]
    try:
        evo = SceneEvolution()
        assert set(evo.scenes.keys()) == {"home", "school", "cafe"}
        assert evo.scenes["school"]["open_hours"] == (8, 17)
        assert evo.scenes["school"]["capacity"] == 50
    finally:
        set_scene_loader(None)


def test_missing_loader_degrades_to_noop() -> None:
    set_scene_loader(None)
    evo = SceneEvolution()
    assert evo.scenes == {}


async def test_refresh_covers_every_scene() -> None:
    evo = SceneEvolution(
        scenes={
            "home": {"open_hours": (0, 24), "capacity": 5},
            "cafe": {"open_hours": (7, 22), "capacity": 20},
        }
    )
    redis = FakeRedis()
    state = await evo._refresh(cast(Redis, redis), hour=10, visitors_map={"home": 2})
    assert set(state.keys()) == {"home", "cafe"}
    assert state["home"] == {"open": True, "crowded": 40, "visitors": 2, "capacity": 5}
    assert SCENES_KEY in redis.store


def test_is_open_crosses_midnight() -> None:
    assert is_open((18, 26), 23)
    assert is_open((18, 26), 1)
    assert not is_open((18, 26), 12)


def test_is_open_full_day_scene() -> None:
    # 回归：end%24 将 24 归零，曾使 [0,24] 全天场景恒为关闭
    assert is_open((0, 24), 0)
    assert is_open((0, 24), 12)
    assert is_open((0, 24), 23)


def test_is_open_end_zero_means_24() -> None:
    # yaml 约定：end=0 表示 24 点（与 SceneLoader 一致）
    assert is_open((8, 0), 8)
    assert is_open((8, 0), 23)
    assert not is_open((8, 0), 7)


# ---------------------------------------------------------------------------
# tools.world.get_scene_info 出口来源
# ---------------------------------------------------------------------------


async def test_get_scene_info_exits_from_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.tools import world as world_tool

    loader = FakeLoader({}, {"home": {"school": 5, "cafe": 8}})
    monkeypatch.setattr(world_tool, "get_scene_loader", lambda: loader)

    result = await world_tool.get_scene_info("home")

    assert result["success"] is True
    assert result["exits"] == {"school": 5, "cafe": 8}


async def test_get_scene_info_exits_empty_without_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.tools import world as world_tool

    monkeypatch.setattr(world_tool, "get_scene_loader", lambda: None)

    result = await world_tool.get_scene_info("home")

    assert result["success"] is True
    assert result["exits"] == {}
