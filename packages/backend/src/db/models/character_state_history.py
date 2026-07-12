"""角色状态历史快照模型 - 分区表（按月）

每次角色状态更新时写入一条快照，用于状态趋势图表展示。
与 CharacterState（仅当前状态）互补，提供历史趋势数据。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class CharacterStateHistory(Base):
    """角色状态历史快照表 - 按月分区

    用途：
    - 前端状态趋势图表（体力/饱腹度/情绪/金钱/手机电量/社交能量曲线）
    - 数据分析（状态变化模式、资源消耗趋势）

    分区策略：
    - 按月分区（character_state_history_YYYY_MM）
    - 默认分区兜底
    """
    __tablename__ = "character_state_history"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="记录 ID（UUID v7）"
    )
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), comment="角色 ID"
    )
    location: Mapped[str | None] = mapped_column(String(50), comment="当前场景 ID")
    stamina: Mapped[int] = mapped_column(Integer, comment="体力 0-100")
    satiety: Mapped[int] = mapped_column(Integer, comment="饱腹度 0-100")
    mood: Mapped[str | None] = mapped_column(String(20), comment="情绪")
    money: Mapped[int] = mapped_column(Integer, comment="金钱")
    phone_battery: Mapped[int] = mapped_column(Integer, comment="手机电量 0-100")
    social_energy: Mapped[int] = mapped_column(Integer, comment="社交能量 0-100")
    action_id: Mapped[str | None] = mapped_column(String(100), comment="触发状态变更的 Action ID")
    recorded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="记录时间"
    )

    __table_args__ = (
        Index("idx_csh_char_time", "character_id", "recorded_at"),
    )
