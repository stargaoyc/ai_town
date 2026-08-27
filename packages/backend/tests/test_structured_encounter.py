"""结构化相遇闲聊单元测试（round-7 G1）

覆盖：
- 非 wait 决策不触发
- wait 且无同场景角色不触发
- 概率未命中不触发
- 冷却期内不触发
- 命中时决策替换为 chat_with（目标为在场 idle 角色）
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from pytest import MonkeyPatch

from src.actions import DecisionResult
from src.config import settings
from src.core.character.tick import CharacterTickEngine
from src.llm import LLMClient, PromptTemplates

_CID = UUID("01964000-0000-7000-8000-000000000001")


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value


def _engine(redis: FakeRedis | None = None) -> CharacterTickEngine:
    engine = CharacterTickEngine.__new__(CharacterTickEngine)
    engine.redis = cast(Any, redis if redis is not None else FakeRedis())
    engine.llm = cast(LLMClient, None)
    engine.prompts = cast(PromptTemplates, None)
    return engine


def _context(*nearby: dict[str, Any]) -> dict[str, Any]:
    return {"nearby_characters": list(nearby), "character": SimpleNamespace(name="小艾")}


def _wait_decision() -> DecisionResult:
    return DecisionResult(action="wait", reason="歇一会", params={}, duration=10)


def _idle(nid: str, name: str) -> dict[str, Any]:
    return {"id": nid, "name": name, "current_action": None}


def _busy(nid: str, name: str) -> dict[str, Any]:
    return {"id": nid, "name": name, "current_action": {"action_name": "eat_meal"}}


def _lost() -> Any:
    import asyncio

    return asyncio.Event()


class TestStructuredEncounter:
    async def test_non_wait_decision_unchanged(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", True)
        engine = _engine()
        decision = DecisionResult(action="move", reason="去咖啡店", params={}, duration=10)

        out = await engine._maybe_structured_encounter(_CID, decision, _context(_idle("t1", "小传")), lost=_lost())

        assert out.action == "move"

    async def test_no_nearby_unchanged(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", True)
        engine = _engine()

        out = await engine._maybe_structured_encounter(_CID, _wait_decision(), _context(), lost=_lost())

        assert out.action == "wait"

    async def test_only_busy_nearby_unchanged(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", True)
        engine = _engine()

        out = await engine._maybe_structured_encounter(
            _CID, _wait_decision(), _context(_busy("t1", "小传")), lost=_lost()
        )

        assert out.action == "wait"

    async def test_probability_miss_unchanged(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", True)
        monkeypatch.setattr(settings, "social_encounter_probability", 0.0)
        engine = _engine()

        out = await engine._maybe_structured_encounter(
            _CID, _wait_decision(), _context(_idle("t1", "小传")), lost=_lost()
        )

        assert out.action == "wait"

    async def test_probability_hit_switches_to_chat(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", True)
        monkeypatch.setattr(settings, "social_encounter_probability", 1.0)
        monkeypatch.setattr(settings, "social_encounter_cooldown_seconds", 600)
        redis = FakeRedis()
        engine = _engine(redis)

        out = await engine._maybe_structured_encounter(
            _CID,
            _wait_decision(),
            _context(_idle("t1", "小传"), _idle("t2", "小博")),
            lost=_lost(),
        )

        assert out.action == "chat_with"
        assert out.params["target_character_id"] in ("t1", "t2")
        assert "小传" in out.reason or "小博" in out.reason
        assert redis.data != {}, "命中后应写冷却键"

    async def test_cooldown_suppresses_repeat(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", True)
        monkeypatch.setattr(settings, "social_encounter_probability", 1.0)
        redis = FakeRedis()
        await redis.set(f"char:{_CID}:encounter:cooldown", "1", ex=600)
        engine = _engine(redis)

        out = await engine._maybe_structured_encounter(
            _CID, _wait_decision(), _context(_idle("t1", "小传")), lost=_lost()
        )

        assert out.action == "wait"

    async def test_disabled_feature_unchanged(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "social_encounter_enabled", False)
        engine = _engine()

        out = await engine._maybe_structured_encounter(
            _CID, _wait_decision(), _context(_idle("t1", "小传")), lost=_lost()
        )

        assert out.action == "wait"
