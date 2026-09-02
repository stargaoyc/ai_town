"""计划 Repository - 角色长期/短期规划管理

LLM 决策返回 planChanges 时更新此表，计划影响候选 Action 的 precondition 评估。
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Plan
from src.db.repositories.base import BaseRepository

logger = get_logger()


class PlanRepository(BaseRepository[Plan]):
    """计划 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Plan)

    async def add_plan(self, plan: Plan) -> Plan:
        """新增计划"""
        self.session.add(plan)
        await self.session.flush()
        logger.info(
            "plan_created",
            character_id=str(plan.character_id),
            plan_type=plan.type,
            title=plan.title,
        )
        return plan

    async def get_active_plans(
        self,
        character_id: UUID,
        world_time: datetime | None = None,
    ) -> list[Plan]:
        """获取角色进行中（status='active'）的计划

        排序：优先级降序 -> 截止时间升序（无截止靠后）——
        越紧急越靠前，注入决策 Prompt 时截断保留的是最要紧的计划。

        Args:
            character_id: 角色 ID
            world_time: 当前世界时间（可选）。提供时过滤 deadline 已过
                （deadline < world_time）的计划——deadline 是 LLM 按世界时间
                给出的，世界时间已越过即计划过期，不应再注入决策/对话上下文
                （R9 闭环：防角色永久复读过期计划）。
                无 deadline 的长期计划不过滤。
        """
        stmt = (
            select(Plan)
            .where(
                Plan.character_id == character_id,
                Plan.status == "active",
            )
            .order_by(Plan.priority.desc(), Plan.deadline.asc().nulls_last())
        )
        if world_time is not None:
            stmt = stmt.where(or_(Plan.deadline.is_(None), Plan.deadline >= world_time))
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_active_short_term(self, character_id: UUID) -> list[Plan]:
        """获取角色所有 active short_term 计划（供 R9 补救审查与膨胀控制）"""
        stmt = (
            select(Plan)
            .where(
                Plan.character_id == character_id,
                Plan.status == "active",
                Plan.type == "short_term",
            )
            .order_by(Plan.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def mark_expired(self, plan_id: UUID) -> None:
        """将计划置 expired（R9：deadline 已过且不可补救 / 顺延超限 / 超膨胀上限）"""
        stmt = update(Plan).where(Plan.id == plan_id).values(status="expired", updated_at=func.now())
        await self.session.execute(stmt)
        await self.session.flush()

    async def extend_deadline(self, plan_id: UUID, new_deadline: datetime) -> None:
        """顺延计划 deadline（同一记录不新增行，防膨胀）并递增顺延计数"""
        stmt = (
            update(Plan)
            .where(Plan.id == plan_id)
            .values(deadline=new_deadline, extend_count=Plan.extend_count + 1, updated_at=func.now())
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def has_daily_plan_on(self, character_id: UUID, plan_date: date) -> bool:
        """当日是否已存在 daily 计划（0022：精确日期幂等判定）

        替代此前「day_key in plan.title」字符串匹配——LLM 生成含日期串的
        任意标题都会被误判为今日已规划；精确列查询消除误判。
        """
        stmt = select(Plan.id).where(
            Plan.character_id == character_id,
            Plan.type == "daily",
            Plan.plan_date == plan_date,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def create_plan(self, character_id: UUID, **fields: Any) -> Plan:
        """创建角色计划（LLM 新建路径，character_id 服务端绑定防越权）

        调用方负责先经 _normalize_plan_creates 归一化字段。
        """
        plan = Plan(character_id=character_id, **fields)
        self.session.add(plan)
        await self.session.flush()
        logger.info("plan_created", character_id=str(character_id), type=fields.get("type"))
        return plan

    async def update_plan(self, plan_id: UUID, **fields: Any) -> None:
        """更新计划字段（status/progress/priority 等）"""
        if not fields:
            return
        stmt = update(Plan).where(Plan.id == plan_id).values(**fields)
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info("plan_updated", plan_id=str(plan_id), fields=list(fields.keys()))

    async def update_plan_scoped(
        self,
        plan_id: UUID,
        character_id: UUID,
        **fields: Any,
    ) -> bool:
        """更新计划字段（限定归属角色）

        LLM 决策可携带任意 planId，必须以 character_id 约束更新范围，
        防止跨角色篡改。返回是否命中目标计划。
        """
        if not fields:
            return False
        exists_stmt = select(Plan.id).where(
            Plan.id == plan_id,
            Plan.character_id == character_id,
        )
        if (await self.session.execute(exists_stmt)).scalar_one_or_none() is None:
            return False
        stmt = update(Plan).where(Plan.id == plan_id).values(**fields)
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info("plan_updated_scoped", plan_id=str(plan_id), fields=list(fields.keys()))
        return True
