"""RetrievalService 服务层集成测试（round-7 P2-8）

覆盖 search_with_vec 路径：embed + 混合检索 + MEMORY_RETRIEVE_LATENCY 指标。
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode
from src.db.repositories import MemoryRepository
from src.llm.client import LLMClient
from src.memory.retrieval_service import RetrievalService

_CHAR_ID = UUID("01964000-0000-7000-8000-000000000001")
_DIM = 2048


def _unit_vec(index: int = 0) -> list[float]:
    vec = [0.0] * _DIM
    vec[index % _DIM] = 1.0
    return vec


class StubEmbedder:
    async def embed(self, text: str) -> list[float]:
        return _unit_vec(index=7)


@pytest.fixture(autouse=True)
async def _seed_character(it_session: AsyncSession) -> None:
    it_session.add(Character(id=_CHAR_ID, name="测试角色", is_active=True))
    await it_session.flush()


@pytest.fixture(autouse=True)
async def _seed_episodes(it_session: AsyncSession) -> None:
    it_session.add_all(
        [
            MemoryEpisode(
                character_id=_CHAR_ID,
                content="在咖啡店遇到了好朋友",
                embedding=_unit_vec(index=7),  # 与查询向量一致 → sim=1.0
                importance=8,
                materialized=True,
                is_duplicate=False,
                source_type="action",
            ),
            MemoryEpisode(
                character_id=_CHAR_ID,
                content="今天去图书馆学习",
                embedding=_unit_vec(index=100),
                importance=9,
                materialized=True,
                is_duplicate=False,
                source_type="action",
            ),
            MemoryEpisode(
                character_id=_CHAR_ID,
                content="吃了一个大蛋糕",
                embedding=_unit_vec(index=200),
                importance=5,
                materialized=True,
                is_duplicate=False,
                source_type="action",
            ),
        ]
    )
    await it_session.flush()


class TestRetrievalService:
    async def test_search_with_vec_returns_ranked_results(self, it_session: AsyncSession) -> None:
        repo = MemoryRepository(it_session)
        llm = cast(LLMClient, StubEmbedder())
        svc = RetrievalService(llm, repo)

        results = await svc.search_with_vec(_CHAR_ID, _unit_vec(index=7), top_k=3)

        assert len(results) == 3
        assert all(r["content"] for r in results)
        assert all(r["final_score"] > 0 for r in results)
        # 完全命中的记忆应排第一
        assert results[0]["content"] == "在咖啡店遇到了好朋友"
        assert results[0]["sim_score"] > 0.99

    async def test_search_with_vec_returns_empty_when_no_materialized(self, it_session: AsyncSession) -> None:
        other_id = uuid7()
        it_session.add(Character(id=other_id, name="无记忆角色", is_active=True))
        await it_session.flush()
        repo = MemoryRepository(it_session)
        svc = RetrievalService(cast(LLMClient, StubEmbedder()), repo)

        results = await svc.search_with_vec(other_id, _unit_vec(index=7), top_k=5)

        assert len(results) == 0

    async def test_top_k_respected(self, it_session: AsyncSession) -> None:
        repo = MemoryRepository(it_session)
        svc = RetrievalService(cast(LLMClient, StubEmbedder()), repo)

        results = await svc.search_with_vec(_CHAR_ID, _unit_vec(index=7), top_k=2)

        assert len(results) == 2
