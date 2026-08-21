"""src/actions/base.py 单元测试

覆盖 clamp_resource 与 apply_cost_fields 的边界值与组合场景。
"""

from typing import Any

from src.actions.base import Action, ActionCategory, apply_cost_fields, clamp_resource

# ---------------------------------------------------------------------------
# clamp_resource
# ---------------------------------------------------------------------------


class _ActionBuilder:
    """便捷构造 Action 的辅助类，减少重复样板"""

    @staticmethod
    def make(**kwargs: Any) -> Action:
        defaults: dict[str, Any] = {
            "id": "test_action",
            "name": "测试动作",
            "category": ActionCategory.LIFE,
        }
        defaults.update(kwargs)
        return Action(**defaults)


def test_clamp_resource_within_range() -> None:
    """区间内的值原样返回"""
    assert clamp_resource(0) == 0
    assert clamp_resource(50) == 50
    assert clamp_resource(100) == 100


def test_clamp_resource_below_min() -> None:
    """低于下界的值被截断到 0"""
    assert clamp_resource(-1) == 0
    assert clamp_resource(-100) == 0


def test_clamp_resource_above_max() -> None:
    """高于上界的值被截断到 100"""
    assert clamp_resource(101) == 100
    assert clamp_resource(999) == 100


def test_clamp_resource_custom_bounds() -> None:
    """自定义 lo/hi 区间"""
    assert clamp_resource(50, lo=10, hi=90) == 50
    assert clamp_resource(5, lo=10, hi=90) == 10
    assert clamp_resource(95, lo=10, hi=90) == 90


def test_clamp_resource_boundary_exact() -> None:
    """恰好等于边界值"""
    assert clamp_resource(0) == 0
    assert clamp_resource(100) == 100


# ---------------------------------------------------------------------------
# apply_cost_fields - energy_cost
# ---------------------------------------------------------------------------


def test_apply_cost_energy_recovery() -> None:
    """energy_cost 为正：体力恢复"""
    state = {"stamina": 50}
    action = _ActionBuilder.make(energy_cost=20)
    changes = apply_cost_fields(state, action)
    assert changes == {"stamina": 70}


def test_apply_cost_energy_recovery_clamped_to_max() -> None:
    """体力恢复不超过 100"""
    state = {"stamina": 90}
    action = _ActionBuilder.make(energy_cost=30)
    changes = apply_cost_fields(state, action)
    assert changes["stamina"] == 100


def test_apply_cost_energy_consumption() -> None:
    """energy_cost 为负：体力消耗"""
    state = {"stamina": 50}
    action = _ActionBuilder.make(energy_cost=-20)
    changes = apply_cost_fields(state, action)
    assert changes["stamina"] == 30


def test_apply_cost_energy_consumption_clamped_to_zero() -> None:
    """体力消耗不低于 0"""
    state = {"stamina": 10}
    action = _ActionBuilder.make(energy_cost=-50)
    changes = apply_cost_fields(state, action)
    assert changes["stamina"] == 0


# ---------------------------------------------------------------------------
# apply_cost_fields - satiety_cost
# ---------------------------------------------------------------------------


def test_apply_cost_satiety_recovery() -> None:
    """satiety_cost 为正：饱腹度恢复"""
    state = {"satiety": 40}
    action = _ActionBuilder.make(satiety_cost=30)
    changes = apply_cost_fields(state, action)
    assert changes["satiety"] == 70


def test_apply_cost_satiety_consumption_clamped() -> None:
    """饱腹度消耗 clamp 到 0"""
    state = {"satiety": 5}
    action = _ActionBuilder.make(satiety_cost=-20)
    changes = apply_cost_fields(state, action)
    assert changes["satiety"] == 0


# ---------------------------------------------------------------------------
# apply_cost_fields - social_cost
# ---------------------------------------------------------------------------


def test_apply_cost_social_recovery() -> None:
    """social_cost 为正：社交能量恢复"""
    state = {"social_energy": 30}
    action = _ActionBuilder.make(social_cost=20)
    changes = apply_cost_fields(state, action)
    assert changes["social_energy"] == 50


def test_apply_cost_social_consumption() -> None:
    """social_cost 为负：社交能量消耗"""
    state = {"social_energy": 60}
    action = _ActionBuilder.make(social_cost=-15)
    changes = apply_cost_fields(state, action)
    assert changes["social_energy"] == 45


# ---------------------------------------------------------------------------
# apply_cost_fields - phone_battery_cost
# ---------------------------------------------------------------------------


def test_apply_cost_phone_battery_consumption() -> None:
    """phone_battery_cost 为负：手机电量消耗"""
    state = {"phone_battery": 80}
    action = _ActionBuilder.make(phone_battery_cost=-15)
    changes = apply_cost_fields(state, action)
    assert changes["phone_battery"] == 65


def test_apply_cost_phone_battery_recovery_clamped() -> None:
    """手机电量恢复 clamp 到 100"""
    state = {"phone_battery": 90}
    action = _ActionBuilder.make(phone_battery_cost=50)
    changes = apply_cost_fields(state, action)
    assert changes["phone_battery"] == 100


# ---------------------------------------------------------------------------
# apply_cost_fields - money_cost / money_gain
# ---------------------------------------------------------------------------


def test_apply_cost_money_cost_only() -> None:
    """仅 money_cost：金钱扣减"""
    state = {"money": 500}
    action = _ActionBuilder.make(money_cost=50)
    changes = apply_cost_fields(state, action)
    assert changes["money"] == 450


def test_apply_cost_money_gain_only() -> None:
    """仅 money_gain：金钱增加"""
    state = {"money": 500}
    action = _ActionBuilder.make(money_gain=200)
    changes = apply_cost_fields(state, action)
    assert changes["money"] == 700


def test_apply_cost_money_cost_and_gain() -> None:
    """money_cost 与 money_gain 同时存在：净效果"""
    state = {"money": 500}
    action = _ActionBuilder.make(money_cost=50, money_gain=200)
    changes = apply_cost_fields(state, action)
    assert changes["money"] == 650


def test_apply_cost_money_zero_when_both_zero() -> None:
    """money_cost 与 money_gain 均为 0 时不产生 money 变更"""
    state = {"money": 500}
    action = _ActionBuilder.make(money_cost=0, money_gain=0)
    changes = apply_cost_fields(state, action)
    assert "money" not in changes


# ---------------------------------------------------------------------------
# apply_cost_fields - 组合 & 空场景
# ---------------------------------------------------------------------------


def test_apply_cost_multiple_fields_combined() -> None:
    """多个 cost 字段同时生效"""
    state = {"stamina": 50, "satiety": 60, "money": 500, "social_energy": 40}
    action = _ActionBuilder.make(
        energy_cost=20,
        satiety_cost=-10,
        social_cost=10,
        money_cost=30,
    )
    changes = apply_cost_fields(state, action)
    assert changes["stamina"] == 70
    assert changes["satiety"] == 50
    assert changes["social_energy"] == 50
    assert changes["money"] == 470


def test_apply_cost_no_cost_fields_returns_empty() -> None:
    """所有 cost 字段为默认 0 时返回空 dict"""
    state = {"stamina": 50, "money": 500}
    action = _ActionBuilder.make()
    changes = apply_cost_fields(state, action)
    assert changes == {}


def test_apply_cost_missing_state_field_defaults_to_zero() -> None:
    """state 缺失字段时按 0 处理"""
    state: dict[str, Any] = {}
    action = _ActionBuilder.make(energy_cost=30)
    changes = apply_cost_fields(state, action)
    assert changes["stamina"] == 30
