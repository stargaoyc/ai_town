"""社交类 Action

包含与他人聊天与等待等社交行为。

- chat_with: 与指定角色聊天，消耗社交能量；target_character_id 由执行层用于更新关系。
- wait: 默认 Action，始终可用，无状态变化。
"""

from src.actions.base import Action, ActionCategory


def build_social_actions() -> list[Action]:
    """构造所有社交类 Action"""
    return [
        Action(
            id="chat_with",
            name="聊天",
            category=ActionCategory.SOCIAL,
            scene=None,  # 任意场景
            duration_minutes=30,
            social_cost=-10,  # 消耗 10 社交能量
            # 前置条件：社交能量充足
            precondition=lambda s: s.get("social_energy", 0) >= 10,
            executor=None,  # 社交能量变化由 social_cost 应用；关系更新由执行层处理
            params_schema={
                "type": "object",
                "properties": {
                    "target_character_id": {
                        "type": "string",
                        "description": "对话目标角色 ID",
                    }
                },
                "required": ["target_character_id"],
            },
        ),
        Action(
            id="wait",
            name="等待",
            category=ActionCategory.SOCIAL,
            scene=None,  # 任意场景
            duration_minutes=10,
            # 默认 Action，永远可用，无状态变化
            precondition=None,
            executor=None,
        ),
        Action(
            id="group_activity",
            name="群聚",
            category=ActionCategory.SOCIAL,
            scene=None,  # 任意场景
            duration_minutes=45,
            energy_cost=-3,  # 活动消耗体力
            social_cost=8,  # 群体交流恢复社交能量
            # 前置条件：有基本体力参与集体活动；
            # 真实人数门槛（同场景 >=2 名其他角色）由 Tick 侧候选过滤执行——
            # precondition 仅见 state 字典，拿不到在场名单
            precondition=lambda s: s.get("stamina", 0) >= 15,
            executor=None,
        ),
    ]
