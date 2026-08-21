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
        """获取角色进行中（status='active'）的计划"""
        stmt = (
            select(Plan)
            .where(
                Plan.character_id == character_id,
                Plan.status == "active",
            )
            .order_by(Plan.priority.desc())
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
