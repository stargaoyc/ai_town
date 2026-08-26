"""工作类 Action

包含打工与学习等生产行为。

这些 Action 的效果完全由 cost 字段描述（executor=None），执行层通过
apply_cost_fields 应用默认状态更新。
"""

from src.actions.base import Action, ActionCategory


def build_work_actions() -> list[Action]:
    """构造所有工作类 Action"""
    return [
        Action(
            id="work_parttime_cafe",
            name="咖啡店打工",
            category=ActionCategory.WORK,
            scene_tags=["dining"],
            duration_minutes=120,
            money_gain=300,  # 获得 300 金钱
            energy_cost=-20,  # 消耗 20 体力
            satiety_cost=-10,  # 消耗 10 饱腹度
        ),
        Action(
            id="work_parttime_store",
            name="便利店打工",
            category=ActionCategory.WORK,
            scene_tags=["retail"],
            duration_minutes=120,
            money_gain=250,  # 获得 250 金钱
            energy_cost=-15,  # 消耗 15 体力
        ),
        Action(
            id="study",
            name="学习",
            category=ActionCategory.WORK,
            # 场景限制由 scene_tags 解析（education ∪ reading ∪ residential = {school, library, home}，
            # 不含 bookstore），替代原 precondition 中对 location 的硬编码场景集判断
            scene_tags=["education", "reading", "residential"],
            duration_minutes=90,
            energy_cost=-15,  # 消耗 15 体力
        ),
    ]
