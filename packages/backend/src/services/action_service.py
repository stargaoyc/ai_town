"""Action 域服务：Action 注册表查询编排（P-2：api/actions.py 内联编排下沉）

将 registry 查询 + 响应序列化从路由层下沉。无 HTTP 概念。
"""

from __future__ import annotations

from typing import Any

from src.actions import ActionRegistry

# Action 列表项字段（列表与详情共用序列化）
_LIST_FIELDS = ("id", "name", "description", "category", "duration_minutes", "energy_cost")


class ActionService:
    """Action 域服务：注册表查询 + 响应序列化"""

    def __init__(self, registry: ActionRegistry | None):
        self._registry = registry

    def list_actions(self) -> list[dict[str, Any]]:
        """全部已注册 Action（列表形态）"""
        if self._registry is None:
            return []
        return [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "category": a.category.value if hasattr(a.category, "value") else str(a.category),
                "duration_minutes": a.duration_minutes,
                "energy_cost": a.energy_cost,
            }
            for a in self._registry.list_all()
        ]

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        """单个 Action 详情；不存在返回 None（404 语义由路由层决定）"""
        if self._registry is None:
            return None
        action = self._registry.get(action_id)
        if not action:
            return None
        return {
            "id": action.id,
            "name": action.name,
            "description": action.description,
            "category": action.category.value if hasattr(action.category, "value") else str(action.category),
            "scene": action.scene,
            "duration_minutes": action.duration_minutes,
            "allow_dynamic_duration": action.allow_dynamic_duration,
            "energy_cost": action.energy_cost,
            "satiety_cost": action.satiety_cost,
            "social_cost": action.social_cost,
            "money_cost": action.money_cost,
            "money_gain": action.money_gain,
            "phone_battery_cost": action.phone_battery_cost,
        }
