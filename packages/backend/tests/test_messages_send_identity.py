"""POST /messages/send 身份绑定测试（R4-H3）。

JWT 用户只能以本人身份发言（user_id == token sub）；
API Key 主体属机器对机器桥接，允许代发任意 user_id。
"""

from typing import Any

import pytest
from fastapi import HTTPException
from pytest import MonkeyPatch

import src.api.messages as messages_module
from src.api.messages import send_message
from src.db.session import db as db_singleton
from src.messaging.service import MessageService

_CHARACTER_ID = "01964000-0000-7000-8000-000000000001"


class FakeSessionCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: Any) -> None:
        pass


@pytest.fixture
def captured(monkeypatch: MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {}

    async def fake_handle(self: Any, **kwargs: Any) -> dict[str, Any]:
        box.update(kwargs)
        return {
            "conversation_id": "01964000-0000-7000-8000-0000000000ff",
            "message_id": "01964000-0000-7000-8000-0000000000fe",
            "content": "ok",
            "tokens": 1,
            "cost": 0.0,
            "error": None,
        }

    monkeypatch.setattr(db_singleton, "session", lambda: FakeSessionCtx())
    monkeypatch.setattr(MessageService, "handle_user_message", fake_handle)
    monkeypatch.setattr(messages_module, "get_llm", lambda: object())
    monkeypatch.setattr(messages_module, "get_prompts", lambda: object())
    monkeypatch.setattr(messages_module, "get_redis", lambda: object())
    return box


async def test_jwt_user_cannot_impersonate_others(captured: dict[str, Any]) -> None:
    principal = {"user_id": "alice", "auth_method": "jwt"}
    with pytest.raises(HTTPException) as exc_info:
        await send_message(
            principal,
            character_id=_CHARACTER_ID,
            user_id="bob",
            content="hi",
        )
    assert exc_info.value.status_code == 403
    assert captured == {}


async def test_jwt_user_own_identity_passes_through(captured: dict[str, Any]) -> None:
    principal = {"user_id": "alice", "auth_method": "jwt"}
    result = await send_message(
        principal,
        character_id=_CHARACTER_ID,
        user_id="alice",
        content="hi",
    )
    assert captured["user_id"] == "alice"
    assert result["data"]["content"] == "ok"


async def test_api_key_principal_may_send_as_anyone(captured: dict[str, Any]) -> None:
    principal = {"user_id": "static", "auth_method": "api_key"}
    await send_message(
        principal,
        character_id=_CHARACTER_ID,
        user_id="qq_12345",
        content="hi",
    )
    assert captured["user_id"] == "qq_12345"
