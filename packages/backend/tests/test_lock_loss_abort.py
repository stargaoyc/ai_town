"""H10 回归测试：Tick 看门狗检测到锁易主后必须中止本 Tick 的全部状态写入

验证目标（round-3 review H10）：
- watch_locks 原语行为（置位/不置位 lock_lost）见 tests/test_locks.py
- _execute_action 在 lock_lost 置位时不打开数据库会话、不写 Redis
- _execute_action 在 lock_lost 未置位时正常推进到 PG 事务边界（闸口不误拦）
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

from src.actions import ActionRegistry, DecisionResult
from src.actions.base import Action, ActionCategory
from src.core.character.tick import CharacterTickEngine
from src.db.session import db as db_singleton
from src.llm import LLMClient, PromptTemplates

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeRedis:
    def __init__(self) -> None:
        self.hset_calls: list[tuple[str, dict[str, Any]]] = []

    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.hset_calls.append((key, mapping or {}))


class SessionProbe:
    """替换 db.session：记录会话开启次数，一旦被打开即抛出哨兵异常阻断后续写入"""

    class Opened(Exception):
        pass

    def __init__(self) -> None:
        self.count = 0

    def session(self) -> Any:
        self.count += 1
        raise self.Opened


def _make_engine(redis: FakeRedis) -> CharacterTickEngine:
    registry = ActionRegistry()
    registry.register(Action(id="wait", name="等待", category=ActionCategory.SOCIAL))
    return CharacterTickEngine(
        redis=cast(Redis, redis),
        registry=registry,
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
    )


def _make_decision() -> DecisionResult:
    return DecisionResult(action="wait", reason="测试决策")


def _make_context() -> dict[str, Any]:
    return {
        "character": SimpleNamespace(id=_CHARACTER_ID, name="测试角色"),
        "state": {"location": "home", "stamina": 80},
        "world": {},
    }


async def test_execute_action_skips_all_writes_when_lock_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    probe = SessionProbe()
    monkeypatch.setattr(db_singleton, "session", probe.session)

    engine = _make_engine(redis)
    lock_lost = asyncio.Event()
    lock_lost.set()

    await engine._execute_action(_CHARACTER_ID, _make_decision(), _make_context(), lock_lost=lock_lost)

    assert probe.count == 0
    assert redis.hset_calls == []


async def test_execute_action_still_opens_transaction_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    probe = SessionProbe()
    monkeypatch.setattr(db_singleton, "session", probe.session)

    engine = _make_engine(redis)
    lock_lost = asyncio.Event()

    with pytest.raises(SessionProbe.Opened):
        await engine._execute_action(_CHARACTER_ID, _make_decision(), _make_context(), lock_lost=lock_lost)

    assert probe.count == 1
    assert redis.hset_calls == []
