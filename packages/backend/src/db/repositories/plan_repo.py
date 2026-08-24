"""计划 Repository - 角色长期/短期规划管理

LLM 决策返回 planChanges 时更新此表，计划影响候选 Action 的 precondition 评估。
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
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

    async def get_active_plans(self, character_id: UUID) -> list[Plan]:
        """获取角色进行中（status='active'）的计划

        排序：优先级降序 -> 截止时间升序（无截止靠后）——
        越紧急越靠前，注入决策 Prompt 时截断保留的是最要紧的计划。
        """
        stmt = (
            select(Plan)
            .where(
                Plan.character_id == character_id,
                Plan.status == "active",
            )
            .order_by(Plan.priority.desc(), Plan.deadline.asc().nulls_last())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

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
