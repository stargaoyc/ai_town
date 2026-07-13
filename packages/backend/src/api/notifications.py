"""通知中心 API 路由"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from structlog import get_logger

from src.auth import get_current_user
from src.runtime import get_redis

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])
logger = get_logger(__name__)


def _notif_key(user_id: str) -> str:
    """Redis 通知列表键"""
    return f"notifications:{user_id}"


async def _create_notification(
    user_id: str,
    notif_type: str,
    title: str,
    content: str,
) -> dict:
    """创建通知并写入 Redis（内部函数，可被其他模块调用）"""
    from uuid6 import uuid7

    redis = get_redis()
    if redis is None:
        raise RuntimeError("Redis not initialized")

    notif = {
        "id": str(uuid7()),
        "type": notif_type,
        "title": title,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "read": False,
    }
    await redis.lpush(_notif_key(user_id), json.dumps(notif))
    # 保留最近 200 条
    await redis.ltrim(_notif_key(user_id), 0, 199)
    return notif


@router.get("")
async def list_notifications(
    limit: int = 50,
    unread_only: bool = False,
    user: dict = Depends(get_current_user),
):
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
    raw_list = await redis.lrange(_notif_key(user_id), 0, limit - 1)

    notifications = []
    for raw in raw_list:
        try:
            notif = json.loads(raw)  # type: ignore[arg-type]
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
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
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

    notif = await _create_notification(user_id, notif_type, title, content)
    return {"data": notif}


@router.put("/{notif_id}/read")
async def mark_notification_read(
    notif_id: str,
    user: dict = Depends(get_current_user),
):
    """标记单条通知为已读"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(_notif_key(user_id), 0, -1)
    for i, raw in enumerate(raw_list):
        try:
            notif = json.loads(raw)  # type: ignore[arg-type]
            if notif.get("id") == notif_id:
                notif["read"] = True
                await redis.lset(_notif_key(user_id), i, json.dumps(notif))
                return {"success": True, "id": notif_id}
        except (json.JSONDecodeError, TypeError):
            continue

    raise HTTPException(404, f"Notification {notif_id} not found")


@router.put("/read-all")
async def mark_all_notifications_read(
    user: dict = Depends(get_current_user),
):
    """标记所有通知为已读"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(_notif_key(user_id), 0, -1)
    updated = 0
    for i, raw in enumerate(raw_list):
        try:
            notif = json.loads(raw)  # type: ignore[arg-type]
            if not notif.get("read"):
                notif["read"] = True
                await redis.lset(_notif_key(user_id), i, json.dumps(notif))
                updated += 1
        except (json.JSONDecodeError, TypeError):
            continue

    return {"success": True, "updated": updated}


@router.delete("/{notif_id}")
async def delete_notification(
    notif_id: str,
    user: dict = Depends(get_current_user),
):
    """删除单条通知"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    raw_list = await redis.lrange(_notif_key(user_id), 0, -1)
    for raw in raw_list:
        try:
            notif = json.loads(raw)  # type: ignore[arg-type]
            if notif.get("id") == notif_id:
                # LREM 按 value 删除（需要精确匹配原始 JSON 字符串）
                await redis.lrem(_notif_key(user_id), 1, raw)  # type: ignore[arg-type]
                return {"success": True, "id": notif_id}
        except (json.JSONDecodeError, TypeError):
            continue

    raise HTTPException(404, f"Notification {notif_id} not found")


@router.delete("")
async def clear_all_notifications(
    user: dict = Depends(get_current_user),
):
    """清除所有通知"""
    user_id = user["user_id"]
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")
    await redis.delete(_notif_key(user_id))
    return {"success": True}
