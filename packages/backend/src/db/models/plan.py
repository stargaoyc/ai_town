"""计划模型 - 角色的长期/短期/当日规划

LLM 决策时可返回 planChanges，更新此表。
计划经决策 Prompt 的 [当前计划] 段注入影响 LLM 权重（软引导），
不做 precondition 硬过滤——硬门禁会阻断角色的自主行为空间。
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from src.db.base import Base


class Plan(Base):
    """计划表

    type:
    - long_term: 长期目标（如"适应新学校"），数周-数月
    - short_term: 短期计划（如"交一个新朋友"），数天-数周
    - daily: 当日计划（如"下午去图书馆还书"），当天有效

    status:
    - active: 进行中
    - completed: 已完成
    - abandoned: 已放弃

    priority: 1-5，影响 LLM 决策权重
    progress: 0-100，进度百分比
    """

    __tablename__ = "plans"
    # 0016 迁移补建（此前 data-model.md 声称存在但从未创建，R4-M1）：
    # get_active_plans 每 Tick 每角色一次，是最热的缺失索引
    __table_args__ = (Index("idx_plans_char_status", "character_id", "status"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), comment="所属角色")
    type: Mapped[str] = mapped_column(String(20), comment="计划类型")
    title: Mapped[str] = mapped_column(String(200), comment="计划标题")
    description: Mapped[str | None] = mapped_column(Text, comment="详细描述")
    # 0025 迁移：制定理由（LLM 决策/日报生成时给出，与 action_records.reason 对应，
    # 供 auto_complete 语义对照与决策上下文展示）
    reason: Mapped[str | None] = mapped_column(Text, comment="制定理由")
    status: Mapped[str] = mapped_column(String(20), default="active", comment="状态")
    priority: Mapped[int] = mapped_column(Integer, default=3, comment="优先级 1-5")
    deadline: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), comment="截止时间")
    # 0022 迁移：daily 计划的幂等键（精确日期，替代标题字符串匹配）；非 daily 为 NULL
    plan_date: Mapped[date | None] = mapped_column(Date, comment="计划日期（daily 幂等键，仅 daily 计划非空）")
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="进度 0-100")
    # 0024 迁移：short_term 计划顺延计数（deadline 已过时补救顺延，达上限强制过期防膨胀）
    extend_count: Mapped[int] = mapped_column(Integer, default=0, comment="顺延次数（R9 防膨胀）")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()", comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", onupdate=func.now(), comment="更新时间（触发器自动维护）"
    )
