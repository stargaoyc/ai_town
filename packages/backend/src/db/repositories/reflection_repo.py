"""反思 Repository - 角色高层认知归纳的写入与查询

反思由反思系统定期从记忆片段中提炼生成，影响角色长期行为。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
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
            related_count=0,
        )
        return obj

    async def get_by_character(self, character_id: UUID, limit: int = 10) -> list[Reflection]:
        """获取角色反思记录（元反思优先，其余按创建时间倒序，默认 10 条）"""
        stmt = (
            select(Reflection)
            .where(Reflection.character_id == character_id)
            .order_by(Reflection.tier.desc(), Reflection.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_recent(self, character_id: UUID, since: datetime, tier: int | None = None) -> int:
        """统计 since 之后的反思条数（tier=None 不限层级）"""
        conditions = [Reflection.character_id == character_id, Reflection.created_at >= since]
        if tier is not None:
            conditions.append(Reflection.tier == tier)
        stmt = select(func.count()).select_from(Reflection).where(*conditions)
        return int(await self.session.scalar(stmt) or 0)

    async def get_recent_contents(self, character_id: UUID, limit: int = 10, max_tier: int = 1) -> list[str]:
        """取最近若干条 tier<=max_tier 的反思正文（元反思原料）"""
        stmt = (
            select(Reflection.content)
            .where(Reflection.character_id == character_id, Reflection.tier <= max_tier)
            .order_by(Reflection.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
