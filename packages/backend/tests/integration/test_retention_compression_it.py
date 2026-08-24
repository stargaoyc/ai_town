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
