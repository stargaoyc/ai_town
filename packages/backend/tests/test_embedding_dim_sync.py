"""src/db/embedding_dim_sync.py - 幂等维度同步单测

覆盖：
- 维度一致：跳过（零 DDL）
- 维度不一致：DROP 索引 → 清空旧向量 → ALTER 类型 → 重建索引 四步
- 列不存在：补建列 + 索引
- 返回变更列表语义
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.db.embedding_dim_sync import sync_embedding_dim


class _FakeResult:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


def _make_session(dims: list[str | None]) -> tuple[Any, list[str]]:
    """返回 (session, executed_sql_list)；dims 按表顺序提供 format_type 结果"""
    session = AsyncMock()
    executed: list[str] = []
    dim_iter = iter([_FakeResult(d) for d in dims])

    async def fake_execute(stmt: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(stmt))
        if "format_type" in str(stmt):
            return next(dim_iter)
        return _FakeResult(None)

    session.execute = AsyncMock(side_effect=fake_execute)

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory, executed


class TestSyncEmbeddingDim:
    async def test_idempotent_when_dim_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """维度一致：无任何 DDL，返回空变更列表"""
        factory, executed = _make_session(["halfvec(2048)", "halfvec(2048)"])
        monkeypatch.setattr("src.db.embedding_dim_sync.settings.embedding_dim", 2048)

        changes = await sync_embedding_dim(factory)

        assert changes == []
        # 只应有两个物理维度查询，无 DDL
        assert all("format_type" in sql for sql in executed)
        assert len(executed) == 2

    async def test_rebuild_when_dim_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """维度不一致：对每张表执行 DROP→UPDATE→ALTER→CREATE 四步"""
        factory, executed = _make_session(["halfvec(2048)", "halfvec(2048)"])
        monkeypatch.setattr("src.db.embedding_dim_sync.settings.embedding_dim", 4096)

        changes = await sync_embedding_dim(factory)

        assert len(changes) == 2
        assert all("halfvec(2048) -> halfvec(4096)" in c for c in changes)

        ddl = [sql for sql in executed if "format_type" not in sql]
        assert len(ddl) == 8  # 2 表 × 4 步
        joined = "\n".join(ddl)
        assert "DROP INDEX IF EXISTS idx_mem_embedding_hnsw" in joined
        assert "DROP INDEX IF EXISTS idx_reflections_embedding" in joined
        assert "UPDATE memory_episodes SET embedding = NULL" in joined
        assert "UPDATE reflections SET embedding = NULL" in joined
        assert "ALTER TABLE memory_episodes ALTER COLUMN embedding TYPE halfvec(4096)" in joined
        assert "ALTER TABLE reflections ALTER COLUMN embedding TYPE halfvec(4096)" in joined
        assert "CREATE INDEX idx_mem_embedding_hnsw" in joined
        assert "CREATE INDEX idx_reflections_embedding" in joined

    async def test_create_column_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """列不存在（全新库未跑迁移）：补建列 + 索引"""
        factory, executed = _make_session([None, "halfvec(4096)"])
        monkeypatch.setattr("src.db.embedding_dim_sync.settings.embedding_dim", 4096)

        changes = await sync_embedding_dim(factory)

        assert len(changes) == 1
        assert "created halfvec(4096)" in changes[0]
        ddl = [sql for sql in executed if "format_type" not in sql]
        assert any("ALTER TABLE memory_episodes ADD COLUMN embedding halfvec(4096)" in s for s in ddl)
        assert any("CREATE INDEX idx_mem_embedding_hnsw" in s for s in ddl)

    async def test_mixed_tables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """一表一致一表不一致：只重建不一致的表"""
        factory, executed = _make_session(["halfvec(4096)", "halfvec(2048)"])
        monkeypatch.setattr("src.db.embedding_dim_sync.settings.embedding_dim", 4096)

        changes = await sync_embedding_dim(factory)

        assert len(changes) == 1
        assert "reflections" in changes[0]
        ddl = [sql for sql in executed if "format_type" not in sql]
        # 只有 reflections 被重建（memory_episodes 已一致）
        assert not any("UPDATE memory_episodes" in s for s in ddl)
        assert any("UPDATE reflections" in s for s in ddl)
