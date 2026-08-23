"""Action 相关 API 路由

包含：
- Action 列表查询
- 单个 Action 详情查询
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from src.runtime import get_registry

router = APIRouter(prefix="/api/v1", tags=["actions"])


@router.get("/actions")
async def list_actions() -> dict[str, Any]:
    """获取所有 Action

    Returns:
        所有已注册的 Action 列表
    """
    registry = get_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Action registry not initialized")

    actions = registry.list_all()
    return {
        "data": [
            {
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "category": a.category.value if hasattr(a.category, "value") else str(a.category),
                "duration_minutes": a.duration_minutes,
                "energy_cost": a.energy_cost,
            }
            for a in actions
        ],
        "total": len(actions),
    }


@router.get("/actions/{action_id}")
async def get_action(action_id: str) -> dict[str, Any]:
    """获取单个 Action 详情

    Args:
        action_id: Action ID

    Returns:
        Action 详情
    """
    registry = get_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Action registry not initialized")

    action = registry.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

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
