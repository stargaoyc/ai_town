"""生活类 Action

包含睡眠、进食、休息、阅读、手机、充电等生理与日常行为。

这些 Action 的效果完全由 cost 字段描述（executor=None），执行层通过
apply_cost_fields 应用默认状态更新。
"""

from src.actions.base import Action, ActionCategory


def build_life_actions() -> list[Action]:
    """构造所有生活类 Action"""
    return [
        Action(
            id="sleep",
            name="睡觉",
            category=ActionCategory.LIFE,
            scene="home",
            duration_minutes=480,
            energy_cost=40,  # 恢复 40 体力
            satiety_cost=-10,  # 消耗 10 饱腹度
            social_cost=10,  # 独处恢复社交能量
        ),
        Action(
            id="eat",
            name="吃饭",
            category=ActionCategory.LIFE,
            scene=None,  # 任意场景
            duration_minutes=30,
            satiety_cost=30,  # 恢复 30 饱腹度
            money_cost=50,  # 花费 50
        ),
        Action(
            id="eat_at_home",
            name="在家吃饭",
            category=ActionCategory.LIFE,
            scene="home",
            duration_minutes=30,
            satiety_cost=25,  # 恢复 25 饱腹度
            money_cost=20,  # 花费 20
        ),
        Action(
            id="relax",
            name="休息",
            category=ActionCategory.LIFE,
            scene=None,  # 任意场景
            duration_minutes=30,
            energy_cost=15,  # 恢复 15 体力
            social_cost=10,  # 独处恢复社交能量
        ),
        Action(
            id="read_book",
            name="读书",
            category=ActionCategory.LIFE,
            scene=None,  # 多场景限制通过 precondition 实现
            duration_minutes=60,
            energy_cost=-5,  # 消耗 5 体力
            precondition=lambda s: s.get("location") in {"home", "library", "bookstore"},
            social_cost=10,  # 独处恢复社交能量
        ),
        Action(
            id="use_phone",
            name="玩手机",
            category=ActionCategory.LIFE,
            scene=None,  # 任意场景
            duration_minutes=30,
            phone_battery_cost=-15,  # 消耗 15 手机电量
            social_cost=10,  # 恢复 10 社交能量
        ),
        Action(
            id="charge_phone",
            name="给手机充电",
            category=ActionCategory.LIFE,
            scene="home",
            duration_minutes=30,
            phone_battery_cost=50,  # 恢复 50 手机电量
        ),
    ]
