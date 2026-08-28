"""src/security/startup_checks.py - check_embedding_dim 单元测试

覆盖（R5-L2）：
- memory_episodes.embedding 与 reflections.embedding 双列同时校验
- 任一列维度错配时 fail-fast 且错误信息指名表
- 列缺失（全新库未跑迁移）时告警跳过不阻断

覆盖（R6-L4，probe_embedding_dimension）：
- 探针成功但维度 != EMBEDDING_DIM → fail-fast
- 探针维度一致 → 通过
- EMBEDDING_PROBE_ENABLED=false → 跳过（不调用模型）
- 探针调用失败（网络/上游不可达）→ 告警放行，不抛异常
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.security.startup_checks import check_embedding_dim, probe_embedding_dimension


class _FakeResult:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


def _make_session_factory(results: list[str | None]) -> Any:
    """按调用顺序回放 format_type 结果的异步会话工厂"""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_FakeResult(v) for v in results])

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


class _ProbeLLM:
    """探针替身：记录 embed 调用次数；按构造参数返回向量或抛错"""

    def __init__(self, vec: list[float] | None = None, error: str | None = None) -> None:
        self._vec = vec
        self._error = error
        self.embed_calls = 0

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        if self._error:
            raise RuntimeError(self._error)
        assert self._vec is not None
        return self._vec


@pytest.mark.asyncio
async def test_check_embedding_dim_passes_when_both_columns_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """双列均为 halfvec(2048) 时通过且不抛异常"""
    monkeypatch.setattr(settings, "embedding_dim", 2048, raising=False)
    factory = _make_session_factory(["halfvec(2048)", "halfvec(2048)"])
    await check_embedding_dim(factory)


@pytest.mark.asyncio
async def test_check_embedding_dim_fails_naming_mismatched_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """reflections 维度错配时报错指名 reflections 表"""
    monkeypatch.setattr(settings, "embedding_dim", 2048, raising=False)
    factory = _make_session_factory(["halfvec(2048)", "halfvec(1024)"])
    with pytest.raises(RuntimeError, match="reflections"):
        await check_embedding_dim(factory)


@pytest.mark.asyncio
async def test_check_embedding_dim_fails_on_memory_episodes_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_episodes 维度错配时报错指名 memory_episodes 表"""
    monkeypatch.setattr(settings, "embedding_dim", 2048, raising=False)
    factory = _make_session_factory(["vector(3072)", "halfvec(2048)"])
    with pytest.raises(RuntimeError, match="memory_episodes"):
        await check_embedding_dim(factory)


@pytest.mark.asyncio
async def test_check_embedding_dim_skips_missing_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """两列均不存在（全新库未迁移）时不抛异常"""
    monkeypatch.setattr(settings, "embedding_dim", 2048, raising=False)
    factory = _make_session_factory([None, None])
    await check_embedding_dim(factory)


class TestProbeEmbeddingDimension:
    """R6-L4：Embedding 实时维度探针（探针专用 settings 隔离在类内，不污染上面的 DDL 校验）"""

    @pytest.fixture(autouse=True)
    def _probe_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "embedding_probe_enabled", True, raising=False)
        monkeypatch.setattr(settings, "embedding_dim", 4, raising=False)
        monkeypatch.setattr(settings, "model_embedding", "fake-probe-model", raising=False)

    @pytest.mark.asyncio
    async def test_wrong_dimension_fails_fast(self) -> None:
        """探针成功但实时输出维度与 EMBEDDING_DIM 不一致 → fail-fast 且点名模型"""
        llm = _ProbeLLM(vec=[1.0, 2.0])  # 维度 2，与配置的 4 不一致
        with pytest.raises(RuntimeError, match="MODEL_EMBEDDING=fake-probe-model"):
            await probe_embedding_dimension(llm)

    @pytest.mark.asyncio
    async def test_matching_dimension_passes(self) -> None:
        """探针输出维度与 EMBEDDING_DIM 一致 → 不抛异常"""
        llm = _ProbeLLM(vec=[1.0, 2.0, 3.0, 4.0])
        await probe_embedding_dimension(llm)
        assert llm.embed_calls == 1

    @pytest.mark.asyncio
    async def test_disabled_skips_model_call(self) -> None:
        """EMBEDDING_PROBE_ENABLED=false → 直接跳过，不调用模型"""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(settings, "embedding_probe_enabled", False, raising=False)
        try:
            llm = _ProbeLLM(vec=[1.0, 2.0])
            await probe_embedding_dimension(llm)
            assert llm.embed_calls == 0
        finally:
            monkeypatch.undo()

    @pytest.mark.asyncio
    async def test_network_failure_continues_boot(self) -> None:
        """探针调用失败（网络/上游不可达）→ 告警放行，不抛异常"""
        llm = _ProbeLLM(error="connection refused")
        await probe_embedding_dimension(llm)
        assert llm.embed_calls == 1
