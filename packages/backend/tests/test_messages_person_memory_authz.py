"""Round-6 review HIGH 修复回归测试：角色消息聚合与 person-memory 归属校验

覆盖：
- GET /characters/{id}/messages：viewer 仅见本人会话，admin/operator 可跨用户聚合
- GET /characters/{id}/person-memory：仅本人或 admin/operator
- GET /characters/{id}/person-memory/list：RBAC 依赖接线锁定（仅 admin/operator）
"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, ClassVar, get_args
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pytest import MonkeyPatch
from starlette.requests import Request

import src.api.characters as characters_module
import src.api.memory as memory_module
from src.api.characters import router as characters_router
from src.api.memory import AdminOrOperator
from src.api.memory import router as memory_router
from src.auth import create_token
from src.config import settings
from src.db.session import db as db_singleton

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")
_CONV_A_ID = UUID("01964000-0000-7000-8000-00000000000a")
_CONV_B_ID = UUID("01964000-0000-7000-8000-00000000000b")


class FakeSession:
    async def execute(self, stmt: Any) -> None:
        raise AssertionError("route should only touch repositories")


class FakeSessionCtx:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


class FakeConversationRepository:
    instances: ClassVar[list["FakeConversationRepository"]] = []
    conversations: ClassVar[list[Any]] = []

    def __init__(self, session: Any) -> None:
        self.session = session
        FakeConversationRepository.instances.append(self)

    async def list_by_character(self, character_id: UUID, limit: int = 100) -> list[Any]:
        return list(FakeConversationRepository.conversations)


class FakeMessageRepository:
    instances: ClassVar[list["FakeMessageRepository"]] = []
    messages_by_conversation: ClassVar[dict[UUID, list[Any]]] = {}

    def __init__(self, session: Any) -> None:
        self.session = session
        FakeMessageRepository.instances.append(self)

    async def list_by_conversation(
        self,
        conversation_id: UUID,
        limit: int,
        order_desc: bool,
    ) -> list[Any]:
        return list(FakeMessageRepository.messages_by_conversation.get(conversation_id, []))[:limit]


@pytest.fixture
def fake_db(monkeypatch: MonkeyPatch) -> FakeSession:
    session = FakeSession()
    FakeConversationRepository.instances.clear()
    FakeMessageRepository.instances.clear()
    monkeypatch.setattr(db_singleton, "session", lambda: FakeSessionCtx(session))
    monkeypatch.setattr(characters_module, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(characters_module, "MessageRepository", FakeMessageRepository)
    return session


def _seed_two_user_conversations() -> None:
    base = datetime(2026, 8, 26, 12, 0, 0)
    conv_a = SimpleNamespace(id=_CONV_A_ID, user_id="user-a")
    conv_b = SimpleNamespace(id=_CONV_B_ID, user_id="user-b")
    FakeConversationRepository.conversations = [conv_a, conv_b]
    FakeMessageRepository.messages_by_conversation = {
        _CONV_A_ID: [
            SimpleNamespace(id=uuid4(), conversation_id=_CONV_A_ID, sender="user", content="a-msg", created_at=base),
        ],
        _CONV_B_ID: [
            SimpleNamespace(
                id=uuid4(),
                conversation_id=_CONV_B_ID,
                sender="user",
                content="b-msg",
                created_at=datetime(2026, 8, 26, 12, 1, 0),
            ),
        ],
    }


async def test_get_character_messages_viewer_sees_only_own_conversations(fake_db: FakeSession) -> None:
    _seed_two_user_conversations()
    result = await characters_module.get_character_messages(
        character_id=_CHARACTER_ID,
        user={"user_id": "user-a", "auth_method": "jwt", "role": "viewer"},
    )
    assert [m["content"] for m in result["data"]] == ["a-msg"]
    assert result["total"] == 1


async def test_get_character_messages_viewer_without_own_conversations_gets_empty(fake_db: FakeSession) -> None:
    _seed_two_user_conversations()
    result = await characters_module.get_character_messages(
        character_id=_CHARACTER_ID,
        user={"user_id": "user-nobody", "auth_method": "jwt", "role": "viewer"},
    )
    assert result == {"data": [], "total": 0}


async def test_get_character_messages_admin_aggregates_across_users(fake_db: FakeSession) -> None:
    _seed_two_user_conversations()
    result = await characters_module.get_character_messages(
        character_id=_CHARACTER_ID,
        user={"user_id": "boss", "auth_method": "jwt", "role": "admin"},
    )
    assert [m["content"] for m in result["data"]] == ["a-msg", "b-msg"]
    assert result["total"] == 2


async def test_get_character_messages_operator_aggregates_across_users(fake_db: FakeSession) -> None:
    _seed_two_user_conversations()
    result = await characters_module.get_character_messages(
        character_id=_CHARACTER_ID,
        user={"user_id": "ops", "auth_method": "jwt", "role": "operator"},
    )
    assert result["total"] == 2


def _request_with_headers(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "query_string": b"",
        }
    )


PRINCIPAL_DEPS = [characters_module.principal_with_role, memory_module.principal_with_role]


@pytest.mark.parametrize("principal_dep", PRINCIPAL_DEPS, ids=["characters", "memory"])
async def test_principal_with_role_extracts_jwt_role(principal_dep: Any) -> None:
    token = create_token("user-a", claims={"role": "operator"})
    principal = await principal_dep(_request_with_headers({"authorization": f"Bearer {token}"}))
    assert principal == {"user_id": "user-a", "auth_method": "jwt", "role": "operator"}


@pytest.mark.parametrize("principal_dep", PRINCIPAL_DEPS, ids=["characters", "memory"])
async def test_principal_with_role_api_key_has_no_privileged_role(
    principal_dep: Any,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "api_key", "test-static-key")
    principal = await principal_dep(_request_with_headers({"x-api-key": "test-static-key"}))
    assert principal == {"user_id": "static", "auth_method": "api_key", "role": "viewer"}


@pytest.mark.parametrize("principal_dep", PRINCIPAL_DEPS, ids=["characters", "memory"])
async def test_principal_with_role_rejects_missing_credentials(principal_dep: Any) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await principal_dep(_request_with_headers({}))
    assert exc_info.value.status_code == 401


class FakePersonMemoryService:
    calls: ClassVar[list[tuple[UUID, str]]] = []

    async def get_memory(self, character_id: UUID, user_id: str) -> dict[str, Any] | None:
        FakePersonMemoryService.calls.append((character_id, user_id))
        return {"heat": 1, "user_id": user_id}


@pytest.fixture
def fake_person_memory(monkeypatch: MonkeyPatch) -> type[FakePersonMemoryService]:
    FakePersonMemoryService.calls.clear()
    monkeypatch.setattr(memory_module, "_get_person_memory_service", lambda: FakePersonMemoryService())
    return FakePersonMemoryService


async def test_get_person_memory_rejects_cross_user_for_viewer(
    fake_person_memory: type[FakePersonMemoryService],
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await memory_module.get_person_memory(
            character_id=str(_CHARACTER_ID),
            user={"user_id": "user-a", "auth_method": "jwt", "role": "viewer"},
            user_id="user-b",
        )
    assert exc_info.value.status_code == 403
    assert fake_person_memory.calls == []


async def test_get_person_memory_owner_allowed(fake_person_memory: type[FakePersonMemoryService]) -> None:
    result = await memory_module.get_person_memory(
        character_id=str(_CHARACTER_ID),
        user={"user_id": "user-a", "auth_method": "jwt", "role": "viewer"},
        user_id="user-a",
    )
    assert result["exists"] is True
    assert result["data"]["user_id"] == "user-a"
    assert fake_person_memory.calls == [(_CHARACTER_ID, "user-a")]


async def test_get_person_memory_admin_reads_cross_user(fake_person_memory: type[FakePersonMemoryService]) -> None:
    result = await memory_module.get_person_memory(
        character_id=str(_CHARACTER_ID),
        user={"user_id": "boss", "auth_method": "jwt", "role": "admin"},
        user_id="user-b",
    )
    assert result["exists"] is True
    assert fake_person_memory.calls == [(_CHARACTER_ID, "user-b")]


def _find_route(path: str) -> APIRoute:
    for r in (*characters_router.routes, *memory_router.routes):
        if isinstance(r, APIRoute) and r.path == path:
            return r
    raise AssertionError(f"route not found: {path}")


def test_messages_route_requires_principal_with_role() -> None:
    route = _find_route("/api/v1/characters/{character_id}/messages")
    assert any(d.call is characters_module.principal_with_role for d in route.dependant.dependencies)


def test_person_memory_route_requires_principal_with_role() -> None:
    route = _find_route("/api/v1/characters/{character_id}/person-memory")
    assert any(d.call is memory_module.principal_with_role for d in route.dependant.dependencies)


def test_person_memory_list_route_guarded_by_admin_or_operator() -> None:
    route = _find_route("/api/v1/characters/{character_id}/person-memory/list")
    guard = get_args(AdminOrOperator)[1].dependency
    assert any(d.call is guard for d in route.dependant.dependencies)


async def test_rbac_dependency_denies_viewer() -> None:
    guard = get_args(AdminOrOperator)[1].dependency
    token = create_token("user-a")
    with pytest.raises(HTTPException) as exc_info:
        await guard(_request_with_headers({"authorization": f"Bearer {token}"}))
    assert exc_info.value.status_code == 403


async def test_rbac_dependency_admits_admin_and_operator() -> None:
    guard = get_args(AdminOrOperator)[1].dependency
    for role in ("admin", "operator"):
        token = create_token("boss", claims={"role": role})
        principal = await guard(_request_with_headers({"authorization": f"Bearer {token}"}))
        assert principal["role"] == role
