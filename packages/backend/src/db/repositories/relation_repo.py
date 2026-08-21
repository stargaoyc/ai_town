"""关系 Repository - 角色间有向关系图管理

双向关系需两条记录（A→B 与 B→A）。get_or_create 用于社交 Action 前置准备。
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Relation
from src.db.repositories.base import BaseRepository

logger = get_logger()


class RelationRepository(BaseRepository[Relation]):
    """关系 Repository - 有向图"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Relation)

    async def get_relations(self, character_id: UUID) -> list[Relation]:
        """获取角色对所有其他角色的关系记录"""
        stmt = select(Relation).where(Relation.character_id == character_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_relation(self, character_id: UUID, target_id: UUID, **fields: Any) -> None:
        """更新角色对目标角色的关系字段（strength/relationship_type/notes 等）

        Relation 为复合主键（character_id, target_id），需同时按两者定位。
        """
        if not fields:
            return
        stmt = (
            update(Relation)
            .where(
                Relation.character_id == character_id,
                Relation.target_id == target_id,
            )
            .values(**fields)
        )
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info(
            "relation_updated",
            character_id=str(character_id),
            target_id=str(target_id),
            fields=list(fields.keys()),
        )

    async def get_or_create(self, character_id: UUID, target_id: UUID) -> Relation:
        """获取或创建关系记录（不存在则以默认值创建）"""
        stmt = select(Relation).where(
            Relation.character_id == character_id,
            Relation.target_id == target_id,
        )
        result = await self.session.execute(stmt)
        rel = result.scalar_one_or_none()
        if rel is not None:
            return rel

        rel = Relation(character_id=character_id, target_id=target_id)
        self.session.add(rel)
        await self.session.flush()
        logger.info(
            "relation_created",
            character_id=str(character_id),
            target_id=str(target_id),
        )
        return rel
