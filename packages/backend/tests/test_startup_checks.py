"""src/security/startup_checks.py - check_embedding_dim 单元测试

覆盖（R5-L2）：
- memory_episodes.embedding 与 reflections.embedding 双列同时校验
- 任一列维度错配时 fail-fast 且错误信息指名表
- 列缺失（全新库未跑迁移）时告警跳过不阻断
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.security.startup_checks import check_embedding_dim


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


@pytest.mark.asyncio
async def test_check_embedding_dim_passes_when_both_columns_match() -> None:
    """双列均为 halfvec(2048) 时通过且不抛异常"""
    factory = _make_session_factory(["halfvec(2048)", "halfvec(2048)"])
    await check_embedding_dim(factory)


@pytest.mark.asyncio
async def test_check_embedding_dim_fails_naming_mismatched_table() -> None:
    """reflections 维度错配时报错指名 reflections 表"""
    factory = _make_session_factory(["halfvec(2048)", "halfvec(1024)"])
    with pytest.raises(RuntimeError, match="reflections"):
        await check_embedding_dim(factory)


@pytest.mark.asyncio
async def test_check_embedding_dim_fails_on_memory_episodes_mismatch() -> None:
    """memory_episodes 维度错配时报错指名 memory_episodes 表"""
    factory = _make_session_factory(["vector(3072)", "halfvec(2048)"])
    with pytest.raises(RuntimeError, match="memory_episodes"):
        await check_embedding_dim(factory)


@pytest.mark.asyncio
async def test_check_embedding_dim_skips_missing_columns() -> None:
    """两列均不存在（全新库未迁移）时不抛异常"""
    factory = _make_session_factory([None, None])
    await check_embedding_dim(factory)
