"""对话与消息模型 - 角色与用户/其他角色的交流记录

消息来源：
- user: 用户消息（QQ/飞书/Web）
- character: 角色回复
- system: 系统消息
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from src.db.base import Base


class Conversation(Base):
    """对话会话表 - 一个用户与一个角色的对话线程

    platform: 消息来源平台（web/qq/lark/internal）
    context: 对话上下文（最近 N 条消息摘要，用于 LLM 回复）
    updated_at: 触发器自动维护（v4 新增）
    """

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), comment="角色 ID")
    user_id: Mapped[str] = mapped_column(String(100), comment="用户标识")
    platform: Mapped[str] = mapped_column(
        String(20),
        comment="来源平台（web/qq/lark/internal）",
    )
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="对话上下文")
    last_message_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), comment="最后消息时间")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()", comment="创建时间")
    # v4 新增：触发器自动维护
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="更新时间（触发器自动维护）"
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")

    __table_args__ = (
        # v4: 唯一键扩展为 (user_id, platform, character_id)，支持跨平台
        Index(
            "idx_conv_user_platform_char",
            "user_id",
            "platform",
            "character_id",
            unique=True,
        ),
        Index("idx_conv_last_msg", "last_message_at"),
        Index("idx_conv_char", "character_id"),
        # v4: 枚举字段 CHECK 约束
        CheckConstraint(
            "platform IN ('web', 'qq', 'lark', 'internal')",
            name="ck_conv_platform",
        ),
    )


class Message(Base):
    """消息表 - 单条消息记录

    sender: 发送者（user/character/system）
    content: 消息内容
    tokens: LLM token 消耗（Phase 3 成本追踪）
    cost: 本次调用费用（USD）
    extra_data: 附加信息（回复延迟、平台特定字段等）
    """

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), comment="会话 ID")
    sender: Mapped[str] = mapped_column(String(20), comment="发送者（user/character/system）")
    content: Mapped[str] = mapped_column(Text, comment="消息内容")
    tokens: Mapped[int | None] = mapped_column(Integer, comment="LLM token 消耗")
    cost: Mapped[float | None] = mapped_column(Numeric(10, 6), comment="调用费用（USD）")
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="附加信息（延迟、平台字段等）")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()", comment="创建时间")

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_msg_conv_time", "conversation_id", "created_at"),
        Index("idx_msg_created", "created_at"),
        # v4: 枚举字段 CHECK 约束
        CheckConstraint(
            "sender IN ('user', 'character', 'system')",
            name="ck_msg_sender",
        ),
    )
