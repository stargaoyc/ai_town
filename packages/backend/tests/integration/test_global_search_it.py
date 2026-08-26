"""跨角色全局向量检索集成测试

覆盖：
- search_hybrid_global 无 character_id 谓词时命中多个 HASH 分区（多角色），
  评分公式与 search_hybrid 一致（importance 高者排前），带出 character_name
- search_hybrid 单角色检索仍按分区键裁剪，不串扰其他角色
"""

from __future__ import annotations

from datetime import UTC, datetime

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
async def two_characters(it_session: AsyncSession) -> tuple[Character, Character]:
    alice = Character(id=uuid7(), name="爱丽丝")
    bob = Character(id=uuid7(), name="鲍勃")
    it_session.add_all([alice, bob])
    await it_session.flush()
    return alice, bob


class TestSearchHybridGlobal:
    async def test_global_search_hits_all_characters_ranked_by_score(
        self, it_session: AsyncSession, two_characters: tuple[Character, Character]
    ) -> None:
        alice, bob = two_characters
        repo = MemoryRepository(it_session)
        # 爱丽丝：与查询同向但 importance 低
        mem_alice = MemoryEpisode(
            character_id=alice.id,
            content="爱丽丝在咖啡馆写小说",
            embedding=_unit_vec(index=7),
            materialized=True,
            importance=3,
            timestamp=datetime.now(UTC),
        )
        # 鲍勃：与查询同向且 importance 高，应排第一
        mem_bob = MemoryEpisode(
            character_id=bob.id,
            content="鲍勃在咖啡馆谈成了生意",
            embedding=_unit_vec(index=7),
            materialized=True,
            importance=9,
            timestamp=datetime.now(UTC),
        )
        # 鲍勃的正交记忆：召回候选内但不该排到同向记忆之前
        mem_orth = MemoryEpisode(
            character_id=bob.id,
            content="鲍勃在公园散步",
            embedding=_unit_vec(index=100),
            materialized=True,
            importance=5,
            timestamp=datetime.now(UTC),
        )
        it_session.add_all([mem_alice, mem_bob, mem_orth])
        await it_session.flush()

        rows = await repo.search_hybrid_global(_unit_vec(index=7), top_k=10, allow_cross_character=True)

        assert {r["character_id"] for r in rows} == {alice.id, bob.id}, "必须跨角色命中两个分区"
        names = {r["character_name"] for r in rows}
        assert names == {"爱丽丝", "鲍勃"}, "JOIN characters 必须带出角色名"
        assert rows[0]["content"] == "鲍勃在咖啡馆谈成了生意", "同向时 importance 高者得分更高"
        scores = [float(r["final_score"]) for r in rows]
        assert scores == sorted(scores, reverse=True)
        assert all(s > 0 for s in scores)

    async def test_scoped_search_still_prunes_to_single_character(
        self, it_session: AsyncSession, two_characters: tuple[Character, Character]
    ) -> None:
        alice, bob = two_characters
        repo = MemoryRepository(it_session)
        it_session.add_all(
            [
                MemoryEpisode(
                    character_id=alice.id,
                    content="爱丽丝的咖啡记忆",
                    embedding=_unit_vec(index=7),
                    materialized=True,
                    importance=5,
                ),
                MemoryEpisode(
                    character_id=bob.id,
                    content="鲍勃的咖啡记忆",
                    embedding=_unit_vec(index=7),
                    materialized=True,
                    importance=5,
                ),
            ]
        )
        await it_session.flush()

        rows = await repo.search_hybrid(alice.id, _unit_vec(index=7), top_k=10)

        contents = [r["content"] for r in rows]
        assert "爱丽丝的咖啡记忆" in contents
        assert "鲍勃的咖啡记忆" not in contents, "单角色模式必须按 character_id 分区裁剪"
