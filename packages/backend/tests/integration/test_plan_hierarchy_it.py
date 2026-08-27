"""Plan 层级体系集成测试 - LLM 新建计划落库 + daily 滚动过期（B2/B3）"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.config import settings
from src.core.character.plan_applier import PlanChangeApplier
from src.db.models import Character, Plan
from src.db.repositories.plan_repo import PlanRepository


@asynccontextmanager
async def _session_ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """共享会话包装为可注入工厂（同 reconcile IT 模式）"""
    yield session


class TestCreatePlansIT:
    @pytest_asyncio.fixture
    async def plan_character(self, it_session: AsyncSession) -> Character:
        char = Character(id=uuid7(), name="建计划角色")
        it_session.add(char)
        await it_session.flush()
        return char

    async def test_created_plans_scoped_to_character(self, it_session: AsyncSession, plan_character: Character) -> None:
        repo = PlanRepository(it_session)
        created = await PlanChangeApplier.create_plans(
            repo,
            plan_character.id,
            [
                {"title": "今日还书", "type": "daily", "priority": 4},
                {"title": "", "type": "daily"},
                {"title": "三个月内通过考试", "type": "long_term"},
            ],
        )

        assert created == 2
        plans = list((await it_session.execute(select(Plan).where(Plan.character_id == plan_character.id))).scalars())
        assert {p.type for p in plans} == {"daily", "long_term"}
        assert all(p.status == "active" for p in plans)


class TestDailyPlanExpiryIT:
    async def test_stale_daily_expired_fresh_kept(self, it_session: AsyncSession, monkeypatch: Any) -> None:
        char = Character(id=uuid7(), name="过期测试角色")
        it_session.add(char)
        await it_session.flush()

        stale = Plan(character_id=char.id, type="daily", title="昨天的计划", status="active")
        fresh = Plan(character_id=char.id, type="daily", title="今天的计划", status="active")
        long_term = Plan(character_id=char.id, type="long_term", title="长期目标", status="active")
        it_session.add_all([stale, fresh, long_term])
        await it_session.flush()
        # 手动把 stale 的 created_at 拨回 48 小时前（绕过 server_default）
        stale.created_at = datetime.now(UTC) - timedelta(hours=48)
        await it_session.flush()

        monkeypatch.setattr(settings, "daily_plan_ttl_hours", 24)

        from src.scheduler.loops import expire_daily_plans

        expired = await expire_daily_plans(lambda: _session_ctx(it_session))
        assert expired >= 1

        await it_session.refresh(stale)
        await it_session.refresh(fresh)
        await it_session.refresh(long_term)
        assert stale.status == "expired"
        assert fresh.status == "active"
        assert long_term.status == "active"
