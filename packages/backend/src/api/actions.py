"""Action 相关 API 路由

包含：
- Action 列表查询
- 单个 Action 详情查询

业务编排见 src/services/action_service.py（P-2：内联编排下沉 Service）。
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from src.runtime import get_registry
from src.schemas.api_out import ActionDefOut, ActionDefsListOut
from src.services.action_service import ActionService

router = APIRouter(prefix="/api/v1", tags=["actions"])


@router.get("/actions", response_model=ActionDefsListOut)
async def list_actions() -> dict[str, Any]:
    """获取所有 Action

    Returns:
        所有已注册的 Action 列表
    """
    registry = get_registry()
    if not registry:
        raise HTTPException(status_code=503, detail="Action registry not initialized")

    actions = ActionService(registry).list_actions()
    return {"data": actions, "total": len(actions)}


@router.get("/actions/{action_id}", response_model=ActionDefOut)
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

    action = ActionService(registry).get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action
