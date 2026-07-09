"""会话 Repository - 用户与角色对话线程的持久化

设计要点：
- conversations 表通过 (user_id, character_id) 唯一标识一个会话
  同一用户对同一角色仅保留一个活跃会话
- context JSONB 存储压缩后的对话上下文摘要，避免每次拉取全量历史
- last_message_at 维护会话活跃度，用于排序与清理
"""
from datetime import datetime, timezone
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Conversation
from src.db.repositories.base import BaseRepository

logger = get_logger()


class ConversationRepository(BaseRepository[Conversation]):
    """会话 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Conversation)

    async def get_or_create(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
    ) -> Conversation:
        """获取或创建会话（同一用户在同一平台对同一角色仅一个会话）

        使用 ON CONFLICT DO NOTHING 保证幂等，避免并发创建重复会话。
        v4: 唯一键扩展为 (user_id, platform, character_id)，支持跨平台独立会话。

        Args:
            character_id: 角色 ID
            user_id: 用户标识
            platform: 来源平台（web/qq/lark/internal）

        Returns:
            会话对象（含 id 与现有 context）
        """
        # 幂等插入：若已存在则跳过
        stmt = insert(Conversation).values(
            id=uuid7(),
            character_id=character_id,
            user_id=user_id,
            platform=platform,
        ).on_conflict_do_nothing(
            index_elements=["user_id", "platform", "character_id"],
        ).returning(Conversation)

        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            # 已存在，反查（含 platform 维度）
            select_stmt = (
                select(Conversation)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.platform == platform,
                    Conversation.character_id == character_id,
                )
            )
            result = await self.session.execute(select_stmt)
            record = result.scalar_one()
        else:
            await self.session.flush()
            logger.info(
                "conversation_created",
                conversation_id=str(record.id),
                character_id=str(character_id),
                user_id=user_id,
                platform=platform,
            )

        return record

    async def get_by_id(self, id: UUID) -> Conversation | None:
        """按主键查询单条会话（覆盖基类方法，参数名对齐）"""
        return await self.session.get(Conversation, id)

    async def get_by_user_character(
        self,
        user_id: str,
        character_id: UUID,
        platform: str | None = None,
    ) -> Conversation | None:
        """按 (user_id, character_id) 精确查询会话（仅查询不创建）

        v4: 增加 platform 可选参数，支持跨平台独立会话查询。
        若不指定 platform，返回该用户与角色的任一会话（按 last_message_at 倒序取首条）。

        Args:
            user_id: 用户标识
            character_id: 角色 ID
            platform: 可选，来源平台

        Returns:
            会话对象，不存在返回 None
        """
        stmt = (
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.character_id == character_id,
            )
        )
        if platform is not None:
            stmt = stmt.where(Conversation.platform == platform)
        else:
            # 不指定平台时取最近活跃的会话
            stmt = stmt.order_by(Conversation.last_message_at.desc().nullslast()).limit(1)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_context(
        self,
        conversation_id: UUID,
        context: dict,
    ) -> None:
        """更新会话上下文摘要

        由 MessageService 在上下文压缩后调用。

        Args:
            conversation_id: 会话 ID
            context: 压缩后的上下文（JSONB 兼容 dict）
        """
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                context=context,
                last_message_at=datetime.now(timezone.utc),
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def touch_last_message(
        self,
        conversation_id: UUID,
    ) -> None:
        """更新会话最后消息时间（轻量更新，不修改 context）

        Args:
            conversation_id: 会话 ID
        """
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_message_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def list_by_character(
        self,
        character_id: UUID,
        limit: int = 50,
    ) -> list[Conversation]:
        """按角色查询所有会话（角色侧主动分享使用）

        按 last_message_at 倒序排序，活跃会话优先。
        """
        stmt = (
            select(Conversation)
            .where(Conversation.character_id == character_id)
            .order_by(Conversation.last_message_at.desc().nullslast())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_active(
        self,
        character_id: UUID | None = None,
        since: datetime | None = None,
    ) -> int:
        """统计活跃会话数（监控指标）

        Args:
            character_id: 可选，限定角色
            since: 可选，统计该时间后有消息的会话数
        """
        stmt = select(func.count()).select_from(Conversation)
        if character_id is not None:
            stmt = stmt.where(Conversation.character_id == character_id)
        if since is not None:
            stmt = stmt.where(Conversation.last_message_at >= since)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
