"""chat 认知注入回归测试 - 反思/日报按 chat_inject_cognition 开关注入对话上下文

验证目标：
- 开关关闭时不触发任何 DB 读取，「暂无」占位保证模板键恒存在
- 开启时 top-3 反思与最新日报进入上下文，超长条目按 token 预算截断
- 仓库加载失败仅降级为「暂无」并记录 warning，不阻断回复主流程
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

import src.db.session as db_session_module
from src.config import settings
from src.db.models import Character, CharacterState, Conversation
from src.llm import LLMClient, PromptTemplates
from src.messaging.service import MessageService

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeSession:
    async def commit(self) -> None:
        pass


class FakeDBSession:
    async def rollback(self) -> None:
        pass


class FakeDB:
    """替换 src.db.session.db 单例：仅计数会话开启，不触真实 PG"""

    def __init__(self) -> None:
        self.opened = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        self.opened += 1
        yield cast(AsyncSession, FakeDBSession())


class _FakePersonMemoryService:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def get_relevant_context(self, character_id: UUID, user_id: str) -> str:
        return "（测试桩记忆）"


def _make_reflection_repo(calls: list[int], result: list[Any] | Exception) -> type[Any]:
    class _FakeReflectionRepository:
        def __init__(self, session: Any) -> None:
            pass

        async def get_by_character(self, character_id: UUID, limit: int = 10) -> list[Any]:
            calls.append(limit)
            if isinstance(result, Exception):
                raise result
            return result

    return _FakeReflectionRepository


def _make_diary_repo(calls: list[str], result: Any) -> type[Any]:
    class _FakeDiaryRepository:
        def __init__(self, session: Any) -> None:
            pass

        async def get_latest(self, character_id: UUID, period: str = "day") -> Any:
            calls.append(period)
            if isinstance(result, Exception):
                raise result
            return result

    return _FakeDiaryRepository


def _patch_repos(
    monkeypatch: pytest.MonkeyPatch,
    fake_db: FakeDB,
    reflection_result: list[Any] | Exception,
    diary_result: Any,
) -> tuple[list[int], list[str]]:
    ref_calls: list[int] = []
    diary_calls: list[str] = []
    monkeypatch.setattr(db_session_module, "db", fake_db)
    monkeypatch.setattr(
        "src.db.repositories.ReflectionRepository",
        _make_reflection_repo(ref_calls, reflection_result),
    )
    monkeypatch.setattr("src.db.repositories.DiaryRepository", _make_diary_repo(diary_calls, diary_result))
    return ref_calls, diary_calls


def _make_service(monkeypatch: pytest.MonkeyPatch) -> PromptTemplates:
    monkeypatch.setattr("src.memory.person_memory_service.PersonMemoryService", _FakePersonMemoryService)
    return PromptTemplates()


def _make_message_service(prompts: PromptTemplates) -> MessageService:
    return MessageService(
        session=cast(Any, FakeSession()),
        llm=cast(LLMClient, None),
        prompts=prompts,
        redis=None,
    )


def _make_context_inputs() -> tuple[Conversation, Character, CharacterState]:
    conversation = cast(Conversation, SimpleNamespace(id=uuid4(), user_id="user_1", context=None))
    character = cast(
        Character,
        SimpleNamespace(
            id=_CHARACTER_ID,
            name="小艾",
            traits={"personality": ["温柔"]},
            backstory="咖啡店老板",
        ),
    )
    state = cast(CharacterState, SimpleNamespace(location="cafe", stamina=80, mood="calm"))
    return conversation, character, state


async def test_toggle_off_uses_placeholder_without_db_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_inject_cognition", False)
    prompts = _make_service(monkeypatch)
    svc = _make_message_service(prompts)
    fake_db = FakeDB()
    ref_calls, diary_calls = _patch_repos(monkeypatch, fake_db, [], SimpleNamespace(content="x"))

    conversation, character, state = _make_context_inputs()
    context = await svc._build_context(conversation=conversation, character=character, state=state, history=[])

    assert ref_calls == [] and diary_calls == [] and fake_db.opened == 0
    assert context["reflections"] == "暂无"
    assert context["diary"] == "暂无"

    system_prompt = prompts.render_system("chat", **context)
    assert system_prompt.count("暂无") >= 2


async def test_toggle_on_injects_truncated_reflections_and_diary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_inject_cognition", True)
    prompts = _make_service(monkeypatch)
    svc = _make_message_service(prompts)
    fake_db = FakeDB()
    refs = [
        SimpleNamespace(content="反思一"),
        SimpleNamespace(content="认" * 400),
        SimpleNamespace(content="反思三"),
    ]
    ref_calls, diary_calls = _patch_repos(monkeypatch, fake_db, refs, SimpleNamespace(content="日" * 400))

    conversation, character, state = _make_context_inputs()
    context = await svc._build_context(conversation=conversation, character=character, state=state, history=[])

    expected_reflections = "- 反思一\n- " + "认" * 300 + "\n- 反思三"
    assert context["reflections"] == expected_reflections
    assert ("认" * 301) not in str(context["reflections"])
    assert context["diary"] == "日" * 300
    assert ref_calls == [3]
    assert diary_calls == ["day"]
    assert fake_db.opened == 1

    system_prompt = prompts.render_system("chat", **context)
    assert "- 反思一" in system_prompt and ("日" * 300) in system_prompt


async def test_reflection_failure_degrades_to_placeholder_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "chat_inject_cognition", True)
    prompts = _make_service(monkeypatch)
    svc = _make_message_service(prompts)
    fake_db = FakeDB()
    ref_calls, diary_calls = _patch_repos(
        monkeypatch,
        fake_db,
        RuntimeError("pg down"),
        SimpleNamespace(content="今天很平静，给常客多拉了一朵拉花。"),
    )

    conversation, character, state = _make_context_inputs()
    with capture_logs() as cap_logs:
        context = await svc._build_context(conversation=conversation, character=character, state=state, history=[])

    assert any(e.get("event") == "chat_reflections_load_failed_continue" for e in cap_logs)
    assert ref_calls == [3] and diary_calls == ["day"]
    assert context["reflections"] == "暂无"
    assert context["diary"] == "今天很平静，给常客多拉了一朵拉花。"

    system_prompt = prompts.render_system("chat", **context)
    assert "今天很平静" in system_prompt
