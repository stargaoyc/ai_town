"""P-4/P-5 回归测试：消息服务加固

验证目标（docs/design-improvement-and-fixes.md P-4/P-5）：
- 群聊回复概率常量化且取值合法，统一概率闸门语义正确
- 分享投递按用户去重（同用户多会话只推送一次）
- WebSocket 推送的 character_id 必须为 str（UUID 类型会导致连接表 key 永不相等）
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

import src.messaging.proactive_sharing as ps_module
from src.db.models import Character
from src.llm import LLMClient, PromptTemplates
from src.messaging.proactive_sharing import ProactiveSharingService
from src.messaging.service import (
    GROUP_REPLY_EMOTION_PROBABILITY,
    GROUP_REPLY_LLM_ERROR_FALLBACK,
    GROUP_REPLY_LLM_NO_FALLBACK,
    GROUP_REPLY_PROBABILITY_CAP,
    _probability_roll,
)

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeSession:
    async def commit(self) -> None:
        pass


class FakeConversationRepo:
    def __init__(self, conversations: list[Any]) -> None:
        self._convs = conversations

    async def list_by_character(self, character_id: UUID, limit: int = 100) -> list[Any]:
        return self._convs


class FakeMessageRepo:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> None:
        self.added.append(kwargs)


class FakeWSManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send_to_user(self, user_id: str, character_id: str, message: dict[str, Any]) -> bool:
        self.calls.append((user_id, character_id))
        return True


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    conversations: list[Any],
) -> tuple[ProactiveSharingService, FakeWSManager, FakeMessageRepo]:
    fake_session = FakeSession()
    conv_repo = FakeConversationRepo(conversations)
    msg_repo = FakeMessageRepo()
    ws_manager = FakeWSManager()
    monkeypatch.setattr(ps_module, "ConversationRepository", lambda session: conv_repo)
    monkeypatch.setattr(ps_module, "MessageRepository", lambda session: msg_repo)
    service = ProactiveSharingService(
        session=cast(Any, fake_session),
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
        ws_manager=ws_manager,
    )
    return service, ws_manager, msg_repo


def test_group_reply_probabilities_valid() -> None:
    for p in (
        GROUP_REPLY_PROBABILITY_CAP,
        GROUP_REPLY_EMOTION_PROBABILITY,
        GROUP_REPLY_LLM_NO_FALLBACK,
        GROUP_REPLY_LLM_ERROR_FALLBACK,
    ):
        assert 0 <= p <= 1
    assert _probability_roll(0.0) is False
    assert _probability_roll(1.0) is True


async def test_deliver_share_dedupes_users_and_passes_str_character_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 同一用户的两个会话：写库两次、推送一次
    conversations = [
        SimpleNamespace(id=uuid4(), user_id="user-a"),
        SimpleNamespace(id=uuid4(), user_id="user-a"),
    ]
    service, ws_manager, msg_repo = _make_service(monkeypatch, conversations)

    fake_character = cast(Character, SimpleNamespace(id=_CHARACTER_ID, name="小艾"))

    delivered = await service._deliver_share(_CHARACTER_ID, fake_character, "今天天气真好")

    await asyncio.sleep(0.05)  # 让后台推送任务执行完毕

    assert delivered == 1
    assert len(msg_repo.added) == 2
    assert len(ws_manager.calls) == 1
    user_id, character_id = ws_manager.calls[0]
    assert user_id == "user-a"
    # P-5 类型 bug 回归：character_id 必须是 str（UUID 会导致 key 永不相等）
    assert isinstance(character_id, str)
    assert character_id == str(_CHARACTER_ID)


async def test_deliver_share_pushes_each_distinct_user_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations = [
        SimpleNamespace(id=uuid4(), user_id="user-a"),
        SimpleNamespace(id=uuid4(), user_id="user-b"),
    ]
    service, ws_manager, msg_repo = _make_service(monkeypatch, conversations)

    fake_character = cast(Character, SimpleNamespace(id=_CHARACTER_ID, name="小艾"))

    delivered = await service._deliver_share(_CHARACTER_ID, fake_character, "周末愉快")

    await asyncio.sleep(0.05)

    assert delivered == 2
    assert len(msg_repo.added) == 2
    assert len(ws_manager.calls) == 2
