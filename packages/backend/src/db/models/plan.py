"""计划模型 - 角色的长期/短期规划

LLM 决策时可返回 planChanges，更新此表。
计划会影响候选 Action 的 precondition 评估。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class Plan(Base):
    """计划表

    type:
    - long_term: 长期目标（如"适应新学校"），数周-数月
    - short_term: 短期计划（如"交一个新朋友"），数天-数周

    status:
    - active: 进行中
    - completed: 已完成
    - abandoned: 已放弃

    priority: 1-5，影响 LLM 决策权重
    progress: 0-100，进度百分比
    """
    __tablename__ = "plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), comment="所属角色"
    )
    type: Mapped[str] = mapped_column(String(20), comment="计划类型")
    title: Mapped[str] = mapped_column(String(200), comment="计划标题")
    description: Mapped[str | None] = mapped_column(Text, comment="详细描述")
    status: Mapped[str] = mapped_column(String(20), default="active", comment="状态")
    priority: Mapped[int] = mapped_column(Integer, default=3, comment="优先级 1-5")
    deadline: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), comment="截止时间")
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="进度 0-100")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate="now()", comment="更新时间（触发器自动维护）"
    )