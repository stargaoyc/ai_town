"""反思 Repository - 角色高层认知归纳的写入与查询

反思由反思系统定期从记忆片段中提炼生成，影响角色长期行为。
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Reflection
from src.db.repositories.base import BaseRepository

logger = get_logger()


class ReflectionRepository(BaseRepository[Reflection]):
    """反思 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Reflection)

    async def add(self, obj: Reflection) -> Reflection:
        """写入一条反思"""
        self.session.add(obj)
        await self.session.flush()
        logger.info(
            "reflection_created",
            character_id=str(obj.character_id),
            related_count=len(obj.related_episodes or []),
        )
        return obj

    async def get_by_character(
        self, character_id: UUID, limit: int = 10
    ) -> list[Reflection]:
        """获取角色反思记录（按创建时间倒序，默认 10 条）"""
        stmt = (
            select(Reflection)
            .where(Reflection.character_id == character_id)
            .order_by(Reflection.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())
