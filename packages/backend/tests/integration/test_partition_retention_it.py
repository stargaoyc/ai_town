"""分区保留期回收集成测试（Round-3 H9）

验证 drop_old_partitions / run_partition_retention_cycle：
- 整月早于 retention 边界的分区被 DETACH + DROP
- 边界内（当前月）分区保留
- character_state_history DEFAULT 分区中的超期散行被清理

集成服务不可达时经 it_db_url fixture 自动跳过。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, CharacterStateHistory
from src.scheduler.partition_scheduler import run_partition_retention_cycle


def _month_bounds(months_back: int) -> tuple[str, str, str]:
    """返回（YYYY_MM, 月初 ISO 日, 次月初 ISO 日）——months_back 个月前的整月范围"""
    now = datetime.now(UTC)
    total = now.year * 12 + (now.month - 1) - months_back
    year, month = total // 12, total % 12 + 1
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}_{month:02d}", f"{year:04d}-{month:02d}-01", f"{next_year:04d}-{next_month:02d}-01"


async def _table_exists(session: AsyncSession, name: str) -> bool:
    result = await session.execute(text("SELECT to_regclass(:name)"), {"name": name})
    return result.scalar_one() is not None


@pytest_asyncio.fixture
async def retention_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="分区回收测试角色")
    it_session.add(char)
    await it_session.flush()
    return char


class TestPartitionRetention:
    async def test_expired_partitions_dropped_and_recent_kept(
        self, it_session: AsyncSession, retention_character: Character
    ) -> None:
        session = it_session
        # 预创建当前月及未来分区（「保留」侧样本，走与生产相同的 pre_create 助手）
        await session.execute(text("SELECT pre_create_partitions(3);"))

        # 「回收」侧样本：手工建超期旧分区——action_records 14 个月前（>12 个月保留期）、
        # character_state_history 8 个月前（>6 个月保留期）
        ar_old_month, ar_start, ar_end = _month_bounds(months_back=14)
        csh_old_month, csh_start, csh_end = _month_bounds(months_back=8)
        await session.execute(
            text(
                f"CREATE TABLE action_records_{ar_old_month} PARTITION OF action_records "
                f"FOR VALUES FROM ('{ar_start}') TO ('{ar_end}')"
            )
        )
        await session.execute(
            text(
                f"CREATE TABLE character_state_history_{csh_old_month} PARTITION OF character_state_history "
                f"FOR VALUES FROM ('{csh_start}') TO ('{csh_end}')"
            )
        )

        # recorded_at 早于所有月度分区 → 路由进 DEFAULT 分区，只能靠 DELETE 清理
        stale_row = CharacterStateHistory(
            id=uuid7(),
            character_id=retention_character.id,
            stamina=50,
            satiety=50,
            money=0,
            phone_battery=50,
            social_energy=50,
            recorded_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(stale_row)
        await session.flush()

        @asynccontextmanager
        async def factory() -> AsyncIterator[AsyncSession]:
            yield session

        dropped, deleted_default_rows = await run_partition_retention_cycle(factory)

        assert deleted_default_rows == 1
        assert dropped >= 2
        assert not await _table_exists(session, f"action_records_{ar_old_month}")
        assert not await _table_exists(session, f"character_state_history_{csh_old_month}")

        current_month, _, _ = _month_bounds(months_back=0)
        assert await _table_exists(session, f"action_records_{current_month}")
        assert await _table_exists(session, f"character_state_history_{current_month}")

        remaining = (
            await session.execute(
                text("SELECT count(*) FROM character_state_history_default WHERE id = :id"),
                {"id": stale_row.id},
            )
        ).scalar_one()
        assert remaining == 0

    async def test_boundary_partition_not_dropped(self, it_session: AsyncSession) -> None:
        """恰好落在 retention 边界当月的分区必须保留（整月超出才允许删）"""
        session = it_session
        await session.execute(text("SELECT pre_create_partitions(3);"))

        # action_records 保留期为 12 个月：边界月 = 当前-11（未整月超出），不可删
        boundary_month, start, end = _month_bounds(months_back=11)
        await session.execute(
            text(
                f"CREATE TABLE action_records_{boundary_month} PARTITION OF action_records "
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            )
        )

        @asynccontextmanager
        async def factory() -> AsyncIterator[AsyncSession]:
            yield session

        await run_partition_retention_cycle(factory)

        assert await _table_exists(session, f"action_records_{boundary_month}")
