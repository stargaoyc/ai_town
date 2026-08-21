"""src/actions/registry.py 单元测试

覆盖 ActionRegistry 的注册/注销/查询/候选过滤/资源检查逻辑。
"""

from collections.abc import Callable
from typing import Any

from src.actions.base import Action, ActionCategory
from src.actions.registry import ActionRegistry


def _make_action(
    action_id: str = "a1",
    *,
    scene: str | None = None,
    energy_cost: int = 0,
    satiety_cost: int = 0,
    social_cost: int = 0,
    phone_battery_cost: int = 0,
    money_cost: int = 0,
    precondition: Callable[..., bool] | None = None,
) -> Action:
    return Action(
        id=action_id,
        name=f"动作-{action_id}",
        category=ActionCategory.LIFE,
        scene=scene,
        energy_cost=energy_cost,
        satiety_cost=satiety_cost,
        social_cost=social_cost,
        phone_battery_cost=phone_battery_cost,
        money_cost=money_cost,
        precondition=precondition,
    )


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def test_register_success() -> None:
    """注册成功后可通过 get 获取"""
    reg = ActionRegistry()
    action = _make_action("a1")
    reg.register(action)
    assert reg.get("a1") is action


def test_register_duplicate_overrides() -> None:
    """重复注册相同 ID 会覆盖旧 Action"""
    reg = ActionRegistry()
    old = _make_action("a1", energy_cost=-10)
    new = _make_action("a1", energy_cost=20)
    reg.register(old)
    reg.register(new)
    assert reg.get("a1") is new
    assert reg.get("a1").energy_cost == 20  # type: ignore[union-attr]
    # 覆盖后总数仍为 1
    assert len(reg.list_all()) == 1


# ---------------------------------------------------------------------------
# unregister
# ---------------------------------------------------------------------------


def test_unregister_existing() -> None:
    """注销已存在的 Action 后 get 返回 None"""
    reg = ActionRegistry()
    reg.register(_make_action("a1"))
    reg.unregister("a1")
    assert reg.get("a1") is None
    assert len(reg.list_all()) == 0


def test_unregister_non_existing_is_noop() -> None:
    """注销不存在的 ID 不抛异常且不影响其他 Action"""
    reg = ActionRegistry()
    reg.register(_make_action("a1"))
    reg.unregister("not_exist")
    assert reg.get("a1") is not None
    assert len(reg.list_all()) == 1


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_existing_returns_action() -> None:
    reg = ActionRegistry()
    action = _make_action("a1")
    reg.register(action)
    assert reg.get("a1") is action


def test_get_non_existing_returns_none() -> None:
    reg = ActionRegistry()
    assert reg.get("nope") is None


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_empty() -> None:
    reg = ActionRegistry()
    assert reg.list_all() == []


def test_list_all_returns_all_registered() -> None:
    reg = ActionRegistry()
    reg.register(_make_action("a1"))
    reg.register(_make_action("a2"))
    reg.register(_make_action("a3"))
    ids = {a.id for a in reg.list_all()}
    assert ids == {"a1", "a2", "a3"}


# ---------------------------------------------------------------------------
# get_candidates - 前置条件 / 场景匹配 / 资源检查
# ---------------------------------------------------------------------------


def test_get_candidates_precondition_filter_pass() -> None:
    """precondition 返回 True 时 Action 进入候选"""
    reg = ActionRegistry()
    reg.register(_make_action("a1", precondition=lambda s: s.get("stamina") > 50))
    state = {"stamina": 80, "location": "home"}
    candidates = reg.get_candidates(state)
    assert [a.id for a in candidates] == ["a1"]


def test_get_candidates_precondition_filter_fail() -> None:
    """precondition 返回 False 时 Action 被过滤"""
    reg = ActionRegistry()
    reg.register(_make_action("a1", precondition=lambda s: s.get("stamina") > 50))
    state = {"stamina": 10, "location": "home"}
    candidates = reg.get_candidates(state)
    assert candidates == []


def test_get_candidates_precondition_none_always_pass() -> None:
    """precondition 为 None 时不受前置条件限制"""
    reg = ActionRegistry()
    reg.register(_make_action("a1", precondition=None))
    state = {"stamina": 0, "location": "home"}
    candidates = reg.get_candidates(state)
    assert len(candidates) == 1


def test_get_candidates_scene_match_from_location() -> None:
    """Action 指定 scene 时需与 state['location'] 匹配"""
    reg = ActionRegistry()
    reg.register(_make_action("home_action", scene="home"))
    reg.register(_make_action("cafe_action", scene="cafe"))
    state = {"location": "home", "stamina": 50}
    candidates = reg.get_candidates(state)
    ids = [a.id for a in candidates]
    assert ids == ["home_action"]


def test_get_candidates_scene_match_explicit_scene_param() -> None:
    """显式传入 scene 参数覆盖 state['location']"""
    reg = ActionRegistry()
    reg.register(_make_action("home_action", scene="home"))
    reg.register(_make_action("cafe_action", scene="cafe"))
    state = {"location": "home", "stamina": 50}
    candidates = reg.get_candidates(state, scene="cafe")
    ids = [a.id for a in candidates]
    assert ids == ["cafe_action"]


def test_get_candidates_scene_none_matches_anywhere() -> None:
    """scene 为 None 的 Action 在任意场景均为候选"""
    reg = ActionRegistry()
    reg.register(_make_action("anywhere", scene=None))
    state = {"location": "cafe", "stamina": 50}
    candidates = reg.get_candidates(state)
    assert len(candidates) == 1


def test_get_candidates_resource_check_stamina() -> None:
    """体力不足时 Action 被过滤"""
    reg = ActionRegistry()
    reg.register(_make_action("costly", energy_cost=-50))
    # 体力充足
    ok = reg.get_candidates({"stamina": 60, "location": "home"})
    assert len(ok) == 1
    # 体力不足
    fail = reg.get_candidates({"stamina": 30, "location": "home"})
    assert fail == []


def test_get_candidates_resource_check_money() -> None:
    """金钱不足时 Action 被过滤"""
    reg = ActionRegistry()
    reg.register(_make_action("expensive", money_cost=100))
    ok = reg.get_candidates({"money": 200, "location": "home"})
    assert len(ok) == 1
    fail = reg.get_candidates({"money": 50, "location": "home"})
    assert fail == []


def test_get_candidates_combined_filters() -> None:
    """前置条件 + 场景 + 资源三个过滤条件同时生效"""
    reg = ActionRegistry()
    reg.register(
        _make_action(
            "sleep",
            scene="home",
            energy_cost=-20,
            precondition=lambda s: s.get("mood") != "angry",
        )
    )
    reg.register(
        _make_action(
            "eat",
            scene="home",
            money_cost=10,
        )
    )
    # 满足全部条件
    ok = reg.get_candidates({"location": "home", "stamina": 50, "money": 100, "mood": "calm"})
    assert {a.id for a in ok} == {"sleep", "eat"}
    # mood=angry 导致 sleep 被前置条件过滤
    angry = reg.get_candidates({"location": "home", "stamina": 50, "money": 100, "mood": "angry"})
    assert {a.id for a in angry} == {"eat"}
    # 体力不足导致 sleep 被资源过滤
    tired = reg.get_candidates({"location": "home", "stamina": 10, "money": 100, "mood": "calm"})
    assert {a.id for a in tired} == {"eat"}
    # 场景不匹配
    elsewhere = reg.get_candidates({"location": "cafe", "stamina": 50, "money": 100, "mood": "calm"})
    assert elsewhere == []


# ---------------------------------------------------------------------------
# _has_enough_resources
# ---------------------------------------------------------------------------


def test_has_enough_resources_stamina_insufficient() -> None:
    """体力不足返回 False"""
    action = _make_action(energy_cost=-30)
    state = {"stamina": 20}
    assert ActionRegistry._has_enough_resources(action, state) is False


def test_has_enough_resources_stamina_sufficient() -> None:
    """体力刚好等于消耗量时返回 True"""
    action = _make_action(energy_cost=-30)
    state = {"stamina": 30}
    assert ActionRegistry._has_enough_resources(action, state) is True


def test_has_enough_resources_satiety_insufficient() -> None:
    """饱腹度不足返回 False"""
    action = _make_action(satiety_cost=-20)
    state = {"satiety": 10}
    assert ActionRegistry._has_enough_resources(action, state) is False


def test_has_enough_resources_social_insufficient() -> None:
    """社交能量不足返回 False"""
    action = _make_action(social_cost=-25)
    state = {"social_energy": 10}
    assert ActionRegistry._has_enough_resources(action, state) is False


def test_has_enough_resources_phone_battery_insufficient() -> None:
    """手机电量不足返回 False"""
    action = _make_action(phone_battery_cost=-30)
    state = {"phone_battery": 20}
    assert ActionRegistry._has_enough_resources(action, state) is False


def test_has_enough_resources_money_insufficient() -> None:
    """金钱不足返回 False"""
    action = _make_action(money_cost=100)
    state = {"money": 50}
    assert ActionRegistry._has_enough_resources(action, state) is False


def test_has_enough_resources_all_sufficient() -> None:
    """所有资源充足时返回 True"""
    action = _make_action(
        energy_cost=-10,
        satiety_cost=-10,
        social_cost=-10,
        phone_battery_cost=-10,
        money_cost=50,
    )
    state = {"stamina": 100, "satiety": 100, "social_energy": 100, "phone_battery": 100, "money": 500}
    assert ActionRegistry._has_enough_resources(action, state) is True


def test_has_enough_resources_recovery_always_passes() -> None:
    """恢复型 cost（正值）不触发资源检查，始终返回 True"""
    action = _make_action(
        energy_cost=20,
        satiety_cost=30,
        social_cost=10,
        phone_battery_cost=50,
    )
    state = {"stamina": 0, "satiety": 0, "social_energy": 0, "phone_battery": 0}
    assert ActionRegistry._has_enough_resources(action, state) is True


def test_has_enough_resources_no_cost_passes() -> None:
    """无任何消耗时返回 True"""
    action = _make_action()
    state: dict[str, Any] = {}
    assert ActionRegistry._has_enough_resources(action, state) is True
