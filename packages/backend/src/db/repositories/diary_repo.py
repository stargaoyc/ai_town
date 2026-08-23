"""日记 Repository - 角色叙事归档的查询"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CharacterDiary
from src.db.repositories.base import BaseRepository


class DiaryRepository(BaseRepository[CharacterDiary]):
    """日记 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, CharacterDiary)

    async def get_latest(
        self,
        character_id: UUID,
        period: str = "day",
    ) -> CharacterDiary | None:
        """获取角色最近一篇指定周期的日记"""
        stmt = (
            select(CharacterDiary)
            .where(
                CharacterDiary.character_id == character_id,
                CharacterDiary.period == period,
            )
            .order_by(CharacterDiary.diary_date.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_period_range(
        self,
        character_id: UUID,
        period: str,
        start: datetime,
        end: datetime,
    ) -> list[CharacterDiary]:
        """获取角色指定周期、时间范围内的日记（按日期倒序）"""
        stmt = (
            select(CharacterDiary)
            .where(
                CharacterDiary.character_id == character_id,
                CharacterDiary.period == period,
                CharacterDiary.diary_date >= start,
                CharacterDiary.diary_date <= end,
            )
            .order_by(CharacterDiary.diary_date.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())
