"""EmbeddingWorker 去重分支集成测试（T5）

验证 _process_batch 内的去重路径：
- 近似向量 -> is_duplicate=TRUE、不落 embedding、不计入 failed
- 正交向量 -> 正常 materialize
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.config import settings
from src.db.models import Character, MemoryEpisode
from src.llm import LLMClient
from src.memory.embedding_worker import EmbeddingWorker


def _unit_vec(dim: int = 2048, index: int = 0) -> list[float]:
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


class StubLLM:
    """embed 恒返回固定向量，用于控制比对结果"""

    def __init__(self, vec: list[float]) -> None:
        self._vec = vec

    async def embed(self, content: str) -> list[float]:
        return self._vec


@asynccontextmanager
async def _ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


@pytest_asyncio.fixture
async def dedup_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="worker去重角色")
    it_session.add(char)
    await it_session.flush()
    return char


def _worker(it_session: AsyncSession, vec: list[float]) -> EmbeddingWorker:
    # StubLLM 只实现 embed 协议（测试规范 §5.2 cast 模式）
    return EmbeddingWorker(
        session_factory=lambda: _ctx(it_session),
        llm_client=cast(LLMClient, StubLLM(vec)),
        batch_size=10,
    )


class TestWorkerDedupBranch:
    async def test_near_duplicate_marked_not_materialized_with_vector(
        self, it_session: AsyncSession, dedup_character: Character
    ) -> None:
        # 已向量化历史：vec index=7
        it_session.add(
            MemoryEpisode(
                character_id=dedup_character.id,
                content="在咖啡店和艾莉丝聊天",
                importance=5,
                embedding=_unit_vec(index=7),
                materialized=True,
                timestamp=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        # 待向量化新记忆：内容不同但 embed 结果近似 -> 应判重复
        pending = MemoryEpisode(
            character_id=dedup_character.id,
            content="今天在咖啡店跟艾莉丝聊了会天",
            importance=5,
            timestamp=datetime.now(UTC),
        )
        it_session.add(pending)
        await it_session.flush()

        processed = await _worker(it_session, _unit_vec(index=7))._process_batch()
        assert processed >= 1

        await it_session.refresh(pending)
        assert pending.is_duplicate is True
        assert pending.materialized is True  # 防止 worker 无限重拉
        assert pending.embedding is None  # 重复行不落向量

    async def test_orthogonal_vector_normal_materialize(
        self, it_session: AsyncSession, dedup_character: Character
    ) -> None:
        it_session.add(
            MemoryEpisode(
                character_id=dedup_character.id,
                content="在图书馆读书",
                importance=5,
                embedding=_unit_vec(index=11),
                materialized=True,
                timestamp=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        pending = MemoryEpisode(
            character_id=dedup_character.id,
            content="去公园跑了五公里",
            importance=5,
            timestamp=datetime.now(UTC),
        )
        it_session.add(pending)
        await it_session.flush()

        await _worker(it_session, _unit_vec(index=300))._process_batch()

        await it_session.refresh(pending)
        assert pending.is_duplicate is False
        assert pending.materialized is True
        assert pending.embedding is not None

    async def test_dedup_disabled_falls_through(
        self, it_session: AsyncSession, dedup_character: Character, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(settings, "memory_dedup_enabled", False)
        it_session.add(
            MemoryEpisode(
                character_id=dedup_character.id,
                content="历史记忆",
                importance=5,
                embedding=_unit_vec(index=7),
                materialized=True,
                timestamp=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        pending = MemoryEpisode(
            character_id=dedup_character.id,
            content="相似的新记忆",
            importance=5,
            timestamp=datetime.now(UTC),
        )
        it_session.add(pending)
        await it_session.flush()

        await _worker(it_session, _unit_vec(index=7))._process_batch()

        await it_session.refresh(pending)
        assert pending.is_duplicate is False
        assert pending.embedding is not None
