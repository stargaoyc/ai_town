"""P0-9 回归测试：world-map.yaml 连通矩阵校验

验证 SceneLoader._validate_world_map：
- 完整对称矩阵加载通过
- 缺出发边 / 不对称 / 耗时不等 / 孤岛 / 未知目标 → ValueError 启动报错
"""

from pathlib import Path
from typing import Any, cast

import pytest
from redis.asyncio import Redis

from src.modules.town.loader import SceneLoader


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.hashes else 0

    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.hashes[key] = mapping or {}


_SCENES_YAML = """
scenes:
  - id: home
    name: 家
    type: indoor
    open_hours: [0, 24]
    capacity: 5
    activities: [sleep]
  - id: cafe
    name: 咖啡店
    type: indoor
    open_hours: [7, 22]
    capacity: 20
    activities: [eat]
  - id: park
    name: 公园
    type: outdoor
    open_hours: [0, 24]
    capacity: 100
    activities: [relax]
"""

_SCENES_YAML_WITH_FOREST = (
    _SCENES_YAML
    + """
  - id: forest
    name: 森林
    type: outdoor
    open_hours: [0, 24]
    capacity: 30
    activities: [explore]
"""
)


def _write(tmp_path: Path, scenes: str, map_text: str) -> tuple[str, str]:
    scenes_path = tmp_path / "scenes.yaml"
    map_path = tmp_path / "world-map.yaml"
    scenes_path.write_text(scenes, encoding="utf-8")
    map_path.write_text(f"adjacency:\n{map_text}", encoding="utf-8")
    return str(scenes_path), str(map_path)


async def _load(tmp_path: Path, map_text: str, scenes: str = _SCENES_YAML) -> SceneLoader:
    scenes_path, map_path = _write(tmp_path, scenes, map_text)
    loader = SceneLoader(cast(Redis, FakeRedis()))
    await loader.load_from_files(scenes_path, map_path)
    return loader


async def test_valid_symmetric_map_loads(tmp_path: Path) -> None:
    map_text = """  home:
    cafe: 8
    park: 10
  cafe:
    home: 8
    park: 7
  park:
    home: 10
    cafe: 7
"""
    loader = await _load(tmp_path, map_text)
    assert loader.get_travel_time("home", "park") == 10
    assert loader.get_travel_time("park", "home") == 10


async def test_missing_source_scene_raises(tmp_path: Path) -> None:
    map_text = """  home:
    cafe: 8
  cafe:
    home: 8
"""
    with pytest.raises(ValueError, match="缺少出发边"):
        await _load(tmp_path, map_text)


async def test_missing_reverse_edge_raises(tmp_path: Path) -> None:
    map_text = """  home:
    cafe: 8
  cafe:
    home: 8
    park: 5
  park:
    home: 10
"""
    with pytest.raises(ValueError, match="无反向边"):
        await _load(tmp_path, map_text)


async def test_asymmetric_duration_raises(tmp_path: Path) -> None:
    map_text = """  home:
    cafe: 8
    park: 10
  cafe:
    home: 9
    park: 5
  park:
    home: 10
    cafe: 5
"""
    with pytest.raises(ValueError, match="≠"):
        await _load(tmp_path, map_text)


async def test_unknown_target_raises(tmp_path: Path) -> None:
    map_text = """  home:
    cafe: 8
    nowhere: 5
    park: 10
  cafe:
    home: 8
  park:
    home: 10
"""
    with pytest.raises(ValueError, match="指向未知场景"):
        await _load(tmp_path, map_text)


async def test_unreachable_scene_raises(tmp_path: Path) -> None:
    map_text = """  home:
    cafe: 8
  cafe:
    home: 8
    park: 5
  park:
    cafe: 5
  forest:
    forest: 5
"""
    with pytest.raises(ValueError, match="不可达场景"):
        await _load(tmp_path, map_text, scenes=_SCENES_YAML_WITH_FOREST)
