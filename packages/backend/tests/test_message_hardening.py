"""P-4/P-5 回归测试：消息服务加固

验证目标（docs/design-improvement-and-fixes.md P-4/P-5 与 Round-5 审查 R5-M4/M5/M12）：
- 群聊回复概率常量化且取值合法，统一概率闸门语义正确
- 分享投递按用户去重（同用户多会话只推送一次）
- WebSocket 推送的 character_id 必须为 str（UUID 类型会导致连接表 key 永不相等）
- 失败连接清理做同一性比较，不误删重连后的新连接（R5-M4）
- 分享扇出单条写库失败触发回滚，幸存会话仍落库提交（R5-M5）
- /ws/chat 支持 Sec-WebSocket-Protocol bearer 鉴权（R5-M12）
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

import src.messaging.proactive_sharing as ps_module
import src.messaging.websocket as ws_module
from src.auth import create_token
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
from src.messaging.websocket import (
    WebSocketManager,
    _extract_bearer_subprotocol,
    ws_chat_endpoint,
)

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


class FakeConversationRepo:
    def __init__(self, conversations: list[Any]) -> None:
        self._convs = conversations

    async def list_by_character(self, character_id: UUID, limit: int = 100) -> list[Any]:
        return self._convs


class FakeMessageRepo:
    def __init__(self, failing_conversation_ids: set[UUID] | None = None) -> None:
        self.added: list[dict[str, Any]] = []
        # 模拟单条约束冲突：命中 conversation_id 的写入直接抛错（R5-M5 场景）
        self._failing_ids: frozenset[UUID] = frozenset(failing_conversation_ids or ())

    async def add(self, **kwargs: Any) -> None:
        if kwargs["conversation_id"] in self._failing_ids:
            raise RuntimeError("simulated constraint violation")
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
    failing_conversation_ids: set[UUID] | None = None,
) -> tuple[ProactiveSharingService, FakeWSManager, FakeMessageRepo]:
    fake_session = FakeSession()
    conv_repo = FakeConversationRepo(conversations)
    msg_repo = FakeMessageRepo(failing_conversation_ids)
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


# === R5-M4：失败连接清理必须做同一性比较（重连保护） ===


class FakeWebSocket:
    """WebSocketManager 与 ws_chat_endpoint 触碰面的最小替身"""

    def __init__(
        self,
        *,
        on_send: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.close_codes: list[int] = []
        self.accepted_subprotocols: list[str | None] = []
        self.client_state = WebSocketState.CONNECTED
        self.headers: dict[str, str] = {}
        self._on_send = on_send

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted_subprotocols.append(subprotocol)

    async def send_json(self, message: dict[str, Any]) -> None:
        if self._on_send is not None:
            await self._on_send(message)
            return
        self.sent_messages.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_codes.append(code)

    async def receive_text(self) -> str:
        # 端点消息循环首次读取即视为客户端断开，让会话干净收尾
        raise WebSocketDisconnect(code=1000)


@pytest.fixture
def fresh_manager() -> Iterator[WebSocketManager]:
    # 单例跨测试共享进程状态：前后重置避免污染其他用例
    WebSocketManager._instance = None
    manager = WebSocketManager()
    yield manager
    WebSocketManager._instance = None


async def test_broadcast_failure_does_not_evict_reconnected_connection(
    fresh_manager: WebSocketManager,
) -> None:
    uid = "user-a"
    character_id = str(uuid4())
    ws_old = FakeWebSocket()
    ws_new = FakeWebSocket()

    async def stale_send(message: dict[str, Any]) -> None:
        # 模拟发送挂起期间用户重连：新连接顶掉旧连接占住同一 key
        await fresh_manager.connect(cast(Any, ws_new), uid, character_id)
        raise ConnectionError("stale connection")

    ws_old._on_send = stale_send
    await fresh_manager.connect(cast(Any, ws_old), uid, character_id)

    sent = await fresh_manager.broadcast(character_id, {"type": "share", "content": "hi"})

    assert sent == 0
    # 幸存的是重连后的新连接，旧连接已被重连路径关闭
    assert await fresh_manager.get_connection_count() == 1
    assert await fresh_manager.send_to_user(uid, character_id, {"type": "ping"}) is True
    assert ws_new.sent_messages == [{"type": "ping"}]


async def test_send_to_user_timeout_does_not_evict_replacement_connection(
    fresh_manager: WebSocketManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ws_module, "_WS_SEND_TIMEOUT_SECONDS", 0.05)
    uid = "user-a"
    character_id = str(uuid4())
    ws_old = FakeWebSocket()
    ws_new = FakeWebSocket()

    async def hung_send(message: dict[str, Any]) -> None:
        # 发送挂起期间新连接顶掉旧连接；sleep 远超测试用超时必然触发 TimeoutError
        await fresh_manager.connect(cast(Any, ws_new), uid, character_id)
        await asyncio.sleep(30)

    ws_old._on_send = hung_send
    await fresh_manager.connect(cast(Any, ws_old), uid, character_id)

    ok = await fresh_manager.send_to_user(uid, character_id, {"type": "share"})

    assert ok is False
    # 超时只允许清理挂起的旧连接，重连进来的新连接不能被误删
    assert await fresh_manager.get_connection_count() == 1
    assert await fresh_manager.send_to_user(uid, character_id, {"type": "ping"}) is True
    assert ws_new.sent_messages == [{"type": "ping"}]


# === R5-M5：扇出单条失败回滚隔离，幸存会话照常落库提交 ===


async def test_deliver_share_rolls_back_poisoned_session_and_saves_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_bad = SimpleNamespace(id=uuid4(), user_id="user-a")
    conv_good = SimpleNamespace(id=uuid4(), user_id="user-b")
    service, ws_manager, msg_repo = _make_service(
        monkeypatch,
        [conv_bad, conv_good],
        failing_conversation_ids={conv_bad.id},
    )

    fake_character = cast(Character, SimpleNamespace(id=_CHARACTER_ID, name="小艾"))

    delivered = await service._deliver_share(_CHARACTER_ID, fake_character, "分享内容")

    await asyncio.sleep(0.05)  # 让后台推送任务执行完毕

    fake_session = cast(Any, service.session)
    # 首条写库抛错必须回滚：session 卡在 pending-rollback 时后续写入全部
    # 抛 PendingRollbackError 且最终 commit 整批丢失（R5-M5）
    assert fake_session.rollback_count == 1
    assert [record["conversation_id"] for record in msg_repo.added] == [conv_good.id]
    assert delivered == 1
    # 幸存会话仍走既定的「先 commit 落库、后触发推送」顺序
    assert fake_session.commit_count == 1
    assert ws_manager.calls == [("user-b", str(_CHARACTER_ID))]


# === R5-M12：/ws/chat 支持 Sec-WebSocket-Protocol bearer 鉴权 ===


def test_extract_bearer_subprotocol_parses_token() -> None:
    ws = SimpleNamespace(headers={"sec-websocket-protocol": "bearer, abc.def.ghi"})
    assert _extract_bearer_subprotocol(cast(Any, ws)) == "abc.def.ghi"


def test_extract_bearer_subprotocol_ignores_missing_header() -> None:
    ws = SimpleNamespace(headers={})
    assert _extract_bearer_subprotocol(cast(Any, ws)) is None


def test_extract_bearer_subprotocol_rejects_wrong_keyword() -> None:
    ws = SimpleNamespace(headers={"sec-websocket-protocol": "chat, superchat"})
    assert _extract_bearer_subprotocol(cast(Any, ws)) is None


async def test_chat_ws_accepts_valid_bearer_subprotocol(
    fresh_manager: WebSocketManager,
) -> None:
    token = create_token("user-ws")
    ws = FakeWebSocket()
    ws.headers["sec-websocket-protocol"] = f"bearer, {token}"
    character_id = str(uuid4())

    await ws_chat_endpoint(
        websocket=cast(Any, ws),
        character_id=character_id,
        user_id="user-ws",
        platform="web",
        token=None,
    )

    # RFC 6455：以 subprotocol 携带 token 时握手必须回选 bearer，
    # 否则浏览器端构造器直接握手失败
    assert ws.accepted_subprotocols == ["bearer"]
    assert ws.sent_messages[0]["type"] == "connected"
    # 会话结束后按同一性清理的是本会话这条连接
    assert await fresh_manager.get_connection_count() == 0


async def test_chat_ws_rejects_invalid_bearer_subprotocol_token(
    fresh_manager: WebSocketManager,
) -> None:
    ws = FakeWebSocket()
    ws.headers["sec-websocket-protocol"] = "bearer, not-a-jwt"
    character_id = str(uuid4())

    await ws_chat_endpoint(
        websocket=cast(Any, ws),
        character_id=character_id,
        user_id="user-ws",
        platform="web",
        token=None,
    )

    error_frame = ws.sent_messages[0]
    assert ws.accepted_subprotocols == [None]
    assert error_frame["type"] == "error"
    assert "invalid token" in error_frame["message"]
    assert ws.close_codes == [1008]
    assert await fresh_manager.get_connection_count() == 0


async def test_chat_ws_authorization_header_fallback_still_works(
    fresh_manager: WebSocketManager,
) -> None:
    token = create_token("user-ws")
    ws = FakeWebSocket()
    ws.headers["authorization"] = f"Bearer {token}"
    character_id = str(uuid4())

    await ws_chat_endpoint(
        websocket=cast(Any, ws),
        character_id=character_id,
        user_id="user-ws",
        platform="web",
        token=None,
    )

    # 无 subprotocol 时不得回选子协议；Authorization 头路径保持兼容
    assert ws.accepted_subprotocols == [None]
    assert ws.sent_messages[0]["type"] == "connected"
    assert await fresh_manager.get_connection_count() == 0
