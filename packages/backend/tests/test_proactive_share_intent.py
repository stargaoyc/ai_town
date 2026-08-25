"""主动分享意图回归测试（R5-H2）

修复前缺陷：决策 schema 未声明 proactiveShareIntent，structured_output 按 schema
属性生成 pydantic 模型时静默丢弃该键 → DecisionResult.proactive_share_intent 恒为
False，README 宣称的「主动分享」特性端到端死代码。本文件锁定：
- 决策 schema 含 proactiveShareIntent，输出示例自动渲染该字段（单一真相源）
- _decide 解析 LLM 返回的 proactiveShareIntent
- _execute_tick 仅在 intent=True 时触发分享处理器
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

import src.core.character.tick as tick_module
from src.actions import ActionRegistry, DecisionResult
from src.actions.base import Action, ActionCategory
from src.core.character.tick import CharacterTickEngine, _schema_example
from src.llm import LLMClient, PromptTemplates
from src.tools import registry as registry_module

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeLLM:
    """记录 structured_output 调用并按序返回预置结果"""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def structured_output(self, prompt: str, schema: dict[str, Any], model: str = "strong") -> dict[str, Any]:
        self.calls.append((prompt, schema))
        return self.results.pop(0)


class FakeRedis:
    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        pass


def _make_engine(llm: FakeLLM | None = None) -> CharacterTickEngine:
    registry = ActionRegistry()
    registry.register(Action(id="wait", name="等待", category=ActionCategory.SOCIAL))
    return CharacterTickEngine(
        redis=cast(Redis, FakeRedis()),
        registry=registry,
        llm=cast(LLMClient, llm),
        prompts=PromptTemplates(),
    )


def _wait_action() -> Action:
    return Action(id="wait", name="等待", category=ActionCategory.SOCIAL)


def _decide_context() -> dict[str, Any]:
    return {
        "character": SimpleNamespace(name="小艾", traits={"personality": ["温柔"]}, backstory="咖啡店老板"),
        "state": {"location": "cafe", "stamina": 80, "satiety": 60, "mood": "calm"},
        "world": {"world_time": "2026-08-26T10:00:00+00:00", "weather": "sunny"},
        "memories": [],
        "plans": [],
    }


def _disable_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """零工具环境：绕过 Redis 读取，同时锁定 M3 的工具段跳过行为"""

    async def none_enabled() -> set[str]:
        return set()

    monkeypatch.setattr(registry_module, "get_enabled_tools", none_enabled)


# ---------- schema 与解析 ----------


def test_schema_example_renders_boolean_field() -> None:
    example = _schema_example({"type": "object", "properties": {"proactiveShareIntent": {"type": "boolean"}}})
    assert '"proactiveShareIntent": false' in example


async def test_decide_parses_proactive_share_intent_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_all_tools(monkeypatch)
    fake_llm = FakeLLM([{"action": "wait", "reason": "想静静", "proactiveShareIntent": True}])
    engine = _make_engine(fake_llm)

    decision = await engine._decide(_CHARACTER_ID, _decide_context(), [_wait_action()])

    assert decision.proactive_share_intent is True
    _, schema = fake_llm.calls[0]
    assert schema["properties"]["proactiveShareIntent"]["type"] == "boolean"


async def test_decide_defaults_proactive_share_intent_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_all_tools(monkeypatch)
    engine = _make_engine(FakeLLM([{"action": "wait", "reason": "想静静"}]))

    decision = await engine._decide(_CHARACTER_ID, _decide_context(), [_wait_action()])

    assert decision.proactive_share_intent is False


# ---------- Tick 门控 ----------


def _stub_tick_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    engine: CharacterTickEngine,
    decision: DecisionResult,
) -> None:
    """替换 _execute_tick 的重依赖环节：只留分享门控分支走真实代码"""

    async def fake_perceive(cid: UUID) -> dict[str, Any]:
        return {
            "character": SimpleNamespace(name="小艾"),
            "state": {"location": "cafe", "stamina": 80},
            "world": {},
            "memories": [],
            "plans": [],
            "nearby_characters": [],
        }

    async def fake_decide(
        cid: UUID, ctx: dict[str, Any], cands: list[Action], obs: list[dict[str, Any]]
    ) -> DecisionResult:
        return decision

    async def passthrough_react(
        cid: UUID, ctx: dict[str, Any], cands: list[Action], d: DecisionResult, **kwargs: Any
    ) -> DecisionResult:
        return d

    async def noop(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(tick_module, "start_tick_trace", lambda cid: "trace")
    monkeypatch.setattr(tick_module, "end_tick_trace", lambda **kwargs: None)
    monkeypatch.setattr(tick_module, "trace_character_tick", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_perceive", fake_perceive)
    monkeypatch.setattr(engine, "_decide", fake_decide)
    monkeypatch.setattr(engine, "_run_react_loop", passthrough_react)
    monkeypatch.setattr(engine, "_execute_action", noop)
    monkeypatch.setattr(engine, "_memorize", noop)
    monkeypatch.setattr(engine, "_propagate_gossip", noop)


async def test_execute_tick_fires_share_handler_when_intent_true(monkeypatch: pytest.MonkeyPatch) -> None:
    shared: list[UUID] = []

    async def handler(cid: UUID) -> None:
        shared.append(cid)

    monkeypatch.setattr(tick_module, "get_proactive_share_handler", lambda: handler)
    engine = _make_engine()
    _stub_tick_pipeline(
        monkeypatch,
        engine,
        DecisionResult(action="wait", reason="刚遇到有趣的事", proactive_share_intent=True),
    )

    await engine._execute_tick(_CHARACTER_ID, lock_lost=asyncio.Event())

    assert shared == [_CHARACTER_ID]


async def test_execute_tick_skips_share_handler_when_intent_false(monkeypatch: pytest.MonkeyPatch) -> None:
    shared: list[UUID] = []

    async def handler(cid: UUID) -> None:
        shared.append(cid)

    monkeypatch.setattr(tick_module, "get_proactive_share_handler", lambda: handler)
    engine = _make_engine()
    _stub_tick_pipeline(
        monkeypatch,
        engine,
        DecisionResult(action="wait", reason="平平无奇的一天"),
    )

    await engine._execute_tick(_CHARACTER_ID, lock_lost=asyncio.Event())

    assert shared == []
