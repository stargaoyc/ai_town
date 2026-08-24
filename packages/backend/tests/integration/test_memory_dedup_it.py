"""改写式记忆去重集成测试 - 向量余弦比对（B1，复审 N7 正确路径）"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode
from src.db.repositories.memory_repo import MemoryRepository


def _unit_vec(dim: int = 2048, index: int = 0) -> list[float]:
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


@pytest_asyncio.fixture
async def dedup_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="去重测试角色")
    it_session.add(char)
    await it_session.flush()
    return char


async def _seed_materialized(
    session: AsyncSession,
    character_id: UUID,
    content: str,
    embedding: list[float],
    hours_ago: float = 1.0,
) -> MemoryEpisode:
    episode = MemoryEpisode(
        character_id=character_id,
        content=content,
        importance=5,
        embedding=embedding,
        materialized=True,
        timestamp=datetime.now(UTC) - timedelta(hours=hours_ago),
    )
    session.add(episode)
    await session.flush()
    return episode


class TestFindParaphraseDuplicate:
    async def test_near_identical_vector_detected(self, it_session: AsyncSession, dedup_character: Character) -> None:
        vec = _unit_vec(index=7)
        await _seed_materialized(it_session, dedup_character.id, "在咖啡店和艾莉丝聊天", vec)

        repo = MemoryRepository(it_session)
        assert (
            await repo.find_paraphrase_duplicate(
                dedup_character.id, vec.copy(), before_ts=datetime.now(UTC), window_hours=24
            )
            is True
        )

    async def test_orthogonal_vector_not_flagged(self, it_session: AsyncSession, dedup_character: Character) -> None:
        await _seed_materialized(it_session, dedup_character.id, "在图书馆读书", _unit_vec(index=11))

        repo = MemoryRepository(it_session)
        assert (
            await repo.find_paraphrase_duplicate(
                dedup_character.id,
                _unit_vec(index=200),
                before_ts=datetime.now(UTC),
                window_hours=24,
            )
            is False
        )

    async def test_source_outside_window_ignored(self, it_session: AsyncSession, dedup_character: Character) -> None:
        old = await _seed_materialized(it_session, dedup_character.id, "两天前的经历", _unit_vec(index=5))
        old.timestamp = datetime.now(UTC) - timedelta(hours=48)
        await it_session.flush()

        repo = MemoryRepository(it_session)
        assert (
            await repo.find_paraphrase_duplicate(
                dedup_character.id,
                _unit_vec(index=5),
                before_ts=datetime.now(UTC),
                window_hours=24,
            )
            is False
        )

    async def test_duplicate_marked_excluded_from_reflection_and_search(
        self, it_session: AsyncSession, dedup_character: Character
    ) -> None:
        original = await _seed_materialized(it_session, dedup_character.id, "原始记忆", _unit_vec(index=9))
        dup_row = MemoryEpisode(
            character_id=dedup_character.id,
            content="原始记忆的复述",
            importance=5,
            timestamp=datetime.now(UTC),
            is_duplicate=True,
            materialized=True,
        )
        it_session.add(dup_row)
        await it_session.flush()

        repo = MemoryRepository(it_session)
        await repo.mark_duplicate(dup_row.id, dedup_character.id)

        # 反思队列排除重复行
        unreflected = await repo.fetch_unreflected(dedup_character.id, limit=50)
        ids = {e.id for e in unreflected}
        assert original.id in ids
        assert dup_row.id not in ids

        # 混合检索排除重复行（即使其 materialized=TRUE）
        rows = await repo.search_hybrid(dedup_character.id, _unit_vec(index=9), top_k=10)
        contents = [r["content"] for r in rows]
        assert "原始记忆" in contents or any("原始" in c for c in contents)
        assert all("复述" not in c for c in contents)
