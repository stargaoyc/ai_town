"""EmbeddingWorker 数组输入批量测试（R6-L1）

验证 _process_batch 的批量语义：
- N 行记忆恰好一次 embed_batch（数组输入单次往返），而非逐条 embed
- 整批 API 失败 → 逐行按退避记账失败（保留重试/熔断语义），不丢弃整批
- 改写式去重仍逐行执行（每行向量参与余弦比对），与批量调用并存
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src.config import settings
from src.llm import LLMClient
from src.memory.embedding_worker import EmbeddingWorker


@dataclass
class _FakeEpisode:
    id: str
    character_id: str
    content: str
    timestamp: datetime
    fail_count: int = 0


class _FakeLLM:
    """记录 embed_batch 调用参数，按输入顺序返回第 i 条以 [i.0]*dim 开头的定长向量"""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.batch_calls: list[list[str]] = []
        self.fail_batch = False

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls.append(list(texts))
        if self.fail_batch:
            raise RuntimeError("embedding api down")
        return [[float(i)] * self.dim for i in range(len(texts))]


class _FakeRepo:
    """替换 MemoryRepository：只实现 worker 用到的 4 个方法，副作用可断言"""

    def __init__(self, episodes: list[_FakeEpisode], dup_indices: set[int] | None = None) -> None:
        self.episodes = episodes
        self.dup_indices = dup_indices or set()
        self.updated: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.deduped: list[str] = []
        self.dup_check_calls = 0

    async def fetch_unmaterialized(self, limit: int) -> list[_FakeEpisode]:
        return self.episodes[:limit]

    async def find_paraphrase_duplicate(
        self,
        character_id: str,
        embedding: list[float],
        before_ts: datetime,
        window_hours: int,
        similarity_threshold: float,
    ) -> bool:
        self.dup_check_calls += 1
        return round(embedding[0]) in self.dup_indices

    async def mark_duplicate(self, episode_id: str, character_id: str) -> None:
        self.deduped.append(episode_id)

    async def update_embedding(self, episode_id: str, character_id: str, embedding: list[float]) -> None:
        self.updated.append(episode_id)

    async def mark_embedding_failed(self, episode_id: str, character_id: str, error: str) -> None:
        self.failed.append((episode_id, error))


class _FakeSession:
    """session_factory 产出的会话：仅需满足 worker 的 commit 调用"""

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


@asynccontextmanager
async def _session_ctx(session: _FakeSession) -> AsyncIterator[_FakeSession]:
    yield session


def _make_episodes(count: int) -> list[_FakeEpisode]:
    return [
        _FakeEpisode(
            id=f"episode-{i}",
            character_id="char-1",
            content=f"记忆内容{i}",
            timestamp=datetime.now(UTC),
        )
        for i in range(count)
    ]


def _worker(llm: _FakeLLM, repo: _FakeRepo) -> EmbeddingWorker:
    # _FakeLLM 只实现 embed_batch 协议（测试规范 §5.2 cast 模式）；
    # session_factory 产出的 _FakeSession 由 monkeypatch 的 MemoryRepository 忽略
    return EmbeddingWorker(
        session_factory=cast(Any, lambda: _session_ctx(_FakeSession())),
        llm_client=cast(LLMClient, llm),
        batch_size=5,
    )


def _install_fake_repo(monkeypatch: pytest.MonkeyPatch, repo: _FakeRepo) -> None:
    monkeypatch.setattr("src.memory.embedding_worker.MemoryRepository", lambda session: repo)


@pytest.mark.asyncio
async def test_batch_single_array_call_with_all_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 行记忆 → 恰好 1 次 embed_batch 调用且携带全部文本；逐行落库"""
    episodes = _make_episodes(5)
    repo = _FakeRepo(episodes)
    llm = _FakeLLM()
    _install_fake_repo(monkeypatch, repo)
    worker = _worker(llm, repo)

    processed = await worker._process_batch()

    assert processed == 5
    assert llm.batch_calls == [[e.content for e in episodes]], "必须是单次数组调用携带全部文本"
    assert len(repo.updated) == 5
    assert repo.failed == []
    assert [u for u in repo.updated] == [e.id for e in episodes]


@pytest.mark.asyncio
async def test_batch_failure_marks_each_row_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embed_batch 整批失败 → 每行各记一次失败（退避/熔断逐行保留），不中断周期"""
    episodes = _make_episodes(5)
    repo = _FakeRepo(episodes)
    llm = _FakeLLM()
    llm.fail_batch = True
    _install_fake_repo(monkeypatch, repo)
    worker = _worker(llm, repo)

    processed = await worker._process_batch()

    assert processed == 5
    assert repo.updated == []
    assert len(repo.failed) == 5, "整批失败必须逐行记账"
    assert {eid for eid, _err in repo.failed} == {e.id for e in episodes}
    assert all("embedding_batch_failed" in err for _eid, err in repo.failed)


@pytest.mark.asyncio
async def test_dedup_runs_per_row_alongside_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """去重逐行执行：第 3 条（index=2）判定重复 → mark_duplicate 而非 update"""
    episodes = _make_episodes(5)
    repo = _FakeRepo(episodes, dup_indices={2})
    llm = _FakeLLM()
    _install_fake_repo(monkeypatch, repo)
    monkeypatch.setattr(settings, "memory_dedup_enabled", True)
    worker = _worker(llm, repo)

    processed = await worker._process_batch()

    assert processed == 5
    assert len(llm.batch_calls) == 1, "去重逐行执行不得退化为逐条 embed"
    assert repo.dup_check_calls == 5
    assert repo.deduped == [episodes[2].id]
    assert episodes[2].id not in repo.updated
    assert len(repo.updated) == 4
