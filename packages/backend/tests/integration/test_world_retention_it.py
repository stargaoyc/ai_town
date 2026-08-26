"""世界历史保留集成测试 - world_events 超期删除 / world_snapshots 保留最近 N 份"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import WorldEvent, WorldSnapshot
from src.scheduler.loops import run_world_retention_cycle


async def _seed_event(session: AsyncSession, tick_id: int, days_ago: int) -> None:
    session.add(
        WorldEvent(
            tick_id=tick_id,
            event_type="time",
            event_key=f"evt-{tick_id}",
            payload={"tick_id": tick_id},
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )


async def _seed_snapshot(session: AsyncSession, tick_id: int) -> None:
    session.add(WorldSnapshot(tick_id=tick_id, locations={"home": {"visitors": 0}}))


class TestWorldRetention:
    async def test_old_events_deleted_recent_kept(self, it_session: AsyncSession) -> None:
        await _seed_event(it_session, tick_id=1, days_ago=120)
        await _seed_event(it_session, tick_id=2, days_ago=10)
        await it_session.flush()

        deleted_events, _ = await run_world_retention_cycle(session_factory=_ctx(it_session))

        assert deleted_events == 1
        remaining = (await it_session.execute(select(WorldEvent.tick_id))).scalars().all()
        assert remaining == [2]

    async def test_snapshots_keep_latest_n(self, it_session: AsyncSession) -> None:
        for tick in (10, 20, 30, 40):
            await _seed_snapshot(it_session, tick)
        await it_session.flush()

        _, deleted_snaps = await run_world_retention_cycle(session_factory=_ctx(it_session))

        # 默认保留最近 3 份 → 删除最旧的 tick=10
        assert deleted_snaps == 1
        remaining = (await it_session.execute(select(WorldSnapshot.tick_id))).scalars().all()
        assert sorted(remaining) == [20, 30, 40]

    async def test_noop_when_within_retention(self, it_session: AsyncSession) -> None:
        await _seed_event(it_session, tick_id=5, days_ago=1)
        await _seed_snapshot(it_session, tick_id=5)
        await it_session.flush()

        deleted_events, deleted_snaps = await run_world_retention_cycle(session_factory=_ctx(it_session))

        assert (deleted_events, deleted_snaps) == (0, 0)

    async def test_batch_size_one_deletes_all_rows(self, it_session: AsyncSession, monkeypatch: Any) -> None:
        """R5-L4：批大小=1 时逐行分批删空，语义与单条全量 DELETE 一致"""
        from src.config import settings as _settings

        monkeypatch.setattr(_settings, "retention_delete_batch_size", 1)
        for i in range(3):
            await _seed_event(it_session, tick_id=100 + i, days_ago=120)
        await it_session.flush()

        deleted_events, deleted_snaps = await run_world_retention_cycle(session_factory=_ctx(it_session))

        assert (deleted_events, deleted_snaps) == (3, 0)
        remaining = (await it_session.execute(select(WorldEvent.tick_id))).scalars().all()
        assert remaining == []


def _ctx(session: AsyncSession) -> Any:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory
