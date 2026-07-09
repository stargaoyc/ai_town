"""消息 Repository - 单条消息记录的持久化与查询

设计要点：
- messages 表通过 (conversation_id, created_at) 复合索引支撑时间线查询
- 按 conversation_id 分页拉取历史消息（ASC/DESC 双向）
- 支持 token/cost 聚合统计，供 Phase 3.5 LLM 成本控制使用
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Message
from src.db.repositories.base import BaseRepository

logger = get_logger()


class MessageRepository(BaseRepository[Message]):
    """消息 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Message)

    async def add(
        self,
        conversation_id: UUID,
        sender: str,
        content: str,
        tokens: int | None = None,
        cost: float | None = None,
        extra_data: dict | None = None,
    ) -> Message:
        """新增一条消息

        Args:
            conversation_id: 会话 ID
            sender: 发送者（user/character/system）
            content: 消息内容
            tokens: LLM token 消耗（character 消息时填写）
            cost: 调用费用 USD（character 消息时填写）
            extra_data: 附加信息（reply_to / attachments 等）

        Returns:
            已写入的 Message 对象
        """
        msg = Message(
            id=uuid7(),
            conversation_id=conversation_id,
            sender=sender,
            content=content,
            tokens=tokens,
            cost=cost,
            extra_data=extra_data,
        )
        self.session.add(msg)
        await self.session.flush()
        logger.info(
            "message_added",
            message_id=str(msg.id),
            conversation_id=str(conversation_id),
            sender=sender,
            content_length=len(content),
            tokens=tokens,
        )
        return msg

    async def get_by_id(self, id: UUID) -> Message | None:
        """按主键查询单条消息（覆盖基类方法，参数名对齐）"""
        return await self.session.get(Message, id)

    async def list_by_conversation(
        self,
        conversation_id: UUID,
        limit: int = 50,
        before: datetime | None = None,
        order_desc: bool = True,
    ) -> list[Message]:
        """按会话拉取消息历史（支持游标分页）

        利用 idx_msg_conv_time (conversation_id, created_at) 复合索引。

        Args:
            conversation_id: 会话 ID
            limit: 返回数量上限
            before: 游标，仅返回该时间点之前的消息（实现"加载更早历史"）
            order_desc: True 倒序（最新优先），False 正序（最早优先）

        Returns:
            消息列表
        """
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if before is not None:
            stmt = stmt.where(Message.created_at < before)

        if order_desc:
            stmt = stmt.order_by(Message.created_at.desc())
        else:
            stmt = stmt.order_by(Message.created_at.asc())

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_recent(
        self,
        conversation_id: UUID,
        limit: int = 20,
    ) -> list[Message]:
        """按会话拉取最近 N 条消息（正序返回，便于构造 LLM 上下文）

        等价于 list_by_conversation(order_desc=True) 后反转。
        """
        msgs = await self.list_by_conversation(
            conversation_id=conversation_id,
            limit=limit,
            order_desc=True,
        )
        msgs.reverse()  # 原地反转，最新在末尾
        return msgs

    async def sum_tokens_by_conversation(
        self,
        conversation_id: UUID,
    ) -> tuple[int, float]:
        """统计会话累计 token 与 cost（Phase 3.5 成本控制依赖）

        Returns:
            (tokens, cost) 元组
        """
        stmt = (
            select(
                func.coalesce(func.sum(Message.tokens), 0),
                func.coalesce(func.sum(Message.cost), 0.0),
            )
            .where(Message.conversation_id == conversation_id)
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return int(row[0]), float(row[1])

    async def sum_tokens_by_character(
        self,
        character_id: UUID,
    ) -> tuple[int, float]:
        """统计角色累计 token 与 cost（跨所有会话）

        通过 JOIN conversations 聚合。

        Returns:
            (tokens, cost) 元组
        """
        from src.db.models import Conversation

        stmt = (
            select(
                func.coalesce(func.sum(Message.tokens), 0),
                func.coalesce(func.sum(Message.cost), 0.0),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.character_id == character_id)
        )
        result = await self.session.execute(stmt)
        row = result.one()
        return int(row[0]), float(row[1])
