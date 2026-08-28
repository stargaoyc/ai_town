"""Person Memory 事实条目模型 - 两层结构追加层

每次交互 LLM 抽取的「新事实」逐条追加（append-only，只写不改）；
后台压缩任务把足够多的未压缩条目合并进 person_memories.content 主档，
然后把这些条目置 compacted=TRUE 软归档（保留追溯，不删）。

0023 迁移：新增 embedding 语义向量列（记忆-05 语义召回）——此前用字符
二元组重叠选相关条目，无语义；写入时即时向量化后可按与当前消息的语义
相似度召回「关于这个用户我记过的相关事」。
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from src.config import settings
from src.db.base import Base


class PersonMemoryEntry(Base):
    """角色对用户的单条事实条目（append-only）"""

    __tablename__ = "person_memory_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, comment="条目 ID，UUID v7")
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        comment="角色 ID",
    )
    user_id: Mapped[str] = mapped_column(String(100), comment="用户标识，如 qq_123456")
    platform: Mapped[str] = mapped_column(String(20), default="web", comment="来源平台：web/qq/lark/internal")
    content: Mapped[str] = mapped_column(Text, comment="事实内容，一条一句独立事实")
    # 0023 迁移：语义召回向量（审查 记忆-05）。写入时即时生成，维度与
    # memory_episodes 一致（settings.embedding_dim）；NULL 时检索回退二元组重叠。
    embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(settings.embedding_dim),
        nullable=True,
        comment="语义向量（记忆-05 语义召回；NULL 回退二元组重叠）",
    )
    compacted: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已合并进主档")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default="now()",
        comment="创建时间",
    )

    __table_args__ = (
        # 未压缩查询（组合主档装配）：(角色, 用户, 压缩态) + 时间倒序
        Index("idx_pmem_entries_lookup", "character_id", "user_id", "compacted", "created_at"),
    )
