"""Action 注册表

集中管理所有 Action 的注册、注销、查询与候选过滤。

候选过滤逻辑（get_candidates）：
1. precondition 返回 True（代码级前置条件）
2. 场景匹配：若 Action 指定了 scene 或 scene_tags，必须等于角色当前 location
3. 资源检查：当前状态足以承担 Action 的消耗（体力/饱腹度/社交能量/手机电量/金钱）

scene_tags 解析：
- 注册时由 SceneLoader.get_scene_ids_by_tag 将标签转为具体场景 ID 集。
- 标签未命中任何场景时注册失败（fail-fast），避免 scenes.yaml 增删场景静默破坏 Action。
"""

from typing import Any

from structlog import get_logger

from src.actions.base import Action
from src.modules.town.loader import SceneLoader

logger = get_logger()


class ActionRegistry:
    """Action 注册表"""

    def __init__(self, scene_loader: SceneLoader | None = None) -> None:
        self._actions: dict[str, Action] = {}
        self._scene_loader = scene_loader
        # action_id → 该 Action 的 scene_tags 解析后的具体场景 ID 集合
        self._resolved_scene_ids: dict[str, frozenset[str]] = {}

    def set_scene_loader(self, scene_loader: SceneLoader) -> None:
        """在注册后注入 SceneLoader（用于场景标签解析）"""
        self._scene_loader = scene_loader

    def register(self, action: Action) -> None:
        """注册一个 Action；重复 ID 将覆盖并记录警告

        若 Action 声明了 scene_tags，在注册时解析为具体场景 ID 集。
        标签未命中任何场景或未注入 SceneLoader 时抛出 ValueError（fail-fast）。
        """
        if action.id in self._actions:
            logger.warning("action_overridden", action_id=action.id)
        if action.scene_tags:
            resolved = self._resolve_scene_tags(action)
            self._resolved_scene_ids[action.id] = resolved
        else:
            self._resolved_scene_ids.pop(action.id, None)
        self._actions[action.id] = action
        logger.info("action_registered", action_id=action.id, category=action.category.value)

    def _resolve_scene_tags(self, action: Action) -> frozenset[str]:
        """将 Action 的 scene_tags 解析为具体场景 ID 集合"""
        if self._scene_loader is None:
            raise ValueError(
                f"Action '{action.id}' 声明了 scene_tags={action.scene_tags}，"
                "但 ActionRegistry 未注入 SceneLoader（无法解析标签）"
            )
        resolved: set[str] = set()
        for tag in action.scene_tags:
            ids = self._scene_loader.get_scene_ids_by_tag(tag)
            if not ids:
                raise ValueError(
                    f"Action '{action.id}' 的 scene_tags 包含标签 '{tag}'，"
                    "该标签在 configs/scenes.yaml 中未匹配任何场景"
                )
            resolved.update(ids)
        return frozenset(resolved)

    def unregister(self, action_id: str) -> None:
        """注销一个 Action"""
        if action_id in self._actions:
            del self._actions[action_id]
            self._resolved_scene_ids.pop(action_id, None)
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
            # 2. 场景匹配：scene 字段（单场景）或 scene_tags 解析出的场景集合
            if action.scene is not None and action.scene != current_scene:
                continue
            resolved = self._resolved_scene_ids.get(action.id)
            if resolved is not None and current_scene not in resolved:
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
