"""反思来源中间表 - 替代 reflections.source_memory_ids UUID[]

解决 UUID[] 数组无法建立外键约束的问题，
通过中间表保证引用完整性。

⚠️ v3 修复：增加 memory_character_id 字段，与 memory_id 组成复合外键
   引用 memory_episodes(id, character_id) ON DELETE CASCADE。
   PostgreSQL 12+ 支持分区表作为外键父表。
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ReflectionSource(Base):
    """反思来源关联表

    一条反思由多条记忆归纳而来，此表记录关联关系。
    删除记忆时，复合外键的 ON DELETE CASCADE 会自动清理关联行。
    """
    __tablename__ = "reflection_sources"

    reflection_id: Mapped[UUID] = mapped_column(
        ForeignKey("reflections.id", ondelete="CASCADE"),
        primary_key=True, comment="反思 ID"
    )
    memory_id: Mapped[UUID] = mapped_column(
        primary_key=True, comment="记忆 ID"
    )
    memory_character_id: Mapped[UUID] = mapped_column(
        primary_key=True, comment="记忆所属角色 ID（分区键，复合外键组成部分）"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="创建时间"
    )

    __table_args__ = (
        # 复合外键：引用 memory_episodes(id, character_id) ON DELETE CASCADE
        ForeignKeyConstraint(
            ["memory_id", "memory_character_id"],
            ["memory_episodes.id", "memory_episodes.character_id"],
            ondelete="CASCADE",
            name="fk_reflection_sources_memory",
        ),
        # 按 memory 反查：哪些反思引用了此记忆
        Index("idx_refl_sources_memory", "memory_id", "memory_character_id"),
    )
