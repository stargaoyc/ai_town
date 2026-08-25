"""P0-1 回归测试：工具 delta 不再直接写 Redis，由 _execute_action 统一持久化

修复目标（docs/design-improvement-and-fixes.md P0-1）：
- _apply_tool_deltas 只更新内存 state，不写 Redis
- inventory 等工具变更随 _execute_action 的 PG 事务落库

R5-L11 扩展：工具调用记忆同样暂存到 context，与 ActionRecord 同事务落库——
主事务回滚时记忆一并回滚，杜绝「记忆描述了从未持久化的效果」。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

import src.core.character.tick as tick_module
from src.actions import ActionRegistry, DecisionResult
from src.actions.base import Action, ActionCategory
from src.core.character.tick import CharacterTickEngine
from src.db.models import ActionRecord, CharacterStateHistory, MemoryEpisode
from src.db.session import db as db_singleton
from src.llm import LLMClient, PromptTemplates

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeRedis:
    """记录 hset 调用的假 Redis，用于断言工具 delta 不再直写"""

    def __init__(self) -> None:
        self.hset_calls: list[tuple[str, dict[str, Any]]] = []

    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.hset_calls.append((key, mapping or {}))


def _make_engine(redis: FakeRedis) -> CharacterTickEngine:
    registry = ActionRegistry()
    registry.register(Action(id="wait", name="等待", category=ActionCategory.SOCIAL))
    return CharacterTickEngine(
        redis=cast(Redis, redis),
        registry=registry,
        llm=cast(LLMClient, None),
        prompts=PromptTemplates(),
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


# ---------- R5-L11：工具记忆暂存 → 主事务落库 ----------


class SessionProbe:
    """替换 db.session：记录会话开启次数，一旦被打开即抛出哨兵异常阻断写入"""

    class Opened(Exception):
        pass

    def __init__(self) -> None:
        self.count = 0

    def session(self) -> Any:
        self.count += 1
        raise self.Opened


class FakeToolRegistry:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def call_tool_with_context(
        self, full_name: str, args: dict[str, Any] | None, context: dict[str, Any]
    ) -> dict[str, Any]:
        return {"success": True, "result": {"weather": "sunny"}, "error": None, "state_mutating": False}


async def test_execute_tool_stages_memory_instead_of_committing(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具成功后只暂存到 context，不再开独立会话提交记忆"""
    monkeypatch.setattr(tick_module, "ToolRegistry", FakeToolRegistry)
    probe = SessionProbe()
    monkeypatch.setattr(db_singleton, "session", probe.session)

    redis = FakeRedis()
    engine = _make_engine(redis)
    context: dict[str, Any] = {
        "character": SimpleNamespace(name="测试角色"),
        "state": {"location": "home", "mood": "calm"},
    }
    decision = DecisionResult(action="use_tool", reason="查天气", params={"tool_name": "world.info", "tool_args": {}})

    result = await engine._execute_tool(_CHARACTER_ID, decision, context)

    assert result is not None and result["success"] is True
    assert probe.count == 0
    staged = context["pending_tool_memories"]
    assert len(staged) == 1
    assert staged[0]["content"].startswith("[工具调用] world.info")
    assert staged[0]["location"] == "home"
    assert staged[0]["reason"] == "使用工具 world.info"


class FakeMemoryRepo:
    """MemoryRepository 测试替身：记录去重查询与 add 调用，镜像写入所属会话"""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.duplicate_checks: list[Any] = []
        self.added: list[MemoryEpisode] = []

    async def exists_recent_duplicate(self, character_id: UUID, normalized_content: str) -> bool:
        self.duplicate_checks.append((character_id, normalized_content))
        return False

    async def add(self, episode: MemoryEpisode) -> MemoryEpisode:
        self.added.append(episode)
        if hasattr(self.session, "add"):
            self.session.add(episode)
        return episode


class TxnRecordingDB:
    """模拟真实 db.session 契约：成功退出时提交一次，记录会话内全部 add"""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    @asynccontextmanager
    async def session(self) -> Any:
        added = self.added

        class _Session:
            def add(self, obj: Any) -> None:
                added.append(obj)

            async def commit(self) -> None:
                raise AssertionError("tick 主事务依赖 session CM 提交，不应显式 commit")

            async def rollback(self) -> None:
                pass

        yield _Session()
        self.commits += 1


async def test_apply_pending_artifacts_persists_tool_memory_in_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """暂存的工具记忆经 EpisodeService 落库：保留近邻去重与 use_tool 归属"""
    fake_repo = FakeMemoryRepo(cast(Any, None))
    monkeypatch.setattr(tick_module, "MemoryRepository", lambda session: fake_repo)
    redis = FakeRedis()
    engine = _make_engine(redis)
    context: dict[str, Any] = {
        "character": SimpleNamespace(name="测试角色"),
        "pending_tool_memories": [
            {
                "content": "[工具调用] world.info({}) → sunny",
                "location": "home",
                "reason": "使用工具 world.info",
                "mood": "calm",
            }
        ],
    }

    await engine._apply_pending_artifacts(cast(Any, object()), _CHARACTER_ID, context)

    assert len(fake_repo.added) == 1
    episode = fake_repo.added[0]
    assert episode.action_id == "use_tool"
    assert episode.content == "[工具调用] world.info({}) → sunny"
    assert episode.location == "home"
    assert fake_repo.duplicate_checks == [(_CHARACTER_ID, "[工具调用] world.info({}) → sunny")]


async def test_execute_action_commits_tool_memory_in_same_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构性锁定：ActionRecord / 状态历史 / 工具记忆进入同一会话——
    同一事务意味着主事务回滚时工具记忆一并回滚"""
    created_repos: list[FakeMemoryRepo] = []

    def repo_factory(session: Any) -> FakeMemoryRepo:
        repo = FakeMemoryRepo(session)
        created_repos.append(repo)
        return repo

    monkeypatch.setattr(tick_module, "MemoryRepository", repo_factory)

    class FakeActionRepo:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def add(self, record: ActionRecord) -> ActionRecord:
            self.session.add(record)
            return record

    class FakeCharRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def update_state(self, *args: Any, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(tick_module, "ActionRepository", FakeActionRepo)
    monkeypatch.setattr(tick_module, "CharacterRepository", FakeCharRepo)

    redis = FakeRedis()
    engine = _make_engine(redis)
    fake_db = TxnRecordingDB()
    monkeypatch.setattr(db_singleton, "session", fake_db.session)

    context: dict[str, Any] = {
        "character": SimpleNamespace(id=_CHARACTER_ID, name="测试角色"),
        "state": {"location": "home", "stamina": 80, "satiety": 60, "money": 100},
        "world": {},
        "pending_tool_memories": [
            {
                "content": "[工具调用] world.info({}) → sunny",
                "location": "home",
                "reason": "使用工具 world.info",
                "mood": "calm",
            }
        ],
    }
    decision = DecisionResult(action="wait", reason="休息一下")

    await engine._execute_action(_CHARACTER_ID, decision, context)

    kinds = {type(obj) for obj in fake_db.added}
    assert {ActionRecord, CharacterStateHistory, MemoryEpisode} <= kinds
    assert created_repos[0].added[0].action_id == "use_tool"
    assert fake_db.commits == 1
    assert len(redis.hset_calls) == 1
