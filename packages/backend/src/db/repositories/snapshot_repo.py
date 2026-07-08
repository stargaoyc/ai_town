"""世界事件与快照 Repository - 事件溯源 + 定期快照

架构：
- world_events: 差分事件（高频，仅状态变化时写入）
- world_snapshots: 完整状态快照（低频，每 1000 Tick 存一次）
- 冷启动恢复：加载最新快照 → 回放之后的增量事件 → 恢复状态
"""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import WorldEvent, WorldSnapshot
from src.db.repositories.base import BaseRepository

logger = get_logger()


class WorldEventRepository(BaseRepository[WorldEvent]):
    """世界事件 Repository

    事件幂等：UNIQUE(tick_id, event_type) 约束保证单 Tick 单类型事件唯一。
    add_batch 使用 ON CONFLICT DO NOTHING，重复写入自动跳过。

    用法：
        async with db.session() as session:
            repo = WorldEventRepository(session)
            await repo.add(event)
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, WorldEvent)

    async def add(self, event: WorldEvent) -> WorldEvent:
        """写入一条世界事件"""
        self.session.add(event)
        await self.session.flush()
        logger.info(
            "world_event_created",
            tick_id=event.tick_id,
            event_type=event.event_type,
        )
        return event

    async def add_batch(self, events: list[WorldEvent]) -> None:
        """批量写入世界事件（幂等：重复写入自动跳过）

        使用 INSERT ... ON CONFLICT DO NOTHING，
        配合 UNIQUE(tick_id, event_type) 约束保证幂等性。
        服务重启 / Tick 重试时不会产生重复事件。
        """
        if not events:
            return
        # 使用 PostgreSQL 原生 INSERT ... ON CONFLICT DO NOTHING
        rows = [
            {
                "id": e.id,
                "tick_id": e.tick_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at,
            }
            for e in events
        ]
        stmt = pg_insert(WorldEvent).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_world_events_tick_type"
        )
        result = await self.session.execute(stmt)
        logger.info(
            "world_events_batch_created",
            count=len(events),
            inserted=result.rowcount,
            tick_id=events[0].tick_id if events else None,
        )

    async def get_by_tick(self, tick_id: int) -> list[WorldEvent]:
        """按 Tick 序号获取该 Tick 的所有事件"""
        stmt = (
            select(WorldEvent)
            .where(WorldEvent.tick_id == tick_id)
            .order_by(WorldEvent.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_by_type(
        self, event_type: str, limit: int = 100
    ) -> list[WorldEvent]:
        """按事件类型查询最近的事件"""
        stmt = (
            select(WorldEvent)
            .where(WorldEvent.event_type == event_type)
            .order_by(WorldEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_range(
        self, start_tick: int, end_tick: int
    ) -> list[WorldEvent]:
        """查询 Tick 区间内的所有事件（用于回放）"""
        stmt = (
            select(WorldEvent)
            .where(
                WorldEvent.tick_id >= start_tick,
                WorldEvent.tick_id <= end_tick,
            )
            .order_by(WorldEvent.tick_id, WorldEvent.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())


class WorldSnapshotRepository(BaseRepository[WorldSnapshot]):
    """世界快照 Repository - 冷启动恢复用

    每 1000 Tick 存一次完整快照，冷启动时从最新快照开始回放增量事件。
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, WorldSnapshot)

    async def add(self, snapshot: WorldSnapshot) -> WorldSnapshot:
        """写入一条世界快照"""
        self.session.add(snapshot)
        await self.session.flush()
        logger.info(
            "world_snapshot_created",
            tick_id=snapshot.tick_id,
        )
        return snapshot

    async def get_latest(self) -> WorldSnapshot | None:
        """获取最新的世界快照（冷启动恢复入口）"""
        stmt = (
            select(WorldSnapshot)
            .order_by(WorldSnapshot.tick_id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_tick(self, tick_id: int) -> WorldSnapshot | None:
        """按 Tick 序号获取快照"""
        stmt = select(WorldSnapshot).where(WorldSnapshot.tick_id == tick_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
