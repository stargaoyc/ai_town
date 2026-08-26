"""反思模型 - 角色的高层认知归纳

由反思系统定期从记忆片段中提炼生成，影响角色长期行为。

⚠️ related_episodes 字段已在 0002_optimize v5 迁移中删除，
   关联记忆通过 reflection_sources 中间表管理（复合外键保证参照完整性）。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from src.db.base import Base


class Reflection(Base):
    """反思表

    生成流程：
    1. 每 N 条未反思记忆触发（默认 N=20）
    2. LLM 读取近期记忆，归纳高层认知
    3. 写入 reflections 表
    4. 标记对应 memory_episodes 为 is_reflected=TRUE

    关联记忆通过 reflection_sources 中间表管理（复合外键 ON DELETE CASCADE）。
    """

    __tablename__ = "reflections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), comment="所属角色")
    content: Mapped[str] = mapped_column(Text, comment="反思内容")
    tier: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", comment="反思层级：1=批次主题反思，2=跨期元反思"
    )
    # P2-10：检索配额内按重要性加权，避免平庸与深刻反思平权竞争
    importance: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5", comment="重要性 1-10（按支撑记忆数/主题数推导）"
    )
    # P1-11：元反思的来源 tier-1 反思 ID 列表——reflection_sources 复合外键
    # 只能挂 memory_episodes，元认知溯源需独立承载
    source_reflection_ids: Mapped[list[Any] | None] = mapped_column(
        JSONB, comment="元反思来源的 tier-1 反思 ID 列表（仅 tier=2 使用）"
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()", comment="创建时间")
    # 0016 迁移补建（R4-M1：文档声称存在但从未创建）
    __table_args__ = (Index("idx_refl_char_time", "character_id", created_at.desc()),)
    embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(2048),
        nullable=True,
        comment="语义向量（保存时即时生成；失败留 NULL，检索回退 recency 的文档化退化）",
    )
