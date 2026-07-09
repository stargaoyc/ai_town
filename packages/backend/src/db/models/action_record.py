"""行为记录模型 - 分区表（按月）

记录角色执行的每一个 Action，是世界回放和记忆沉淀的事实真相源。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ActionRecord(Base):
    """行为记录表 - 按月分区

    用途：
    - 世界回放（按时间线重建角色行为）
    - 记忆沉淀的事实真相源
    - 数据分析（行为分布、偏好统计）

    分区策略：
    - 按月分区（action_records_YYYY_MM）
    - 默认分区兜底（action_records_default）
    """
    __tablename__ = "action_records"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="记录 ID（UUID v7）"
    )
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), comment="角色 ID"
    )
    action_id: Mapped[str] = mapped_column(String(100), comment="Action 标识符")
    action_name: Mapped[str] = mapped_column(String(100), comment="Action 显示名")
    params: Mapped[dict | None] = mapped_column(JSONB, comment="执行参数")
    reason: Mapped[str | None] = mapped_column(Text, comment="LLM 决策理由")
    result: Mapped[str | None] = mapped_column(Text, comment="执行结果")
    duration_minutes: Mapped[int] = mapped_column(Integer, comment="耗时（虚拟分钟）")
    location: Mapped[str | None] = mapped_column(String(50), comment="执行场景")
    related_characters: Mapped[list] = mapped_column(
        JSONB, default=list, comment="相关角色 ID 列表"
    )
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="执行时间"
    )

    __table_args__ = (
        # 角色行为时间线查询优化
        Index("idx_action_char_time", "character_id", "timestamp"),
        # v8: 补充文档声明的索引
        Index("idx_ar_action", "action_id"),
        Index("idx_ar_params", "params", postgresql_using="gin", postgresql_ops={"params": "jsonb_path_ops"}),
    )