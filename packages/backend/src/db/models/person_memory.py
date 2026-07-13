"""角色对用户的记忆模型

记录角色对每个用户的认知：偏好、关系进展、共同话题等。
每次交互后更新，影响后续对话上下文。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class PersonMemory(Base):
    """角色对用户的独立记忆

    与 conversation/context 的区别：
    - conversation.context 是会话级摘要（短期、按会话）
    - person_memories 是角色对用户的长期认知（跨会话、按用户）

    热度机制：
    - 每次交互 heat +1
    - 长时间不交互可由后台任务衰减
    - heat 影响检索权重与上下文注入优先级
    """
    __tablename__ = "person_memories"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="记忆 ID（UUID v7）"
    )
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        comment="角色 ID",
    )
    user_id: Mapped[str] = mapped_column(
        String(100), comment="用户标识（如 qq_123456）"
    )
    platform: Mapped[str] = mapped_column(
        String(20), default="web", comment="来源平台（web/qq/lark/internal）"
    )
    content: Mapped[str] = mapped_column(
        Text, comment="记忆内容（自然语言描述）"
    )
    summary: Mapped[str | None] = mapped_column(Text, comment="压缩摘要")
    heat: Mapped[int] = mapped_column(
        Integer, default=0, comment="热度（交互次数）"
    )
    last_interaction_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        comment="最后交互时间",
    )
    preferences: Mapped[dict | None] = mapped_column(
        JSONB, comment="用户偏好（结构化）"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )

    __table_args__ = (
        # 角色 + 用户唯一约束（一个角色对同一用户只保留一条记忆）
        Index("idx_pmem_char_user", "character_id", "user_id", unique=True),
        # 按热度查询（活跃用户检索）
        Index("idx_pmem_heat", "character_id", "heat"),
    )
