"""行为记录 Repository - 按角色/时间维度查询 Action 历史

ActionRecord 为按月分区表，(idx_action_char_time) 索引支持角色时间线查询。
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import ActionRecord
from src.db.repositories.base import BaseRepository

logger = get_logger()


class ActionRepository(BaseRepository[ActionRecord]):
    """行为记录 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, ActionRecord)

    async def add(self, obj: ActionRecord) -> ActionRecord:
        """记录一条行为（覆盖基类 add 以追加日志）"""
        self.session.add(obj)
        await self.session.flush()
        logger.info(
            "action_recorded",
            character_id=str(obj.character_id),
            action_id=obj.action_id,
        )
        return obj

    async def get_by_character(
        self, character_id: UUID, limit: int = 50
    ) -> list[ActionRecord]:
        """获取角色行为时间线（按时间倒序，默认 50 条）"""
        stmt = (
            select(ActionRecord)
            .where(ActionRecord.character_id == character_id)
            .order_by(ActionRecord.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_recent(
        self, character_id: UUID, hours: int = 24
    ) -> list[ActionRecord]:
        """获取角色最近 N 小时的行为（按时间倒序）

        以 UTC 当前时间为基准计算截止点，配合 TIMESTAMPTZ 字段比较。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = (
            select(ActionRecord)
            .where(
                ActionRecord.character_id == character_id,
                ActionRecord.timestamp >= cutoff,
            )
            .order_by(ActionRecord.timestamp.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())
