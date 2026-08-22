"""通知中心 API 路由"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from structlog import get_logger

from src.auth import get_current_user
from src.runtime import create_notification as create_notification_record
from src.runtime import get_redis, notification_key

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])
logger = get_logger(__name__)

# 依赖类型别名（规避 B008：不在函数默认参数中调用 Depends/Body）
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
BodyDict = Annotated[dict[str, Any], Body(...)]


@router.get("")
async def list_notifications(
    user: CurrentUser,
    limit: int = 50,
    unread_only: bool = False,
) -> dict[str, Any]:
    """获取通知列表

    Args:
        limit: 返回数量（最大 200）
        unread_only: 仅返回未读通知

    Returns:
        通知列表（按时间倒序，最新的在前）
    """
    user_id = user["user_id"]
    limit = min(max(limit, 1), 200)
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(notification_key(user_id), 0, limit - 1)

    notifications = []
    for raw in raw_list:
        try:
            notif = json.loads(raw)
            if unread_only and notif.get("read"):
                continue
            notifications.append(notif)
        except (json.JSONDecodeError, TypeError):
            continue

    unread_count = sum(1 for n in notifications if not n.get("read"))
    return {
        "data": notifications,
        "total": len(notifications),
        "unread": unread_count,
    }


@router.post("")
async def create_notification(
    payload: BodyDict,
    user: CurrentUser,
) -> dict[str, Any]:
    """手动创建通知（前端"模拟通知"按钮调用）

    Body:
        type: 通知类型 (share/system/character/qq)
        title: 标题
        content: 内容
    """
    user_id = user["user_id"]
    notif_type = payload.get("type", "system")
    title = payload.get("title", "通知")
    content = payload.get("content", "")

    notif = await create_notification_record(user_id=user_id, notif_type=notif_type, title=title, content=content)
    return {"data": notif}


@router.put("/{notif_id}/read")
async def mark_notification_read(
    notif_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """标记单条通知为已读"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(notification_key(user_id), 0, -1)
    for i, raw in enumerate(raw_list):
        try:
            notif = json.loads(raw)
            if notif.get("id") == notif_id:
                notif["read"] = True
                await redis.lset(notification_key(user_id), i, json.dumps(notif))
                return {"success": True, "id": notif_id}
        except (json.JSONDecodeError, TypeError):
            continue

    raise HTTPException(404, f"Notification {notif_id} not found")


@router.put("/read-all")
async def mark_all_notifications_read(
    user: CurrentUser,
) -> dict[str, Any]:
    """标记所有通知为已读"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(notification_key(user_id), 0, -1)
    updated = 0
    for i, raw in enumerate(raw_list):
        try:
            notif = json.loads(raw)
            if not notif.get("read"):
                notif["read"] = True
                await redis.lset(notification_key(user_id), i, json.dumps(notif))
                updated += 1
        except (json.JSONDecodeError, TypeError):
            continue

    return {"success": True, "updated": updated}


@router.delete("/{notif_id}")
async def delete_notification(
    notif_id: str,
    user: CurrentUser,
) -> dict[str, Any]:
    """删除单条通知"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(notification_key(user_id), 0, -1)
    for raw in raw_list:
        try:
            notif = json.loads(raw)
            if notif.get("id") == notif_id:
                # LREM 按 value 删除（需要精确匹配原始 JSON 字符串）
                await redis.lrem(notification_key(user_id), 1, raw.decode() if isinstance(raw, bytes) else raw)
                return {"success": True, "id": notif_id}
        except (json.JSONDecodeError, TypeError):
            continue

    raise HTTPException(404, f"Notification {notif_id} not found")


@router.delete("")
async def clear_all_notifications(
    user: CurrentUser,
) -> dict[str, Any]:
    """清除所有通知"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    await redis.delete(notification_key(user_id))
    return {"success": True}
