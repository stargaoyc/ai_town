"""Person Memory 热度衰减集成测试（T1）"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, PersonMemory
from src.scheduler.loops import run_person_memory_heat_decay


@asynccontextmanager
async def _ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """共享会话包装为可注入工厂（同 reconcile IT 模式）"""
    yield session


@pytest_asyncio.fixture
async def pm_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="热度衰减角色")
    it_session.add(char)
    await it_session.flush()
    return char


class TestRunPersonMemoryHeatDecay:
    async def test_stale_halved_fresh_untouched(self, it_session: AsyncSession, pm_character: Character) -> None:
        stale = PersonMemory(
            character_id=pm_character.id,
            user_id="user_old",
            content="",
            heat=10,
            last_interaction_at=datetime.now(UTC) - timedelta(days=20),
        )
        fresh = PersonMemory(
            character_id=pm_character.id,
            user_id="user_new",
            content="",
            heat=8,
            last_interaction_at=datetime.now(UTC) - timedelta(hours=1),
        )
        it_session.add_all([stale, fresh])
        await it_session.flush()

        rows = await run_person_memory_heat_decay(lambda: _ctx(it_session))
        assert rows == 1

        await it_session.refresh(stale)
        await it_session.refresh(fresh)
        assert stale.heat == 5  # 10 减半
        assert fresh.heat == 8  # 新鲜交互不衰减

    async def test_zero_heat_stays_zero(self, it_session: AsyncSession, pm_character: Character) -> None:
        zero = PersonMemory(
            character_id=pm_character.id,
            user_id="user_zero",
            content="",
            heat=0,
            last_interaction_at=datetime.now(UTC) - timedelta(days=30),
        )
        it_session.add(zero)
        await it_session.flush()

        rows = await run_person_memory_heat_decay(lambda: _ctx(it_session))
        assert rows == 0
        await it_session.refresh(zero)
        assert zero.heat == 0
