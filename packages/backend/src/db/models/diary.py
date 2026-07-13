"""角色日记模型 - 基于 memory_episodes 生成的叙事归档层

日记不替代 Episode 真相源，而是角色对一段时间经历的叙事性总结。
支持 day/week/month/year 四种周期。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class CharacterDiary(Base):
    """角色日记 - 基于记忆片段生成的叙事归档

    与 memory_episodes 的区别：
    - Episode 是事实真相源（结构化、原子化）
    - Diary 是角色视角的叙事性总结（主观、归档性）

    周期说明：
    - day: 日报，diary_end_date 为空
    - week/month/year: 周期归档，diary_end_date 标记周期起始
    """
    __tablename__ = "character_diaries"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="日记 ID（UUID v7）"
    )
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        comment="角色 ID",
    )
    period: Mapped[str] = mapped_column(
        String(20), comment="周期类型 day/week/month/year"
    )
    diary_date: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), comment="日记日期"
    )
    diary_end_date: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        comment="周期结束日期（day 类型为空，其他为周期起始）",
    )
    title: Mapped[str | None] = mapped_column(String(200), comment="日记标题")
    content: Mapped[str] = mapped_column(Text, comment="日记内容（叙事性正文）")
    mood: Mapped[str | None] = mapped_column(String(50), comment="日记时的情绪")
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        comment="生成时间",
    )

    __table_args__ = (
        # 角色日记时间线查询
        Index("idx_diary_char_date", "character_id", "diary_date"),
        # 按周期查询角色的日记
        Index("idx_diary_char_period", "character_id", "period", "diary_date"),
    )
