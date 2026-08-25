"""H10 回归测试：Tick 看门狗检测到锁易主后必须中止本 Tick 的全部状态写入

验证目标（round-3 review H10）：
- watch_locks 原语行为（置位/不置位 lock_lost）见 tests/test_locks.py
- _execute_action 在 lock_lost 置位时不打开数据库会话、不写 Redis
- _execute_action 在 lock_lost 未置位时正常推进到 PG 事务边界（闸口不误拦）
- R5-M6：chat_with 关系/记忆写入前自查失锁；工具记忆暂存失锁即跳过
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

import src.core.character.tick as tick_module
from src.actions import ActionRegistry, DecisionResult
from src.actions.base import Action, ActionCategory
from src.config import settings
from src.core.character.tick import CharacterTickEngine
from src.db.models import MemoryEpisode
from src.db.session import db as db_singleton
from src.llm import LLMClient, PromptTemplates

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")
_TARGET_ID = UUID("01964000-0000-7000-8000-000000000002")


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


# ---------- R5-M6：chat_with 写入闸口 ----------


class RecordingDB:
    """替换 db.session：记录会话内 add 的实体与 commit 次数，不触真实 PG"""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    @asynccontextmanager
    async def session(self) -> Any:
        db_log = self

        class _Session:
            def add(self, obj: Any) -> None:
                db_log.added.append(obj)

            async def commit(self) -> None:
                db_log.commits += 1

            async def rollback(self) -> None:
                pass

        yield _Session()


class FakeTargetRepo:
    def __init__(self, session: Any) -> None:
        pass

    async def get_character_with_state(self, character_id: UUID) -> tuple[Any, None]:
        return SimpleNamespace(id=character_id, name="目标角色"), None


class FakeRelationGraph:
    instances: list["FakeRelationGraph"] = []

    def __init__(self, session: Any, redis: Any) -> None:
        self.update_calls: list[dict[str, Any]] = []
        type(self).instances.append(self)

    async def get_relation(self, char_a: UUID, char_b: UUID) -> None:
        return None

    async def update_on_interaction(self, **kwargs: Any) -> None:
        self.update_calls.append(kwargs)


def _chat_context() -> dict[str, Any]:
    return {
        "character": SimpleNamespace(id=_CHARACTER_ID, name="发起者"),
        "state": {"location": "cafe", "mood": "calm"},
        "world": {"world_time": "2026-08-26T10:00:00+00:00"},
    }


def _chat_decision() -> DecisionResult:
    return DecisionResult(
        action="chat_with",
        reason="聊聊近况",
        params={"target_character_id": str(_TARGET_ID)},
    )


def _stub_chat_pipeline(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> tuple[CharacterTickEngine, RecordingDB]:
    monkeypatch.setattr(tick_module, "CharacterRepository", FakeTargetRepo)
    monkeypatch.setattr(tick_module, "RelationGraph", FakeRelationGraph)
    monkeypatch.setattr(settings, "chat_quality_enabled", False)
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)

    async def fake_turn(**kwargs: Any) -> str:
        return "今天天气不错。"

    engine = CharacterTickEngine(
        redis=cast(Redis, redis),
        registry=ActionRegistry(),
        llm=cast(LLMClient, None),
        prompts=PromptTemplates(),
    )
    monkeypatch.setattr(engine, "_generate_chat_turn", fake_turn)

    fake_db = RecordingDB()
    monkeypatch.setattr(db_singleton, "session", fake_db.session)
    return engine, fake_db


async def test_do_chat_with_skips_relation_and_memory_writes_when_lock_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对话生成横跨锁 TTL 后易主：关系更新与双方记忆写入全部跳过"""
    monkeypatch.setattr(FakeRelationGraph, "instances", [])
    engine, fake_db = _stub_chat_pipeline(monkeypatch, FakeRedis())
    lock_lost = asyncio.Event()
    lock_lost.set()

    dialogue = await engine._do_chat_with(
        _CHARACTER_ID,
        _TARGET_ID,
        str(_TARGET_ID),
        SimpleNamespace(name="发起者"),
        _chat_decision(),
        _chat_context(),
        lock_lost=lock_lost,
    )

    assert dialogue is not None
    assert fake_db.added == []
    assert fake_db.commits == 0
    assert all(g.update_calls == [] for g in FakeRelationGraph.instances)


async def test_do_chat_with_persists_writes_when_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """对照组：锁未失守时既有写入行为不变（双记忆 + 关系更新）"""
    monkeypatch.setattr(FakeRelationGraph, "instances", [])
    engine, fake_db = _stub_chat_pipeline(monkeypatch, FakeRedis())

    dialogue = await engine._do_chat_with(
        _CHARACTER_ID,
        _TARGET_ID,
        str(_TARGET_ID),
        SimpleNamespace(name="发起者"),
        _chat_decision(),
        _chat_context(),
        lock_lost=asyncio.Event(),
    )

    assert dialogue is not None
    memories = [m for m in fake_db.added if isinstance(m, MemoryEpisode)]
    assert {str(m.character_id) for m in memories} == {str(_CHARACTER_ID), str(_TARGET_ID)}
    assert fake_db.commits >= 1
    updates = [u for g in FakeRelationGraph.instances for u in g.update_calls]
    assert len(updates) == 1
    # 关系快照缺失时 relationship_desc 为「陌生人」，与 legacy 的 "stranger"
    # 英文字面量不匹配，走默认增量 +5（既有行为）
    assert updates[0]["strength_delta"] == 5


# ---------- R5-M6：工具记忆暂存闸口 ----------


class FakeToolRegistry:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def call_tool_with_context(
        self, full_name: str, args: dict[str, Any] | None, context: dict[str, Any]
    ) -> dict[str, Any]:
        return {"success": True, "result": {"ok": True}, "error": None, "state_mutating": False}


async def test_execute_tool_skips_memory_staging_when_lock_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    """失锁后工具调用成功也不暂存记忆——不留下注定无法持久化的影子数据"""
    monkeypatch.setattr(tick_module, "ToolRegistry", FakeToolRegistry)
    probe = SessionProbe()
    monkeypatch.setattr(db_singleton, "session", probe.session)

    engine = _make_engine(FakeRedis())
    context: dict[str, Any] = {
        "character": SimpleNamespace(name="测试角色"),
        "state": {"location": "home"},
    }
    decision = DecisionResult(action="use_tool", reason="x", params={"tool_name": "a.b", "tool_args": {}})

    result = await engine._execute_tool(_CHARACTER_ID, decision, context, lock_lost=_set_event())

    assert result is not None and result["success"] is True
    assert "pending_tool_memories" not in context
    assert probe.count == 0


def _set_event() -> asyncio.Event:
    event = asyncio.Event()
    event.set()
    return event
