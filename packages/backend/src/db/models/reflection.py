"""反思模型 - 角色的高层认知归纳

由反思系统定期从记忆片段中提炼生成，影响角色长期行为。

⚠️ related_episodes 字段已在 0002_optimize v5 迁移中删除，
   关联记忆通过 reflection_sources 中间表管理（复合外键保证参照完整性）。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

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
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), comment="所属角色"
    )
    content: Mapped[str] = mapped_column(Text, comment="反思内容")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="创建时间"
    )