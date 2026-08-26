"""Round-6 review R6-L12 / R6-L13 回归测试：API 错误卫生

覆盖：
- R6-L12a：LLM 生成失败时错误回复以 sender="system" 落库，不混入角色人设；
            system 消息不进 prompt history
- R6-L12b：JSON 提取失败返回固定歉意文案，不透传 raw JSON/blob 原文
- R6-L13：500/400 响应 detail 不泄露 str(e)；真实错误带 exc_info 记入日志
"""

import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from pytest import MonkeyPatch
from starlette.requests import Request
from structlog.testing import capture_logs

import src.api.admin as admin_module
import src.api.messages as messages_module
from src.api.exceptions import global_exception_handler
from src.api.messages import send_message
from src.config import settings
from src.db.session import db as db_singleton
from src.llm import LLMClient, PromptTemplates
from src.messaging.service import (
    DEFAULT_ERROR_REPLY,
    REPLY_EXTRACTION_FALLBACK,
    MessageService,
)

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")
_CONVERSATION_ID = UUID("01964000-0000-7000-8000-0000000000ff")


# ---------------------------------------------------------------------------
# R6-L12b：JSON 提取兜底
# ---------------------------------------------------------------------------


def test_extract_chat_response_structured_reply_preserved() -> None:
    """合法结构化回复原样保留（策略 1/2 不受影响）"""
    assert MessageService._extract_chat_response('{"response": "你好"}') == "你好"
    assert MessageService._extract_chat_response('```json\n{"response": "带代码块的"}\n```') == "带代码块的"
    assert MessageService._extract_chat_response('{"response": "跨行\\n内容"}') == "跨行\n内容"


def test_extract_chat_response_unparseable_blob_returns_apology() -> None:
    """解析失败时返回固定歉意文案，而非 raw JSON/blob 原文"""
    assert MessageService._extract_chat_response('{"foo": "bar"}') == REPLY_EXTRACTION_FALLBACK
    assert MessageService._extract_chat_response("这不是 JSON") == REPLY_EXTRACTION_FALLBACK


# ---------------------------------------------------------------------------
# R6-L12a：错误回复落库 sender 与 prompt 上下文
# ---------------------------------------------------------------------------


class FakeSession:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class FakeConversationRepo:
    async def get_or_create(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(id=_CONVERSATION_ID, context=None)

    async def touch_last_message(self, conversation_id: UUID) -> None:
        pass

    async def update_context(self, **kwargs: Any) -> None:
        pass


class FakeMessageRepo:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> SimpleNamespace:
        self.added.append(kwargs)
        return SimpleNamespace(id=UUID(int=7))

    async def list_recent(self, conversation_id: UUID, limit: int) -> list[Any]:
        return []

    async def list_by_conversation(
        self,
        conversation_id: UUID,
        limit: int,
        order_desc: bool,
    ) -> list[Any]:
        return []


class FakeCharacterRepo:
    async def get_character_with_state(self, character_id: UUID) -> tuple[Any, Any]:
        char = SimpleNamespace(id=character_id, name="阿澄", traits={}, backstory=None)
        state = SimpleNamespace(location="home", stamina=80, mood="calm")
        return (char, state)


class StubPersonMemoryService:
    def __init__(self, **kwargs: Any) -> None:
        pass

    async def get_relevant_context(self, **kwargs: Any) -> str:
        return "（初次与该用户交流）"

    async def update_memory(self, **kwargs: Any) -> None:
        pass


def _make_error_service(
    monkeypatch: MonkeyPatch,
    msg_repo: FakeMessageRepo,
) -> MessageService:
    async def fake_generate_reply_error(
        self: Any,
        character: Any,
        context: dict[str, Any],
        history: list[Any],
        user_message: str,
    ) -> tuple[str, int, float, str]:
        return DEFAULT_ERROR_REPLY, 0, 0.0, "llm unavailable"

    monkeypatch.setattr(MessageService, "_generate_reply", fake_generate_reply_error)
    monkeypatch.setattr(settings, "chat_inject_cognition", False)
    monkeypatch.setattr("src.memory.person_memory_service.PersonMemoryService", StubPersonMemoryService)

    svc = MessageService(
        session=cast(Any, FakeSession()),
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
    )
    svc.conversation_repo = cast(Any, FakeConversationRepo())
    svc.message_repo = cast(Any, msg_repo)
    svc.character_repo = cast(Any, FakeCharacterRepo())
    svc.redis = None
    return svc


async def test_error_reply_persisted_as_system_sender(monkeypatch: MonkeyPatch) -> None:
    msg_repo = FakeMessageRepo()
    svc = _make_error_service(monkeypatch, msg_repo)

    result = await svc.handle_user_message(
        character_id=_CHARACTER_ID,
        user_id="alice",
        platform="web",
        content="你好",
    )

    # 用户消息先落库，错误回复随后以 system 身份落库（不进角色人设）
    assert [m["sender"] for m in msg_repo.added] == ["user", "system"]
    assert msg_repo.added[1]["content"] == DEFAULT_ERROR_REPLY
    assert msg_repo.added[1]["tokens"] == 0
    assert msg_repo.added[1]["extra_data"] == {"error": "llm unavailable"}
    assert result["content"] == DEFAULT_ERROR_REPLY
    assert result["error"] == "llm unavailable"


class CaptureHistoryPrompts:
    def __init__(self) -> None:
        self.history_seen: str | None = None

    def has_system(self, name: str) -> bool:
        return False

    def render_system(self, name: str, **kwargs: Any) -> None:
        return None

    def render(self, name: str, **kwargs: Any) -> str:
        self.history_seen = kwargs.get("history", "")
        return "rendered"


class RecordingLLM:
    async def chat_with_usage(self, prompt: str, system_prompt: str | None = None) -> Any:
        return '{"response": "你好"}', SimpleNamespace(total_tokens=5, cost=0.001)


async def test_system_message_excluded_from_prompt_history() -> None:
    prompts = CaptureHistoryPrompts()
    svc = MessageService(
        session=cast(Any, FakeSession()),
        llm=cast(LLMClient, RecordingLLM()),
        prompts=cast(PromptTemplates, prompts),
    )

    history = [
        SimpleNamespace(sender="user", content="你好"),
        SimpleNamespace(sender="system", content=DEFAULT_ERROR_REPLY),
        SimpleNamespace(sender="character", content="真正的回复"),
    ]
    await svc._generate_reply(
        character=cast(Any, SimpleNamespace(id=_CHARACTER_ID, name="阿澄")),
        context={},
        history=cast(list[Any], history),
        user_message="你好",
    )

    # history 组装只保留 user/character，system（错误回复）不进 prompt
    assert prompts.history_seen is not None
    assert DEFAULT_ERROR_REPLY not in prompts.history_seen
    assert "真正的回复" in prompts.history_seen


# ---------------------------------------------------------------------------
# R6-L13：500 响应不泄露 str(e)，真实错误带 exc_info 入日志
# ---------------------------------------------------------------------------


class FakeSessionCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: Any) -> None:
        pass


async def test_send_message_500_detail_generic_and_logged(monkeypatch: MonkeyPatch) -> None:
    async def boom(self: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("secret: user record 42 failed")

    monkeypatch.setattr(db_singleton, "session", lambda: FakeSessionCtx())
    monkeypatch.setattr(MessageService, "handle_user_message", boom)
    monkeypatch.setattr(messages_module, "get_llm", lambda: object())
    monkeypatch.setattr(messages_module, "get_prompts", lambda: object())
    monkeypatch.setattr(messages_module, "get_redis", lambda: object())

    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc_info:
            await send_message(
                {"user_id": "alice", "auth_method": "jwt"},
                character_id=str(_CHARACTER_ID),
                user_id="alice",
                content="hi",
            )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"
    assert "secret" not in str(exc_info.value.detail)
    assert any(e.get("event") == "message_handle_failed" for e in logs)


async def test_exception_handler_value_error_generic_detail_and_exc_info() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )

    with capture_logs() as logs:
        resp = await global_exception_handler(request, ValueError("boom secret"))

    assert resp.status_code == 400
    body = json.loads(bytes(resp.body).decode())
    assert body["detail"] == "Bad request"
    assert "boom" not in body["detail"]
    assert body["trace_id"]
    client_events = [e for e in logs if e.get("event") == "client_error"]
    assert len(client_events) == 1
    assert client_events[0]["error"] == "boom secret"
    assert client_events[0].get("exc_info") is True


class FakeWorldEngine:
    is_leader = True
    tick_id = 42

    async def _is_still_leader(self) -> bool:
        return True

    async def execute_tick(self) -> None:
        raise RuntimeError("secret tick failure")


async def test_force_world_tick_500_detail_generic_and_logged(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(admin_module, "get_world_engine", lambda: FakeWorldEngine())

    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc_info:
            await admin_module.force_world_tick({"user_id": "boss", "auth_method": "jwt", "role": "admin"})

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"
    assert "secret" not in str(exc_info.value.detail)
    assert any(e.get("event") == "force_world_tick_failed" for e in logs)


class EmbedBoomLLM:
    async def embed(self, query: str) -> list[float]:
        raise RuntimeError("secret embed failure")


async def test_vector_search_500_detail_generic_and_logged(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(admin_module, "get_llm", lambda: EmbedBoomLLM())

    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc_info:
            await admin_module.vector_search(
                {"user_id": "boss", "auth_method": "jwt", "role": "admin"},
                query="猫",
            )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"
    assert "secret" not in str(exc_info.value.detail)
    assert any(e.get("event") == "vector_search_failed" for e in logs)
