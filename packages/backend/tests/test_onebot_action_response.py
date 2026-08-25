"""OneBot action 响应解析与发送 failover 单元测试（纯逻辑，fake WebSocket）

覆盖 round-3 审查缺陷：
- M12：send-action 的 retcode 响应识别（v11 int / v12 str status）与归一化
- M13：回复发送跨连接 failover（优先原连接，全败上抛保住槽位释放契约）
- M14：重放按事件 self_id 选同账号连接
- M17：心跳新鲜度优先、半开连接发送超时驱逐
"""

import asyncio
import time
from typing import cast

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketState

import src.adapters.onebot as onebot_module
from src.adapters.onebot import OneBotAdapter, _parse_action_response


class FakeWS:
    """最小 WebSocket 桩：仅模拟 client_state 与 send_text"""

    def __init__(
        self,
        *,
        connected: bool = True,
        fail_with: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
        self.fail_with = fail_with
        self.delay = delay
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(text)


def _key(ws: FakeWS) -> WebSocket:
    return cast(WebSocket, ws)


def _adapter_with(*conns: FakeWS) -> OneBotAdapter:
    adapter = OneBotAdapter()
    for conn in conns:
        adapter._connections.add(_key(conn))
    return adapter


# === M12：action 响应解析 ===


def test_parse_v11_success() -> None:
    resp = _parse_action_response({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": "e1"})
    assert resp == {"ok": True, "status": "ok", "retcode": 0, "echo": "e1"}


def test_parse_v11_failed_retcode() -> None:
    resp = _parse_action_response({"status": "failed", "retcode": 1200, "data": None, "echo": "e2"})
    assert resp is not None
    assert resp["ok"] is False
    assert resp["retcode"] == 1200
    assert resp["echo"] == "e2"


def test_parse_v11_async_accepted() -> None:
    """v11 async（retcode=1）为已受理非失败"""
    resp = _parse_action_response({"status": "async", "retcode": 1})
    assert resp is not None
    assert resp["ok"] is True


def test_parse_v12_status_string_ok_and_failed() -> None:
    assert _parse_action_response({"status": "ok", "retcode": 0}) == {
        "ok": True,
        "status": "ok",
        "retcode": 0,
        "echo": None,
    }
    failed = _parse_action_response({"status": "failed", "retcode": 0, "message": "forbidden"})
    assert failed is not None
    assert failed["ok"] is False


def test_parse_ignores_event_frames() -> None:
    """带 post_type/type 的事件帧即使含同名字段也不是响应帧"""
    assert _parse_action_response({"post_type": "message", "status": "x", "retcode": 0}) is None
    assert _parse_action_response({"type": "meta_event", "status": "x", "retcode": 0}) is None


def test_parse_non_response_returns_none() -> None:
    assert _parse_action_response({"hello": "world"}) is None
    assert _parse_action_response({"status": "ok"}) is None
    assert _parse_action_response({"retcode": 0}) is None


# === M13/M17：发送 failover 与超时驱逐 ===


async def test_failover_prefers_origin_then_falls_back() -> None:
    dead = FakeWS(fail_with=RuntimeError("closed"))
    alive = FakeWS()
    adapter = _adapter_with(dead, alive)

    await adapter._send_with_failover(_key(dead), lambda: {"action": "send_private_msg", "params": {}})

    assert dead.sent == []
    assert len(alive.sent) == 1
    # 失败连接被驱逐，后续选择不再命中
    assert _key(dead) not in adapter._connections


async def test_failover_all_candidates_failed_raises_last_error() -> None:
    first = FakeWS(fail_with=RuntimeError("a"))
    second = FakeWS(fail_with=TimeoutError("b"))
    adapter = _adapter_with(first, second)

    with pytest.raises(TimeoutError):
        await adapter._send_with_failover(_key(first), lambda: {"action": "x"})

    assert adapter._connections.isdisjoint({_key(first), _key(second)})


async def test_failover_no_connected_candidate_raises() -> None:
    disconnected = FakeWS(connected=False)
    adapter = _adapter_with(disconnected)

    with pytest.raises(RuntimeError, match="no_connected_connection"):
        await adapter._send_with_failover(_key(disconnected), lambda: {"action": "x"})


async def test_send_timeout_evicts_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """半开连接：send 永久阻塞 → wait_for 超时 → 驱逐并抛出"""
    monkeypatch.setattr(onebot_module, "_SEND_TIMEOUT_SECONDS", 0.01)
    slow = FakeWS(delay=1.0)
    adapter = _adapter_with(slow)

    with pytest.raises(asyncio.TimeoutError):
        await adapter._send_with_failover(_key(slow), lambda: {"action": "x"})

    assert _key(slow) not in adapter._connections


# === M14/M17：按账号路由与心跳新鲜度 ===


async def test_ws_for_self_id_matches_account_connection() -> None:
    ws_a, ws_b = FakeWS(), FakeWS()
    adapter = _adapter_with(ws_a, ws_b)
    adapter._conn_self_id[_key(ws_a)] = "111"
    adapter._conn_self_id[_key(ws_b)] = "222"

    got = await adapter._ws_for_self_id(222)
    assert got is _key(ws_b)
    # 无匹配账号连接时返回 None（调用方退回 _any_ws）
    assert await adapter._ws_for_self_id("333") is None
    assert await adapter._ws_for_self_id(None) is None


async def test_any_ws_prefers_fresh_heartbeat() -> None:
    stale, fresh = FakeWS(), FakeWS()
    adapter = _adapter_with(stale, fresh)
    adapter._last_heartbeat[_key(stale)] = time.monotonic() - onebot_module._HEARTBEAT_FRESH_SECONDS * 5
    adapter._last_heartbeat[_key(fresh)] = time.monotonic()

    for _ in range(5):
        assert await adapter._any_ws() is _key(fresh)


async def test_meta_event_records_heartbeat_and_self_id() -> None:
    ws = FakeWS()
    adapter = _adapter_with(ws)

    await adapter._handle_meta_event(
        {"post_type": "meta_event", "detail_type": "heartbeat", "self_id": 777, "interval": 30000},
        _key(ws),
    )

    assert adapter._last_heartbeat[_key(ws)] > 0
    assert adapter._conn_self_id[_key(ws)] == "777"


async def test_unregister_cleans_side_records() -> None:
    ws = FakeWS()
    adapter = _adapter_with(ws)
    key = _key(ws)
    adapter._last_heartbeat[key] = time.monotonic()
    adapter._conn_self_id[key] = "1"

    await adapter._unregister(key)

    assert key not in adapter._connections
    assert key not in adapter._last_heartbeat
    assert key not in adapter._conn_self_id
