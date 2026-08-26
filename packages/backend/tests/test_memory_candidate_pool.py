"""混合检索候选池放大倍数测试（R6-L2）

search_hybrid / search_hybrid_global 的候选 LIMIT = top_k ×
RETRIEVAL_CANDIDATE_MULTIPLIER（默认 4，原 2× 仅 20 条候选，召回上界低且无多样性）。
混合公式只能重排 HNSW 召回的候选——本测试钉住「候选池大小」这一检索广度的契约：
- 默认倍数 4 时候选 LIMIT = top_k × 4
- 倍数旋钮可调（monkeypatch 后候选 LIMIT 同步变化）
- 评分公式不改动，仅候选池变宽（参数位验证）
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.repositories.memory_repo import MemoryRepository


def _unit_vec(dim: int = 2048, index: int = 0) -> list[float]:
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


class _FakeDBAPIConn:
    """asyncpg 连接替身：捕获 fetch 的参数（含候选 LIMIT），execute 为 no-op"""

    def __init__(self) -> None:
        self.fetch_args: tuple[Any, ...] | None = None

    async def execute(self, statement: str) -> None:
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_args = args
        return []


class _FakeRaw:
    def __init__(self, dbapi: _FakeDBAPIConn) -> None:
        self.driver_connection = dbapi


class _FakeConnection:
    def __init__(self, dbapi: _FakeDBAPIConn) -> None:
        self._dbapi = dbapi

    async def get_raw_connection(self) -> _FakeRaw:
        return _FakeRaw(self._dbapi)


class _FakeSession:
    def __init__(self, dbapi: _FakeDBAPIConn) -> None:
        self._dbapi = dbapi

    async def connection(self) -> _FakeConnection:
        return _FakeConnection(self._dbapi)


def _repo(dbapi: _FakeDBAPIConn) -> MemoryRepository:
    # session 替身走真实连接链（connection → raw → driver_connection），
    # 只替换最底层的 asyncpg 驱动连接（测试规范 §5.2 cast 模式）
    return MemoryRepository(cast(AsyncSession, _FakeSession(dbapi)))


@pytest.fixture(autouse=True)
def _reset_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "retrieval_candidate_multiplier", 4, raising=False)


@pytest.mark.asyncio
async def test_search_hybrid_candidate_limit_uses_multiplier() -> None:
    """search_hybrid 候选 LIMIT = top_k × 4（默认倍数）；最终 LIMIT 仍为 top_k"""
    dbapi = _FakeDBAPIConn()
    await _repo(dbapi).search_hybrid(uuid4(), _unit_vec(index=3), top_k=5)

    assert dbapi.fetch_args is not None
    candidate_limit, final_limit = dbapi.fetch_args[2], dbapi.fetch_args[3]
    assert candidate_limit == 20
    assert final_limit == 5


@pytest.mark.asyncio
async def test_search_hybrid_candidate_limit_respects_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """RETRIEVAL_CANDIDATE_MULTIPLIER 调大 → 候选池同步放宽"""
    monkeypatch.setattr(settings, "retrieval_candidate_multiplier", 8, raising=False)
    dbapi = _FakeDBAPIConn()
    await _repo(dbapi).search_hybrid(uuid4(), _unit_vec(index=3), top_k=5)

    assert dbapi.fetch_args is not None
    assert dbapi.fetch_args[2] == 40


@pytest.mark.asyncio
async def test_search_hybrid_global_candidate_limit_uses_multiplier() -> None:
    """search_hybrid_global 同样按倍数放宽候选池（跨角色管理面）"""
    dbapi = _FakeDBAPIConn()
    await _repo(dbapi).search_hybrid_global(_unit_vec(index=3), top_k=10, allow_cross_character=True)

    assert dbapi.fetch_args is not None
    candidate_limit, final_limit = dbapi.fetch_args[1], dbapi.fetch_args[2]
    assert candidate_limit == 40
    assert final_limit == 10
