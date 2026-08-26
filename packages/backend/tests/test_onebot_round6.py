"""OneBot round-6 审查缺陷修复单元测试（fake WS / fake Redis / fake Queue，纯逻辑）

覆盖：
- R6-H6：消息事件异步派发——同会话 FIFO、跨会话并发、链满丢弃、
  流条目在派发任务成功后才 XACK+XDEL（失败留待恢复循环重放）
- R6-M3：回复去重键按 (self_id, character_id) 分桶，多实例/多角色互不压制
- R6-L6：出站 action 携带 echo 并按响应关联时延计数；心跳过期连接被驱逐；
  同会话出站最小间隔节拍
"""

import asyncio
import json
import time
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import WebSocket
from prometheus_client import REGISTRY
from starlette.websockets import WebSocketState
from structlog.testing import capture_logs

import src.adapters.onebot as onebot_module
import src.runtime as runtime
from src.adapters.onebot import OneBotAdapter, _dispatch_chat_key, _reply_dedup_key
from src.config import settings
from src.messaging.event_queue import EventQueue


class FakeWS:
    """最小 WebSocket 桩：client_state / send_text / close"""

    def __init__(self, *, connected: bool = True) -> None:
        self.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
        self.sent: list[str] = []
        self.closed: list[dict[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append({"code": code, "reason": reason})


class FakeKVRedis:
    """SETNX/DELETE 最小模拟：验证去重键格式与作用域"""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.kv:
                del self.kv[key]
                deleted += 1
        return deleted


class FakeQueue:
    """EventQueue 最小桩：记录 remove 调用相对 handler 的时序"""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.removed: list[str] = []

    async def enqueue(self, event: dict[str, Any]) -> str:
        return "e1"

    async def remove(self, entry_id: str) -> None:
        self.log.append("removed")
        self.removed.append(entry_id)


def _key(ws: FakeWS) -> WebSocket:
    return cast(WebSocket, ws)


_GROUP_EVENT = {"post_type": "message", "message_type": "group", "group_id": 888, "user_id": 1}


# === R6-H6：异步派发 ===


async def test_same_chat_events_processed_in_fifo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """同会话两条事件按到达顺序执行，前一条的人为延迟不颠倒顺序"""
    adapter = OneBotAdapter()
    calls: list[str] = []

    async def handler(event: dict[str, Any], ws: WebSocket) -> None:
        tag = str(event["raw_message"])
        calls.append(f"{tag}:start")
        if tag == "first":
            await asyncio.sleep(0.05)
        calls.append(f"{tag}:end")

    monkeypatch.setattr(adapter, "handle_event", handler)
    adapter._spawn_dispatch({**_GROUP_EVENT, "raw_message": "first"}, _key(FakeWS()), None)
    adapter._spawn_dispatch({**_GROUP_EVENT, "raw_message": "second"}, _key(FakeWS()), None)

    await adapter._chat_chains["g:888"]

    assert calls == ["first:start", "first:end", "second:start", "second:end"]


async def test_different_chats_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """不同会话互不阻塞：两个 handler 必须出现同时在途（峰值并发=2）"""
    adapter = OneBotAdapter()
    state = {"inflight": 0, "peak": 0}

    async def handler(event: dict[str, Any], ws: WebSocket) -> None:
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.05)
        state["inflight"] -= 1

    monkeypatch.setattr(adapter, "handle_event", handler)
    adapter._spawn_dispatch({**_GROUP_EVENT, "group_id": 1}, _key(FakeWS()), None)
    adapter._spawn_dispatch({**_GROUP_EVENT, "group_id": 2}, _key(FakeWS()), None)

    await asyncio.gather(adapter._chat_chains["g:1"], adapter._chat_chains["g:2"])

    assert state["peak"] == 2


async def test_chain_overflow_drops_event_beyond_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """单会话链满（16）后新事件被丢弃：告警日志 + 指标递增，流条目不动"""
    adapter = OneBotAdapter()
    gate = asyncio.Event()

    async def blocking_handler(event: dict[str, Any], ws: WebSocket) -> None:
        await gate.wait()

    monkeypatch.setattr(adapter, "handle_event", blocking_handler)
    ws = _key(FakeWS())
    for _ in range(onebot_module._DISPATCH_CHAIN_MAX_PER_CHAT):
        adapter._spawn_dispatch(dict(_GROUP_EVENT), ws, None)

    before = REGISTRY.get_sample_value("ai_town_onebot_dispatch_dropped_total", {"reason": "chain_overflow"}) or 0.0
    with capture_logs() as logs:
        adapter._spawn_dispatch(dict(_GROUP_EVENT), ws, None)

    assert any(e.get("event") == "onebot_dispatch_chain_overflow" for e in logs)
    after = REGISTRY.get_sample_value("ai_town_onebot_dispatch_dropped_total", {"reason": "chain_overflow"}) or 0.0
    assert after == pytest.approx(before + 1)

    gate.set()
    await adapter._chat_chains["g:888"]


async def test_stream_entry_removed_only_after_handler_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """XACK+XDEL 时序（R6-H6）：remove 必须发生在 handler 成功之后"""
    adapter = OneBotAdapter()
    fq = FakeQueue()

    async def ok_handler(event: dict[str, Any], ws: WebSocket) -> None:
        await asyncio.sleep(0.01)
        fq.log.append("handled")

    monkeypatch.setattr(adapter, "handle_event", ok_handler)
    adapter._spawn_dispatch(dict(_GROUP_EVENT), _key(FakeWS()), (cast(EventQueue, fq), "e1"))

    await adapter._chat_chains["g:888"]

    assert fq.log == ["handled", "removed"]


async def test_stream_entry_left_pending_on_handler_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """handler 失败时不确认条目，留给恢复循环重投（至少一次语义）"""
    adapter = OneBotAdapter()
    fq = FakeQueue()

    async def failing_handler(event: dict[str, Any], ws: WebSocket) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "handle_event", failing_handler)
    private_event = {"post_type": "message", "message_type": "private", "user_id": 42}
    adapter._spawn_dispatch(private_event, _key(FakeWS()), (cast(EventQueue, fq), "e1"))

    await adapter._chat_chains["u:42"]

    assert fq.removed == []


def test_dispatch_chat_key_matches_rate_limit_shape() -> None:
    """排序键与入站限流键同构：群按 group_id、私聊按 user_id"""
    assert _dispatch_chat_key({"post_type": "message", "message_type": "group", "group_id": 7}) == "g:7"
    assert _dispatch_chat_key({"post_type": "message", "message_type": "private", "user_id": 9}) == "u:9"
    # v12 风格 detail_type 同样可解析
    assert _dispatch_chat_key({"type": "message", "detail_type": "group", "group_id": 7}) == "g:7"


# === R6-M3：去重键作用域 ===


async def test_dedup_slot_scoped_by_self_and_character(monkeypatch: pytest.MonkeyPatch) -> None:
    """两角色/两账号共享同一 message_id 时互不压制（R6-M3 核心回归）"""
    fake = FakeKVRedis()
    monkeypatch.setattr(runtime, "_redis", fake)
    adapter = OneBotAdapter()
    char_a, char_b = UUID(int=1), UUID(int=2)

    assert await adapter._claim_reply_slot("111", char_a, "m1") is True
    assert await adapter._claim_reply_slot("111", char_a, "m1") is False
    assert await adapter._claim_reply_slot("111", char_b, "m1") is True
    assert await adapter._claim_reply_slot("222", char_a, "m1") is True

    assert f"onebot:msg:111:{char_a}:m1" in fake.kv


async def test_release_reply_slot_scoped_key_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """释放与认领使用同一作用域键：失败释放后重试可重新认领"""
    fake = FakeKVRedis()
    monkeypatch.setattr(runtime, "_redis", fake)
    adapter = OneBotAdapter()
    key = _reply_dedup_key("111", UUID(int=1), "m1")

    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is True
    await adapter._release_reply_slot("111", UUID(int=1), "m1")
    assert key not in fake.kv
    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is True
    assert fake.kv[key] == "1"


# === R6-L6：心跳过期驱逐 ===


async def test_stale_heartbeat_connection_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    """心跳超过阈值的连接被驱逐并 close（触发实现重连），新鲜连接不受影响"""
    monkeypatch.setattr(settings, "onebot_heartbeat_stale_seconds", 90.0)
    adapter = OneBotAdapter()
    stale_ws, fresh_ws = FakeWS(), FakeWS()
    adapter._connections.update({_key(stale_ws), _key(fresh_ws)})
    now = time.monotonic()
    adapter._last_heartbeat[_key(stale_ws)] = now - 200.0
    adapter._last_heartbeat[_key(fresh_ws)] = now

    evicted = await adapter._evict_stale_connections()

    assert evicted == 1
    assert _key(stale_ws) not in adapter._connections
    assert _key(stale_ws) not in adapter._last_heartbeat
    assert stale_ws.closed and stale_ws.closed[0]["code"] == 1001
    assert _key(fresh_ws) in adapter._connections
    assert fresh_ws.closed == []


async def test_connection_without_heartbeat_record_not_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    """元事件未到的全新连接不参与过期判定（由发送超时路径兜底）"""
    monkeypatch.setattr(settings, "onebot_heartbeat_stale_seconds", 90.0)
    adapter = OneBotAdapter()
    ws = FakeWS()
    adapter._connections.add(_key(ws))

    assert await adapter._evict_stale_connections() == 0
    assert _key(ws) in adapter._connections
    assert ws.closed == []


# === R6-L6：echo 关联 ===


async def test_send_single_attaches_echo_and_tracks_pending() -> None:
    """出站 action 必带 uuid4 hex echo，并登记 (发起时刻, action 名) 供关联"""
    adapter = OneBotAdapter()
    ws = FakeWS()

    await adapter._send_single(_key(ws), "private", 42, None, "hello")

    payload = json.loads(ws.sent[0])
    echo = payload["echo"]
    assert isinstance(echo, str) and len(echo) == 32
    assert adapter._pending_actions[echo][1] == "send_private_msg"


async def test_action_response_correlates_echo_to_action_counter() -> None:
    """响应帧按 echo 关联到发起的 action 名计数；挂起项随即清除"""
    adapter = OneBotAdapter()
    ws = FakeWS()
    await adapter._send_single(_key(ws), "private", 42, None, "hi")
    echo = json.loads(ws.sent[0])["echo"]

    adapter._record_action_response({"ok": True, "status": "ok", "retcode": 0, "echo": echo})

    assert echo not in adapter._pending_actions
    value = REGISTRY.get_sample_value(
        "ai_town_onebot_action_response_total",
        {"outcome": "success", "action": "send_private_msg"},
    )
    assert value is not None and value >= 1


async def test_action_response_without_matching_echo_counts_as_unknown() -> None:
    """无匹配 echo 的响应以 action="unknown" 计数（标签基数有界）"""
    adapter = OneBotAdapter()

    adapter._record_action_response({"ok": False, "status": "failed", "retcode": 1200, "echo": "stray"})

    value = REGISTRY.get_sample_value(
        "ai_town_onebot_action_response_total",
        {"outcome": "failed", "action": "unknown"},
    )
    assert value is not None and value >= 1


# === R6-L6：出站节拍 ===


async def test_outbound_pacing_enforces_min_interval_per_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一会话连续两次发送被拉开到最小间隔以上"""
    monkeypatch.setattr(settings, "onebot_send_min_interval_ms", 80)
    adapter = OneBotAdapter()
    ws = FakeWS()

    start = time.monotonic()
    await adapter._send_single(_key(ws), "private", 1, None, "a")
    await adapter._send_single(_key(ws), "private", 1, None, "b")
    elapsed = time.monotonic() - start

    assert len(ws.sent) == 2
    assert elapsed >= 0.06


async def test_outbound_pacing_isolated_between_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    """不同会话互不限速：各自首次发送均立即发出"""
    monkeypatch.setattr(settings, "onebot_send_min_interval_ms", 500)
    adapter = OneBotAdapter()
    ws = FakeWS()

    start = time.monotonic()
    await adapter._send_single(_key(ws), "private", 1, None, "a")
    await adapter._send_single(_key(ws), "private", 2, None, "b")
    elapsed = time.monotonic() - start

    assert len(ws.sent) == 2
    assert elapsed < 0.4


async def test_outbound_pacing_disabled_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """间隔配置为 0 时完全禁用节拍（若生效本应等待 ≥1 秒）"""
    monkeypatch.setattr(settings, "onebot_send_min_interval_ms", 0)
    adapter = OneBotAdapter()
    ws = FakeWS()

    start = time.monotonic()
    await adapter._send_single(_key(ws), "private", 1, None, "a")
    await adapter._send_single(_key(ws), "private", 1, None, "b")
    elapsed = time.monotonic() - start

    assert len(ws.sent) == 2
    assert elapsed < 0.5
