"""P0-8 遗留修复：GET /api/v1/conversations 归属过滤

修复目标：
- 端点要求认证（Depends(get_current_user)）
- user_id 参数与当前用户不一致时拒绝（403）
- 所有查询分支强制绑定当前用户，防止枚举他人会话
"""

from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from pytest import MonkeyPatch

import src.api.messages as messages_module
from src.api.messages import list_conversations
from src.db.session import db as db_singleton

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> list[Any]:
        return self._items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None


class FakeSession:
    def __init__(self) -> None:
        self.executed_stmts: list[Any] = []

    async def execute(self, stmt: Any) -> FakeResult:
        self.executed_stmts.append(stmt)
        return FakeResult([])


class FakeSessionCtx:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


class FakeRepo:
    instances: list["FakeRepo"] = []

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.get_by_user_character_calls: list[tuple[str, UUID, str | None]] = []
        FakeRepo.instances.append(self)

    async def get_by_user_character(
        self,
        user_id: str,
        character_id: UUID,
        platform: str | None = None,
    ) -> None:
        self.get_by_user_character_calls.append((user_id, character_id, platform))
        return None


@pytest.fixture
def fake_db(monkeypatch: MonkeyPatch) -> FakeSession:
    FakeRepo.instances.clear()
    session = FakeSession()
    monkeypatch.setattr(db_singleton, "session", lambda: FakeSessionCtx(session))
    monkeypatch.setattr(messages_module, "ConversationRepository", FakeRepo)
    return session


def _compiled_sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


async def test_list_conversations_rejects_other_user(fake_db: FakeSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await list_conversations(
            current_user={"user_id": "user-a"},
            user_id="user-b",
        )
    assert exc_info.value.status_code == 403
    assert fake_db.executed_stmts == []


async def test_list_conversations_exact_query_uses_authed_user(fake_db: FakeSession) -> None:
    await list_conversations(
        current_user={"user_id": "user-a"},
        character_id=str(_CHARACTER_ID),
        user_id="user-a",
    )
    repo = FakeRepo.instances[-1]
    assert repo.get_by_user_character_calls == [("user-a", _CHARACTER_ID, None)]


async def test_list_conversations_character_branch_filters_user(fake_db: FakeSession) -> None:
    await list_conversations(
        current_user={"user_id": "user-a"},
        character_id=str(_CHARACTER_ID),
    )
    assert len(fake_db.executed_stmts) == 1
    sql = _compiled_sql(fake_db.executed_stmts[0])
    assert "user_id" in sql
    assert "'user-a'" in sql
    assert _CHARACTER_ID.hex in sql


async def test_list_conversations_no_filter_returns_own_only(fake_db: FakeSession) -> None:
    await list_conversations(current_user={"user_id": "user-a"})
    assert len(fake_db.executed_stmts) == 1
    sql = _compiled_sql(fake_db.executed_stmts[0])
    assert "user_id" in sql
    assert "'user-a'" in sql
