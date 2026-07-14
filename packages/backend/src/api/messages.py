"""消息服务 API 路由

包含：
- 用户消息发送与角色回复
- 会话消息历史（游标分页）
- 会话列表查询
- 消息统计（token/cost 监控）
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from structlog import get_logger

from src.db.models import Character, Conversation, Message
from src.db.repositories import ConversationRepository, MessageRepository
from src.db.session import db
from src.messaging import MessageService
from src.runtime import get_llm, get_prompts, get_redis
from src.security.rate_limit_dep import rate_limit

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["messages"])


# === Phase 3 API：消息服务 ===


@router.post("/messages/send", dependencies=[Depends(rate_limit("msg_send", 60, 60))])
async def send_message(
    character_id: Annotated[str, Body(...)],
    user_id: Annotated[str, Body(...)],
    platform: str = "web",
    content: str = "",
):
    """发送消息给角色并获取回复

    Args:
        character_id: 角色 UUID
        user_id: 用户标识
        platform: 来源平台（web/qq/lark/internal）
        content: 用户消息内容

    Returns:
        角色回复内容与元数据（token/cost/conversation_id）
    """
    llm = get_llm()
    prompts = get_prompts()
    redis = get_redis()
    if not llm or not prompts:
        raise HTTPException(status_code=503, detail="LLM client not initialized")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    if not content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    if platform not in ("web", "qq", "lark", "internal"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform: {platform}",
        )

    async with db.session() as session:
        svc = MessageService(
            session=session,
            llm=llm,
            prompts=prompts,
            redis=redis,
        )
        try:
            result = await svc.handle_user_message(
                character_id=cid,
                user_id=user_id,
                platform=platform,
                content=content,
            )
        except Exception as e:
            logger.error(
                "message_handle_failed",
                character_id=character_id,
                user_id=user_id,
                error=str(e),
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Message handling failed: {str(e)}",
            ) from e

    return {
        "data": {
            "conversation_id": str(result["conversation_id"]),
            "message_id": str(result["message_id"]) if result["message_id"] else None,
            "content": result["content"],
            "tokens": result["tokens"],
            "cost": result["cost"],
            "error": result["error"],
        }
    }


@router.get("/messages/history")
async def get_message_history(
    conversation_id: str,
    limit: int = 50,
    before: str | None = None,
):
    """获取会话消息历史（支持游标分页）

    Args:
        conversation_id: 会话 UUID
        limit: 返回数量上限（默认 50）
        before: 游标时间（ISO 8601），仅返回该时间点之前的消息

    Returns:
        消息列表（按时间倒序）
    """
    try:
        conv_id = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    before_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid 'before' datetime format (use ISO 8601)",
            ) from None

    async with db.session() as session:
        repo = MessageRepository(session)
        messages = await repo.list_by_conversation(
            conversation_id=conv_id,
            limit=limit,
            before=before_dt,
            order_desc=True,
        )

    return {
        "data": [
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "tokens": m.tokens,
                "cost": float(m.cost) if m.cost else None,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "total": len(messages),
        "has_more": len(messages) == limit,
    }


@router.get("/conversations")
async def list_conversations(
    character_id: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
):
    """查询会话列表

    Args:
        character_id: 可选，按角色过滤
        user_id: 可选，按用户过滤
        limit: 返回数量上限

    Returns:
        会话列表（按 last_message_at 倒序）
    """
    async with db.session() as session:
        repo = ConversationRepository(session)

        if character_id and user_id:
            # 精确查询：单会话（仅查询不创建）
            try:
                cid = UUID(character_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format") from None
            conv = await repo.get_by_user_character(
                user_id=user_id,
                character_id=cid,
            )
            conversations = [conv] if conv else []
        elif character_id:
            try:
                cid = UUID(character_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format") from None
            conversations = await repo.list_by_character(cid, limit=limit)
        else:
            # 无过滤条件：返回所有会话（按 last_message_at 倒序）
            stmt = select(Conversation).order_by(Conversation.last_message_at.desc().nullslast()).limit(limit)
            result = await session.execute(stmt)
            conversations = list(result.scalars())

    return {
        "data": [
            {
                "id": str(c.id),
                "character_id": str(c.character_id),
                "user_id": c.user_id,
                "platform": c.platform,
                "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in conversations
        ],
        "total": len(conversations),
    }


@router.get("/messages/stats")
async def get_message_stats(
    character_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """获取消息统计（token/cost 累计，供成本监控）

    Args:
        character_id: 可选，按角色过滤
        start_date: 可选，起始日期（ISO 8601）
        end_date: 可选，结束日期（ISO 8601）

    Returns:
        累计消息数、token 数与 cost（USD），含按角色/按日期分组
    """
    async with db.session() as session:
        # 总计
        base = select(
            func.count(Message.id).label("total_messages"),
            func.coalesce(func.sum(Message.tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(Message.cost), 0).label("total_cost"),
        ).select_from(Message)
        if character_id:
            try:
                cid = UUID(character_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format") from None
            base = base.join(Conversation, Message.conversation_id == Conversation.id).where(
                Conversation.character_id == cid
            )

        result = await session.execute(base)
        row = result.one()
        total_messages = int(row.total_messages or 0)
        total_tokens = int(row.total_tokens or 0)
        total_cost = float(row.total_cost or 0)

        # 按角色分组
        by_char_stmt = (
            select(
                Character.name.label("name"),
                func.count(Message.id).label("messages"),
                func.coalesce(func.sum(Message.tokens), 0).label("tokens"),
                func.coalesce(func.sum(Message.cost), 0).label("cost"),
            )
            .select_from(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Character, Conversation.character_id == Character.id)
            .group_by(Character.name)
        )
        try:
            char_result = await session.execute(by_char_stmt)
            by_character = {
                r.name: {
                    "messages": int(r.messages),
                    "tokens": int(r.tokens),
                    "cost": float(r.cost),
                }
                for r in char_result
            }
        except Exception:
            by_character = {}

        # 按日期分组
        by_day_stmt = (
            select(
                func.to_char(Message.created_at, "YYYY-MM-DD").label("date"),
                func.count(Message.id).label("messages"),
                func.coalesce(func.sum(Message.tokens), 0).label("tokens"),
                func.coalesce(func.sum(Message.cost), 0).label("cost"),
            )
            .select_from(Message)
            .group_by("date")
            .order_by("date")
        )
        try:
            day_result = await session.execute(by_day_stmt)
            by_day = {
                r.date: {
                    "messages": int(r.messages),
                    "tokens": int(r.tokens),
                    "cost": float(r.cost),
                }
                for r in day_result
            }
        except Exception:
            by_day = {}

    return {
        "total_messages": total_messages,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "by_character": by_character,
        "by_day": by_day,
    }
