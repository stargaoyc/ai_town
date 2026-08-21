"""Action 注册表

集中管理所有 Action 的注册、注销、查询与候选过滤。

候选过滤逻辑（get_candidates）：
1. precondition 返回 True（代码级前置条件）
2. 场景匹配：若 Action 指定了 scene，必须等于角色当前 location（或传入的 scene）
3. 资源检查：当前状态足以承担 Action 的消耗（体力/饱腹度/社交能量/手机电量/金钱）
"""

from typing import Any

from structlog import get_logger

from src.actions.base import Action

logger = get_logger()


class ActionRegistry:
    """Action 注册表"""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, action: Action) -> None:
        """注册一个 Action；重复 ID 将覆盖并记录警告"""
        if action.id in self._actions:
            logger.warning("action_overridden", action_id=action.id)
        self._actions[action.id] = action
        logger.info("action_registered", action_id=action.id, category=action.category.value)

    def unregister(self, action_id: str) -> None:
        """注销一个 Action"""
        if action_id in self._actions:
            del self._actions[action_id]
            logger.info("action_unregistered", action_id=action_id)

    def get(self, action_id: str) -> Action | None:
        """根据 ID 获取 Action"""
        return self._actions.get(action_id)

    def list_all(self) -> list[Action]:
        """列出所有已注册的 Action"""
        return list(self._actions.values())

    def get_candidates(self, state: dict[str, Any], scene: str | None = None) -> list[Action]:
        """获取当前可执行的候选 Action 列表

        Args:
            state: 角色当前状态字典（包含 location / stamina / satiety / mood /
                money / phone_battery / social_energy / current_action 等）。
            scene: 当前场景；若为 None，则从 state["location"] 推断。

        Returns:
            满足前置条件、场景匹配且资源充足的 Action 列表。
        """
        # Redis/JSON 反序列化后数值字段可能为字符串，统一转为 int
        _NUMERIC_FIELDS = {
            "stamina",
            "satiety",
            "social_energy",
            "phone_battery",
            "money",
            "energy",
            "hunger",
        }
        state = {k: int(v) if k in _NUMERIC_FIELDS and isinstance(v, (str, float)) else v for k, v in state.items()}

        current_scene = scene if scene is not None else state.get("location")
        candidates: list[Action] = []

        for action in self._actions.values():
            # 1. 前置条件
            if action.precondition is not None and not action.precondition(state):
                continue
            # 2. 场景匹配
            if action.scene is not None and action.scene != current_scene:
                continue
            # 3. 资源检查
            if not self._has_enough_resources(action, state):
                continue
            candidates.append(action)

        return candidates

    @staticmethod
    def _has_enough_resources(action: Action, state: dict[str, Any]) -> bool:
        """检查当前状态是否足以承担 Action 的各项消耗"""
        # 体力消耗（energy_cost < 0 表示消耗）
        if action.energy_cost < 0 and int(state.get("stamina", 0)) < -action.energy_cost:
            return False
        # 饱腹度消耗
        if action.satiety_cost < 0 and int(state.get("satiety", 0)) < -action.satiety_cost:
            return False
        # 社交能量消耗
        if action.social_cost < 0 and int(state.get("social_energy", 0)) < -action.social_cost:
            return False
        # 手机电量消耗
        if action.phone_battery_cost < 0 and int(state.get("phone_battery", 0)) < -action.phone_battery_cost:
            return False
        # 金钱消耗（money_cost 为正数表示花费）
        if action.money_cost > 0 and int(state.get("money", 0)) < action.money_cost:
            return False
        return True
