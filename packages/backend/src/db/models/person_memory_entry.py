"""Person Memory 事实条目模型 - 两层结构的追加层

每次交互由 LLM 抽取的「新事实」逐条追加，只写不改；
后台压缩任务把足够多的未压缩条目合并进 person_memories.content 主档，
并将这些条目标记 compacted=TRUE（软归档，保留可追溯）。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from src.db.base import Base


class PersonMemoryEntry(Base):
    """角色对用户的单条事实条目（append-only）"""

    __tablename__ = "person_memory_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, comment="条目 ID（UUID v7）")
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        comment="角色 ID",
    )
    user_id: Mapped[str] = mapped_column(String(100), comment="用户标识（如 qq_123456）")
    platform: Mapped[str] = mapped_column(String(20), default="web", comment="来源平台：web/qq/lark/internal")
    content: Mapped[str] = mapped_column(Text, comment="事实内容（一条一个独立事实）")
    compacted: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已合并进主档")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default="now()",
        comment="创建时间",
    )

    __table_args__ = (
        # 主档压缩查询与近期上下文组装共用：(角色, 用户, 压缩态) + 时间倒序
        Index("idx_pmem_entries_lookup", "character_id", "user_id", "compacted", "created_at"),
    )
