"""chat_with 多轮对话回归测试

验证目标：
- 轮数配置生效：每轮双方各一次 LLM 调用（call count == rounds * 2）
- 轮数硬上限 3，与配置无关
- 关系增量由 LLM 结构化评估并钳制到 [-10, 10]
- 评估关闭或失败时回退固定值（陌生人 +2 / 其他 +5）并记录 warning
- 每轮 prompt 只携带对话记录末尾窗口
- 单句解析失败 → 整场对话失败（返回 None，不写关系）
- 双方记忆内容压缩到上限以内
- R6-M7：双方记忆经 EpisodeService 落库，双写/互指/source_type 契约不变
"""

import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis
from structlog.testing import capture_logs

import src.core.character.social as social_module
from src.actions import ActionRegistry, DecisionResult
from src.config import settings
from src.core.character.social import (
    _CHAT_MEMORY_MAX_CHARS,
    _CHAT_QUALITY_DELTA_LIMIT,
    _CHAT_TRANSCRIPT_MAX_CHARS,
    _clip_tail,
    _parse_chat_line,
)
from src.core.character.tick import CharacterTickEngine
from src.db.models import MemoryEpisode
from src.db.session import db as db_singleton
from src.llm import LLMClient, PromptTemplates
from src.modules.relation.graph import RelationSnapshot

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")
_TARGET_ID = UUID("01964000-0000-7000-8000-000000000002")
_LOCATION = "gallery"
_INITIATOR_NAME = "阿澄"
_TARGET_NAME = "小铃"


class FakeRedis:
    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        pass

    async def hgetall(self, key: str) -> dict[str, str]:
        return {}


class FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commit_count = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: Any) -> FakeResult:
        # exists_recent_duplicate 探测：恒返回「无近邻重复」
        return FakeResult(None)


class FakeSessionCtx:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


class FakeCharRepo:
    """只提供 chat 链路用到的查询：目标角色档案"""

    def __init__(self, session: Any) -> None:
        pass

    async def get_character_with_state(self, character_id: UUID) -> tuple[SimpleNamespace, dict[str, Any]]:
        target = SimpleNamespace(id=character_id, name=_TARGET_NAME, traits={"personality": ["开朗", "健谈"]})
        return (target, {})


class FakeRelationGraph:
    """记录关系读取与更新调用；relationship_type/strength 由测试注入"""

    def __init__(self, relationship_type: str = "friend", strength: int = 45) -> None:
        self.relationship_type = relationship_type
        self.strength = strength
        self.updates: list[int] = []

    def __call__(self, session: Any, redis: Any) -> "FakeRelationGraph":
        return self

    async def get_relation(self, character_id: UUID, target_id: UUID) -> RelationSnapshot:
        return RelationSnapshot(
            character_id=character_id,
            target_id=target_id,
            relationship_type=self.relationship_type,
            strength=self.strength,
            last_interaction_at=None,
            notes=None,
        )

    async def update_on_interaction(
        self,
        char_a: UUID,
        char_b: UUID,
        strength_delta: int = 0,
        notes: str | None = None,
    ) -> tuple[RelationSnapshot, RelationSnapshot]:
        self.updates.append(strength_delta)
        snapshot = RelationSnapshot(
            character_id=char_a,
            target_id=char_b,
            relationship_type="friend",
            strength=50,
            last_interaction_at=None,
            notes=None,
        )
        return (snapshot, snapshot)


class FakeLLM:
    """chat() 按序返回预制台词 JSON；structured_output 返回质量评估（或抛异常）"""

    def __init__(
        self,
        lines: list[str],
        quality: dict[str, Any] | Exception | None = None,
    ) -> None:
        self._lines = list(lines)
        self._quality = quality
        self.chat_calls = 0
        self.prompts_seen: list[str] = []
        self.structured_schemas: list[dict[str, Any]] = []

    async def chat(self, prompt: str, model: str = "chat", system_prompt: str | None = None) -> str:
        self.chat_calls += 1
        self.prompts_seen.append(prompt)
        return self._lines.pop(0)

    async def structured_output(self, prompt: str, schema: dict[str, Any], model: str = "chat") -> dict[str, Any]:
        self.structured_schemas.append(schema)
        if isinstance(self._quality, Exception):
            raise self._quality
        assert self._quality is not None
        return self._quality


def _json_line(text: str) -> str:
    return json.dumps({"line": text}, ensure_ascii=False)


def _default_lines(count: int) -> list[str]:
    return [_json_line(f"台词{index}") for index in range(count)]


def _make_engine(
    monkeypatch: pytest.MonkeyPatch,
    lines: list[str],
    quality: dict[str, Any] | Exception | None = None,
    relationship_type: str = "friend",
) -> tuple[CharacterTickEngine, FakeLLM, FakeRelationGraph, FakeSession]:
    llm = FakeLLM(lines, quality)
    graph = FakeRelationGraph(relationship_type=relationship_type)
    fake_session = FakeSession()
    # 对话记忆评分关闭：FakeLLM.chat 按序弹出台词，评分调用会污染调用数断言
    monkeypatch.setattr(settings, "memory_llm_scoring_enabled", False)
    monkeypatch.setattr(db_singleton, "session", lambda: FakeSessionCtx(fake_session))
    # _do_chat_with 已迁入 social.py，CharacterRepository/RelationGraph 在其模块命名空间解析
    monkeypatch.setattr(social_module, "CharacterRepository", FakeCharRepo)
    monkeypatch.setattr(social_module, "RelationGraph", graph)
    engine = CharacterTickEngine(
        redis=cast(Redis, FakeRedis()),
        registry=ActionRegistry(),
        llm=cast(LLMClient, llm),
        prompts=PromptTemplates(),
    )
    return engine, llm, graph, fake_session


def _make_decision() -> DecisionResult:
    return DecisionResult(
        action="chat_with",
        reason="想聊聊最近的艺术展",
        params={"target_character_id": str(_TARGET_ID)},
    )


def _make_context() -> dict[str, Any]:
    character = SimpleNamespace(id=_CHARACTER_ID, name=_INITIATOR_NAME, traits={"personality": ["内向", "细腻"]})
    return {
        "character": character,
        "state": {"location": _LOCATION, "stamina": 80, "satiety": 60, "mood": "calm"},
        "world": {"world_time": "2026-08-25T10:00:00+00:00", "weather": "sunny"},
    }


async def _run_chat(engine: CharacterTickEngine) -> str | None:
    context = _make_context()
    return await engine._do_chat_with(
        _CHARACTER_ID,
        _TARGET_ID,
        str(_TARGET_ID),
        context["character"],
        _make_decision(),
        context,
    )


def test_clip_tail_keeps_only_trailing_window() -> None:
    text = "HEADMARK" + "乙" * (_CHAT_TRANSCRIPT_MAX_CHARS + 50)
    clipped = _clip_tail(text, _CHAT_TRANSCRIPT_MAX_CHARS)
    assert len(clipped) == _CHAT_TRANSCRIPT_MAX_CHARS
    assert not clipped.startswith("HEADMARK")


def test_parse_chat_line_accepts_fenced_and_plain_json() -> None:
    assert _parse_chat_line('{"line": "你好"}') == "你好"
    assert _parse_chat_line('```json\n{"line": " fences 也行 "}\n```') == "fences 也行"
    assert _parse_chat_line("不是 JSON") is None
    assert _parse_chat_line('{"other": "字段不对"}') is None
    assert _parse_chat_line('{"line": "   "}') is None


async def test_rounds_honored_generates_two_calls_per_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 2)
    engine, llm, graph, fake_session = _make_engine(monkeypatch, _default_lines(4), {"delta": 3, "reason": "愉快"})

    dialogue = await _run_chat(engine)

    assert llm.chat_calls == 4
    assert dialogue is not None
    for index in range(4):
        assert f"台词{index}" in dialogue
    # 发起方先开口，随后对方回应
    lines = (dialogue or "").split("\n")
    assert lines[0].startswith(f"{_INITIATOR_NAME}: ")
    assert lines[1].startswith(f"{_TARGET_NAME}: ")
    assert graph.updates == [3]
    assert len(fake_session.added) == 2


async def test_hard_cap_three_rounds_regardless_of_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 99)
    engine, llm, graph, _ = _make_engine(monkeypatch, _default_lines(6), {"delta": 1, "reason": "普通寒暄"})

    dialogue = await _run_chat(engine)

    assert llm.chat_calls == 6
    assert dialogue is not None and len(dialogue.split("\n")) == 6
    assert graph.updates == [1]


@pytest.mark.parametrize(
    ("raw_delta", "clamped"),
    [
        (_CHAT_QUALITY_DELTA_LIMIT + 32, _CHAT_QUALITY_DELTA_LIMIT),
        (-_CHAT_QUALITY_DELTA_LIMIT - 5, -_CHAT_QUALITY_DELTA_LIMIT),
    ],
)
async def test_quality_delta_clamped_to_limit(monkeypatch: pytest.MonkeyPatch, raw_delta: int, clamped: int) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)
    monkeypatch.setattr(settings, "chat_quality_enabled", True)
    engine, llm, graph, _ = _make_engine(monkeypatch, _default_lines(2), {"delta": raw_delta, "reason": "过热"})

    dialogue = await _run_chat(engine)

    assert dialogue is not None
    assert len(llm.structured_schemas) == 1
    assert graph.updates == [clamped]


async def test_quality_disabled_uses_legacy_default_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)
    monkeypatch.setattr(settings, "chat_quality_enabled", False)
    engine, llm, graph, _ = _make_engine(monkeypatch, _default_lines(2), {"delta": 9, "reason": "不应被调用"})

    dialogue = await _run_chat(engine)

    assert dialogue is not None
    assert llm.structured_schemas == []
    assert graph.updates == [5]


async def test_quality_disabled_stranger_gets_legacy_icebreak_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)
    monkeypatch.setattr(settings, "chat_quality_enabled", False)
    engine, llm, graph, _ = _make_engine(
        monkeypatch, _default_lines(2), {"delta": 9, "reason": "不应被调用"}, relationship_type="stranger"
    )

    dialogue = await _run_chat(engine)

    assert dialogue is not None
    assert llm.structured_schemas == []
    assert graph.updates == [2]


async def test_quality_failure_falls_back_to_legacy_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)
    monkeypatch.setattr(settings, "chat_quality_enabled", True)
    engine, llm, graph, _ = _make_engine(monkeypatch, _default_lines(2), RuntimeError("llm unavailable"))

    with capture_logs() as logs:
        dialogue = await _run_chat(engine)

    assert dialogue is not None
    assert graph.updates == [5]
    fallback_events = [e for e in logs if e.get("event") == "chat_quality_fallback_legacy_delta"]
    assert len(fallback_events) == 1


async def test_unparseable_turn_aborts_whole_dialogue_without_relation_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "chat_quality_enabled", True)
    lines = ["这不是 JSON", _json_line("回应")]
    engine, llm, graph, fake_session = _make_engine(monkeypatch, lines, {"delta": 1, "reason": "ok"})

    with capture_logs() as logs:
        dialogue = await _run_chat(engine)

    assert dialogue is None
    assert llm.chat_calls == 1
    assert graph.updates == []
    abort_events = [e for e in logs if e.get("event") == "chat_dialogue_generation_failed"]
    assert len(abort_events) == 1
    assert fake_session.added == []


async def test_transcript_window_applied_in_turn_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 2)
    long_body = "HEADMARK" + "丙" * (_CHAT_TRANSCRIPT_MAX_CHARS + 100)
    lines = [_json_line(long_body), _json_line("收到"), _json_line("继续"), _json_line("好的")]
    engine, llm, _, _ = _make_engine(monkeypatch, lines, {"delta": 1, "reason": "ok"})

    await _run_chat(engine)

    second_prompt = llm.prompts_seen[1]
    assert "HEADMARK" not in second_prompt
    assert "丙" * 100 in second_prompt


async def test_memory_content_condensed_and_first_person(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)
    long_body = "丁" * 900
    lines = [_json_line(long_body), _json_line("好的")]
    engine, _, _, fake_session = _make_engine(monkeypatch, lines, {"delta": 2, "reason": "ok"})

    dialogue = await _run_chat(engine)

    assert dialogue is not None
    episodes = [obj for obj in fake_session.added if isinstance(obj, MemoryEpisode)]
    assert len(episodes) == 2
    by_char = {ep.character_id: ep for ep in episodes}
    assert set(by_char) == {_CHARACTER_ID, _TARGET_ID}
    expected_body = f"在{_LOCATION}和{_INITIATOR_NAME}聊天。{dialogue[:_CHAT_MEMORY_MAX_CHARS]}"
    assert by_char[_TARGET_ID].content == expected_body
    assert len(by_char[_TARGET_ID].content) <= _CHAT_MEMORY_MAX_CHARS + 20
    assert by_char[_CHARACTER_ID].source_type == "conversation"


async def test_memory_via_episode_service_keeps_dual_write_and_linkage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-M7 回归：双方记忆经 EpisodeService 写入后，双写、互指与 source_type 契约不变"""
    monkeypatch.setattr(settings, "chat_with_max_rounds", 1)
    engine, llm, _, fake_session = _make_engine(monkeypatch, _default_lines(2), {"delta": 2, "reason": "ok"})

    with capture_logs() as logs:
        dialogue = await _run_chat(engine)

    assert dialogue is not None
    assert not any(e.get("event") == "chat_memory_persist_failed_continue" for e in logs)
    episodes = [obj for obj in fake_session.added if isinstance(obj, MemoryEpisode)]
    by_char = {ep.character_id: ep for ep in episodes}
    assert by_char[_CHARACTER_ID].related_characters == [_TARGET_ID]
    assert by_char[_TARGET_ID].related_characters == [_CHARACTER_ID]
    for ep in episodes:
        assert ep.source_type == "conversation"
        assert ep.importance == 6
        assert ep.location == _LOCATION
    assert fake_session.commit_count >= 1
