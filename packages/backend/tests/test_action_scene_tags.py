"""Round-6 审查 R6-L15 回归测试：Action scene_tags 标签解析

- 场景标签在注册期经 SceneLoader 解析为具体场景 ID 集，替代代码里硬编码的场景 ID。
- 解析结果须与改动前的硬编码场景集逐字等价。
- 标签未命中任何场景 / 未注入 SceneLoader 时注册失败（fail-fast）。
"""

from typing import Any, cast

import pytest
from redis.asyncio import Redis

from src.actions import ActionRegistry, register_all
from src.actions.base import Action, ActionCategory
from src.modules.town.loader import SceneLoader
from src.paths import find_project_root


class _FakeRedis:
    """SceneLoader 构造用替身：load_configs_sync 不触碰 Redis，仅满足类型"""


class _FakeLoader:
    """SceneLoader 替身：按标签返回场景 ID 集合"""

    def __init__(self, tag_map: dict[str, set[str]]) -> None:
        self._tag_map = tag_map

    def get_scene_ids_by_tag(self, tag: str) -> frozenset[str]:
        return frozenset(self._tag_map.get(tag, set()))


def _real_loader() -> SceneLoader:
    """从真实 configs/scenes.yaml 同步加载场景配置（不初始化 Redis）"""
    loader = SceneLoader(cast(Redis, _FakeRedis()))
    project_root = find_project_root()
    loader.load_configs_sync(
        project_root / "configs" / "scenes.yaml",
        project_root / "configs" / "world-map.yaml",
    )
    return loader


def _state(location: str) -> dict[str, Any]:
    """资源充足的状态，确保候选过滤只受场景影响"""
    return {
        "location": location,
        "stamina": 100,
        "satiety": 100,
        "mood": "calm",
        "money": 1000,
        "phone_battery": 100,
        "social_energy": 100,
    }


def _make_action(action_id: str, scene_tags: list[str]) -> Action:
    return Action(
        id=action_id,
        name=f"动作-{action_id}",
        category=ActionCategory.LIFE,
        scene_tags=scene_tags,
    )


# 改动前各 Action 的硬编码场景集，用于断言解析后逐字等价
_TAGGED_EXPECTED_SCENES: dict[str, set[str]] = {
    "sleep": {"home"},
    "eat_at_home": {"home"},
    "charge_phone": {"home"},
    "read_book": {"home", "library", "bookstore"},
    "work_parttime_cafe": {"cafe"},
    "work_parttime_store": {"convenience_store"},
    "study": {"school", "library", "home"},
}


# ---------------------------------------------------------------------------
# 注册期标签解析
# ---------------------------------------------------------------------------


def test_register_scene_tags_resolves_to_expected_scene_sets() -> None:
    """注册全部内置 Action 后，scene_tags 解析结果与改动前硬编码场景集逐字等价"""
    loader = _real_loader()
    reg = ActionRegistry(scene_loader=loader)
    register_all(reg)

    all_scenes = set(loader.get_all_scenes().keys())
    for action_id, expected in _TAGGED_EXPECTED_SCENES.items():
        for scene in sorted(all_scenes):
            candidates = {a.id for a in reg.get_candidates(_state(scene))}
            assert (action_id in candidates) is (scene in expected), f"{action_id} 在场景 {scene} 的候选结果不符"


def test_register_unknown_tag_fails_fast() -> None:
    """标签未命中任何场景时注册直接抛错，避免 scenes.yaml 增删场景静默破坏 Action"""
    loader = _real_loader()
    reg = ActionRegistry(scene_loader=loader)
    with pytest.raises(ValueError, match="未匹配任何场景"):
        reg.register(_make_action("bad_tag", ["no_such_tag"]))


def test_register_scene_tags_without_loader_fails() -> None:
    """使用 scene_tags 但未注入 SceneLoader 时注册直接抛错（无法解析标签）"""
    reg = ActionRegistry()
    with pytest.raises(ValueError, match="未注入 SceneLoader"):
        reg.register(_make_action("no_loader", ["residential"]))


# ---------------------------------------------------------------------------
# 候选过滤：scene_tags 与 scene 字段共存
# ---------------------------------------------------------------------------


def test_get_candidates_filters_by_resolved_scene_tags() -> None:
    """候选过滤按解析后的场景集合做成员判断"""
    loader = cast(SceneLoader, _FakeLoader({"residential": {"home"}, "dining": {"cafe"}}))
    reg = ActionRegistry(scene_loader=loader)
    reg.register(_make_action("sleep", ["residential"]))
    reg.register(_make_action("work", ["dining"]))

    home = {a.id for a in reg.get_candidates(_state("home"))}
    cafe = {a.id for a in reg.get_candidates(_state("cafe"))}
    assert "sleep" in home and "work" not in home
    assert "work" in cafe and "sleep" not in cafe


# ---------------------------------------------------------------------------
# SceneLoader 标签查询
# ---------------------------------------------------------------------------


def test_get_scene_ids_by_tag() -> None:
    """标签→场景 ID 集合查询：命中、未命中与多场景标签"""
    loader = _real_loader()
    assert loader.get_scene_ids_by_tag("residential") == frozenset({"home"})
    assert loader.get_scene_ids_by_tag("dining") == frozenset({"cafe"})
    assert loader.get_scene_ids_by_tag("indoor") == frozenset(
        {"home", "school", "cafe", "bookstore", "library", "convenience_store"}
    )
    assert loader.get_scene_ids_by_tag("no_such_tag") == frozenset()
