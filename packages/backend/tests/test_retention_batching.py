"""retention 分批删除单元测试 - _delete_in_batches 终止性与计数（R5-L4）"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Delete

from src.db.models import MemoryEpisode, Reflection
from src.scheduler.loops import _delete_in_batches, _pk_batched_delete


class FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeSession:
    def __init__(self, rowcounts: list[int]) -> None:
        self._rowcounts = iter(rowcounts)
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> FakeResult:
        self.executed.append(stmt)
        return FakeResult(next(self._rowcounts))


def _fake_session(rowcounts: list[int]) -> Any:
    """测试替身按 AGENTS.md §5.2 以 cast 注入生产签名"""
    return cast("Any", FakeSession(rowcounts))


async def test_stops_on_partial_batch() -> None:
    """Given 剩余行数少于批大小 When 执行一批 Then 一轮即终止且计数正确"""
    session = _fake_session([2])
    total = await _delete_in_batches(session, lambda: cast("Delete", object()), batch_size=5)

    assert total == 2
    assert len(session.executed) == 1


async def test_loops_through_full_batches_then_zero() -> None:
    """Given 每批恰好删满 When 连续两轮删满后空轮 Then 三轮终止、总计 4 行"""
    session = _fake_session([2, 2, 0])
    total = await _delete_in_batches(session, lambda: cast("Delete", object()), batch_size=2)

    assert total == 4
    assert len(session.executed) == 3


async def test_full_batch_requires_extra_round_to_observe_zero() -> None:
    """Given 首批恰好等于批大小 When 无法区分是否还有剩余 Then 再执行一轮读到 0 才停"""
    session = _fake_session([10, 0])
    total = await _delete_in_batches(session, lambda: cast("Delete", object()), batch_size=10)

    assert total == 10
    assert len(session.executed) == 2


def test_pk_batched_delete_limits_single_column_pk() -> None:
    """单列主键表：DELETE WHERE id IN (SELECT id ... LIMIT n)，LIMIT 进入子查询"""
    stmt = _pk_batched_delete(Reflection, Reflection.tier == 1, batch_size=7)

    sql = str(stmt.compile())
    assert "LIMIT" in sql
    assert "reflections.id IN" in sql


def test_pk_batched_delete_handles_composite_pk() -> None:
    """memory_episodes 复合主键 (id, character_id)：row-constructor IN 子查询"""
    stmt = _pk_batched_delete(
        MemoryEpisode,
        MemoryEpisode.importance <= 3,
        MemoryEpisode.source_type != "archive",
        batch_size=100,
    )

    sql = str(stmt.compile())
    assert "(memory_episodes.id, memory_episodes.character_id) IN (SELECT" in sql
