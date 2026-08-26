"""记忆压缩归档集成测试 - retention 两阶段（先压缩后删除，归档豁免）"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode
from src.scheduler.loops import run_memory_retention_cycle


class StubLLM:
    async def chat(self, prompt: str, model: str | None = None) -> str:
        return '{"digest": "当月以日常琐事为主，穿插一次冒险"}'


class StubPrompts:
    def render(self, name: str, **kwargs: Any) -> str:
        return f"[{name}] {kwargs.get('memories_text', '')}"


@asynccontextmanager
async def _session_ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """把共享 it_session 包装为可注入的会话工厂（同 reconcile IT 模式）"""
    yield session


@pytest_asyncio.fixture
async def archive_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="归档测试角色")
    it_session.add(char)
    await it_session.flush()
    return char


async def _seed_old(
    session: AsyncSession,
    character_id: Any,
    count: int,
    importance: int,
    days: int,
    content: str = "琐碎日常",
) -> None:
    for i in range(count):
        session.add(
            MemoryEpisode(
                character_id=character_id,
                content=f"{content}{i}",
                importance=importance,
                timestamp=datetime.now(UTC) - timedelta(days=days),
                source_type="action",
            )
        )
    await session.flush()


class TestRetentionCompression:
    async def test_old_low_importance_compressed_then_deleted(
        self, it_session: AsyncSession, archive_character: Character, monkeypatch: Any
    ) -> None:
        import src.runtime as runtime_mod

        monkeypatch.setattr(runtime_mod, "get_llm", lambda: StubLLM())
        monkeypatch.setattr(runtime_mod, "get_prompts", lambda: StubPrompts())

        # 大组（>=min_batch）：走压缩归档路径
        await _seed_old(it_session, archive_character.id, count=6, importance=2, days=120)
        # 小组（<min_batch）：无需摘要，阶段二直接删除
        await _seed_old(it_session, archive_character.id, count=3, importance=3, days=110)
        # 高重要性记忆不受影响
        await _seed_old(it_session, archive_character.id, count=2, importance=9, days=120)
        await it_session.flush()

        archived_groups, deleted_rows = await run_memory_retention_cycle(lambda: _session_ctx(it_session))

        assert archived_groups >= 1
        assert deleted_rows >= 3

        rows = list(
            (
                await it_session.execute(
                    select(MemoryEpisode).where(MemoryEpisode.character_id == archive_character.id)
                )
            ).scalars()
        )
        archives = [r for r in rows if r.source_type == "archive"]
        assert len(archives) == 1
        assert "[归档]" in archives[0].content
        assert archives[0].importance == 3
        # 低价值原始行全部消失（压缩组被删、小组直删）；高重要性保留
        low_left = [r for r in rows if r.source_type == "action" and r.importance <= 3]
        assert low_left == []
        assert any(r.importance == 9 for r in rows)

    async def test_archive_rows_exempt_from_deletion(
        self, it_session: AsyncSession, archive_character: Character, monkeypatch: Any
    ) -> None:
        """归档行本身不再被后续周期删除"""
        import src.runtime as runtime_mod

        monkeypatch.setattr(runtime_mod, "get_llm", lambda: StubLLM())
        monkeypatch.setattr(runtime_mod, "get_prompts", lambda: StubPrompts())

        it_session.add(
            MemoryEpisode(
                character_id=archive_character.id,
                content="[归档] 2026-07：旧摘要",
                importance=3,
                timestamp=datetime.now(UTC) - timedelta(days=400),
                source_type="archive",
            )
        )
        await it_session.flush()

        await run_memory_retention_cycle(lambda: _session_ctx(it_session))

        rows = list(
            (
                await it_session.execute(
                    select(MemoryEpisode).where(MemoryEpisode.character_id == archive_character.id)
                )
            ).scalars()
        )
        assert any(r.source_type == "archive" for r in rows)

    async def test_llm_failure_preserves_originals(
        self, it_session: AsyncSession, archive_character: Character, monkeypatch: Any
    ) -> None:
        """不变量：LLM 失败时绝不未压缩先删除"""
        import src.runtime as runtime_mod

        class FailingLLM:
            async def chat(self, prompt: str, model: str | None = None) -> str:
                raise RuntimeError("llm down")

        monkeypatch.setattr(runtime_mod, "get_llm", lambda: FailingLLM())
        monkeypatch.setattr(runtime_mod, "get_prompts", lambda: StubPrompts())

        await _seed_old(it_session, archive_character.id, count=6, importance=2, days=120)
        await it_session.flush()

        await run_memory_retention_cycle(lambda: _session_ctx(it_session))

        rows = list(
            (
                await it_session.execute(
                    select(MemoryEpisode).where(MemoryEpisode.character_id == archive_character.id)
                )
            ).scalars()
        )
        # 压缩失败的组原样保留（下周期重试）
        assert sum(1 for r in rows if r.source_type == "action") == 6

    async def test_archive_aged_by_created_at_not_event_timestamp(
        self, it_session: AsyncSession, archive_character: Character
    ) -> None:
        """归档保留期按创建时间计龄，不继承原事件时间戳（round-5 M2）

        旧积压压缩出的归档（timestamp 很老、刚诞生）必须存活；
        反之 created_at 超期的归档即使 timestamp 很新也应删除。
        """
        from src.config import settings as _settings
        from src.scheduler.loops import run_cognition_retention_cycle

        now = datetime.now(UTC)
        retention = timedelta(days=_settings.archive_episode_retention_days)
        it_session.add_all(
            [
                MemoryEpisode(
                    character_id=archive_character.id,
                    content="[归档] 2025-01：旧积压摘要",
                    importance=3,
                    timestamp=now - timedelta(days=400),
                    created_at=now - timedelta(days=1),
                    source_type="archive",
                ),
                MemoryEpisode(
                    character_id=archive_character.id,
                    content="[归档] 新鲜事件摘要",
                    importance=3,
                    timestamp=now - timedelta(days=1),
                    created_at=now - retention - timedelta(days=5),
                    source_type="archive",
                ),
            ]
        )
        await it_session.flush()

        deleted = await run_cognition_retention_cycle(lambda: _session_ctx(it_session))

        assert deleted["archive_episodes"] == 1
        rows = list(
            (
                await it_session.execute(
                    select(MemoryEpisode).where(MemoryEpisode.character_id == archive_character.id)
                )
            ).scalars()
        )
        survivors = [r.content for r in rows if r.source_type == "archive"]
        assert survivors == ["[归档] 2025-01：旧积压摘要"]

    async def test_terminal_plans_pruned_active_and_recent_kept(
        self, it_session: AsyncSession, archive_character: Character, monkeypatch: Any
    ) -> None:
        """R5-L5：终态计划按 updated_at 超期修剪；active 与未超期终态行保留"""
        from src.config import settings as _settings
        from src.db.models import Plan
        from src.scheduler.loops import run_cognition_retention_cycle

        monkeypatch.setattr(_settings, "plans_retention_days", 30)
        now = datetime.now(UTC)
        old = now - timedelta(days=40)
        recent = now - timedelta(days=1)

        def plan(title: str, status: str, updated_at: datetime) -> Plan:
            return Plan(
                character_id=archive_character.id, type="daily", title=title, status=status, updated_at=updated_at
            )

        it_session.add_all(
            [
                plan("旧完成", "completed", old),
                plan("旧放弃", "abandoned", old),
                plan("旧过期", "expired", old),
                plan("新完成", "completed", recent),
                plan("旧活跃", "active", old),
            ]
        )
        await it_session.flush()

        deleted = await run_cognition_retention_cycle(lambda: _session_ctx(it_session))

        assert deleted["plans"] == 3
        remaining = list((await it_session.execute(select(Plan.title))).scalars())
        assert sorted(remaining) == ["旧活跃", "新完成"]

    async def test_plans_pruning_disabled_when_retention_zero(
        self, it_session: AsyncSession, archive_character: Character, monkeypatch: Any
    ) -> None:
        """plans_retention_days=0 表示永久保留，跳过修剪"""
        from src.config import settings as _settings
        from src.db.models import Plan
        from src.scheduler.loops import run_cognition_retention_cycle

        monkeypatch.setattr(_settings, "plans_retention_days", 0)
        it_session.add(
            Plan(
                character_id=archive_character.id,
                type="daily",
                title="远古完成",
                status="completed",
                updated_at=datetime.now(UTC) - timedelta(days=3650),
            )
        )
        await it_session.flush()

        deleted = await run_cognition_retention_cycle(lambda: _session_ctx(it_session))

        assert deleted["plans"] == 0
        remaining = list((await it_session.execute(select(Plan.title))).scalars())
        assert remaining == ["远古完成"]
