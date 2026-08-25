"""P0-1 回归测试：工具 delta 不再直接写 Redis，由 _execute_action 统一持久化

修复目标（docs/design-improvement-and-fixes.md P0-1）：
- _apply_tool_deltas 只更新内存 state，不写 Redis
- inventory 等工具变更随 _execute_action 的 PG 事务落库
"""

from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis

from src.actions import ActionRegistry
from src.core.character.tick import CharacterTickEngine
from src.llm import LLMClient, PromptTemplates

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeRedis:
    """记录 hset 调用的假 Redis，用于断言工具 delta 不再直写"""

    def __init__(self) -> None:
        self.hset_calls: list[tuple[str, dict[str, Any]]] = []

    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.hset_calls.append((key, mapping or {}))


def _make_engine(redis: FakeRedis) -> CharacterTickEngine:
    return CharacterTickEngine(
        redis=cast(Redis, redis),
        registry=ActionRegistry(),
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
    )


async def test_apply_tool_deltas_updates_memory_state_only() -> None:
    redis = FakeRedis()
    engine = _make_engine(redis)
    context = {"state": {"money": 100, "inventory": {}, "mood": "calm"}}

    await engine._apply_tool_deltas(
        _CHARACTER_ID,
        {"money_delta": -20, "inventory_delta": {"coffee": 2}, "mood_delta": "happy"},
        context,
    )

    # P0-1：工具 delta 不再直接写 Redis
    assert redis.hset_calls == []
    assert context["state"]["money"] == 80
    assert context["state"]["inventory"] == {"coffee": 2}
    assert context["state"]["mood"] == "happy"


async def test_apply_tool_deltas_inventory_removes_zero_qty() -> None:
    redis = FakeRedis()
    engine = _make_engine(redis)
    context = {"state": {"inventory": {"coffee": 2}}}

    await engine._apply_tool_deltas(
        _CHARACTER_ID,
        {"inventory_delta": {"coffee": -2, "book": 1}},
        context,
    )

    assert context["state"]["inventory"] == {"book": 1}


async def test_apply_tool_deltas_money_never_negative() -> None:
    redis = FakeRedis()
    engine = _make_engine(redis)
    context = {"state": {"money": 10}}

    await engine._apply_tool_deltas(
        _CHARACTER_ID,
        {"money_delta": -50},
        context,
    )

    assert context["state"]["money"] == 0


async def test_apply_tool_deltas_relation_deferred_to_main_txn() -> None:
    """R4-M11：关系增量只暂存 context，不再即时开 PG 连接写 relations 表"""
    redis = FakeRedis()
    engine = _make_engine(redis)
    context: dict[str, Any] = {"state": {}, "relations": {"01964000-0000-7000-8000-000000000002": 30}}

    await engine._apply_tool_deltas(
        _CHARACTER_ID,
        {"relation_strength_delta": 5, "target_id": "01964000-0000-7000-8000-000000000002"},
        context,
    )

    assert context["pending_relation_deltas"] == [{"target_id": "01964000-0000-7000-8000-000000000002", "delta": 5}]
    # 未直接改写关系映射（由主事务应用后统一更新）
    assert context["relations"]["01964000-0000-7000-8000-000000000002"] == 30
