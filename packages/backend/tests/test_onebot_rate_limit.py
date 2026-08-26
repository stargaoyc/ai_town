"""OneBot round-5 审查缺陷修复单元测试（fake Redis / fake WS，纯逻辑）

覆盖：
- R5-M7：每会话入站固定窗口限流——第 N+1 条在任何 LLM 路径前被静默丢弃、
  窗口过期后重新放行、0=禁用、群/私聊计数隔离
- R5-L9：群共享上下文环读取为旧→新序；角色群回复成功后写回环且不重复记录触发消息
- R5-L10：事件与配置均缺 self_id 时一次性告警

（R5-H5 的启动令牌检查契约见 tests/test_production_secrets.py）
"""

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import WebSocket
from structlog.testing import capture_logs

import src.adapters.onebot as onebot_module
import src.runtime as runtime
from src.adapters.onebot import OneBotAdapter
from src.config import settings


class FakeRedis:
    """最小 Redis 模拟：RateLimiter 的原子 INCR+EXPIRE 脚本与群上下文环命令"""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def eval(self, script: str, numkeys: int, key: str, window_seconds: int) -> int:
        count = self.counters.get(key, 0) + 1
        self.counters[key] = count
        if count == 1:
            self.ttls[key] = window_seconds
        return count

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

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

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        lst = self.lists.setdefault(key, [])
        self.lists[key] = lst[start:] if end == -1 else lst[start : end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        return lst[start:] if end == -1 else lst[start : end + 1]


@pytest.fixture
def reset_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """重置模块级 once-flag，隔离告警次数断言"""
    monkeypatch.setattr(onebot_module, "_SELF_ID_WARNING_EMITTED", False)


class StubSessionCtx:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class StubDB:
    def session(self) -> StubSessionCtx:
        return StubSessionCtx()


class RecordingSendAdapter(OneBotAdapter):
    """跳过真实 WS 帧发送，只记录最终发出的文本"""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def _send_single(
        self,
        onebot_ws: WebSocket,
        event_type: str,
        user_id: str | int | None,
        group_id: str | int | None,
        message: str,
        segment_index: int = 0,
        segment_total: int = 1,
    ) -> None:
        self.sent.append(message)


# === R5-M7：入站限流 ===


async def test_rate_limit_drops_n_plus_one_message_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(settings, "onebot_rate_limit_per_minute", 2)
    adapter = OneBotAdapter()

    assert await adapter._check_inbound_rate_limit("g", "888") is True
    assert await adapter._check_inbound_rate_limit("g", "888") is True
    assert await adapter._check_inbound_rate_limit("g", "888") is False


async def test_rate_limit_passes_again_after_window_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(settings, "onebot_rate_limit_per_minute", 1)
    adapter = OneBotAdapter()

    assert await adapter._check_inbound_rate_limit("u", 42) is True
    assert await adapter._check_inbound_rate_limit("u", 42) is False

    redis.counters.clear()

    assert await adapter._check_inbound_rate_limit("u", 42) is True


async def test_rate_limit_isolated_between_chats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(settings, "onebot_rate_limit_per_minute", 1)
    adapter = OneBotAdapter()

    assert await adapter._check_inbound_rate_limit("g", "888") is True
    assert await adapter._check_inbound_rate_limit("u", "888") is True
    assert await adapter._check_inbound_rate_limit("g", "999") is True
    assert await adapter._check_inbound_rate_limit("g", "888") is False


async def test_rate_limit_disabled_when_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(settings, "onebot_rate_limit_per_minute", 0)
    adapter = OneBotAdapter()

    for _ in range(5):
        assert await adapter._check_inbound_rate_limit("g", "888") is True

    assert redis.counters == {}


async def test_rate_limit_skipped_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "_redis", None)
    monkeypatch.setattr(settings, "onebot_rate_limit_per_minute", 1)
    adapter = OneBotAdapter()

    assert await adapter._check_inbound_rate_limit("g", "888") is True


async def test_flooded_message_dropped_before_llm_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超限消息必须在 judge/reply LLM 之前被丢弃：generate 恰好执行一次"""
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(settings, "onebot_rate_limit_per_minute", 1)
    monkeypatch.setattr(onebot_module, "_resolve_character_id", lambda is_group, group_id: UUID(int=1))
    monkeypatch.setattr(onebot_module, "_get_llm_globals", lambda: ("llm", "prompts", None))

    calls = {"generate": 0}

    class StubMessageService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def handle_user_message(self, **kwargs: Any) -> dict[str, Any]:
            calls["generate"] += 1
            return {"content": "generated reply"}

    monkeypatch.setattr(onebot_module, "MessageService", StubMessageService)
    monkeypatch.setattr(onebot_module, "db", StubDB())

    adapter = RecordingSendAdapter()
    ws = cast(WebSocket, object())
    base = {"post_type": "message", "message_type": "private", "user_id": 42, "self_id": 999}

    await adapter.handle_event({**base, "message_id": "m1", "raw_message": "hi"}, ws)
    await adapter.handle_event({**base, "message_id": "m2", "raw_message": "flood"}, ws)

    assert calls["generate"] == 1
    assert adapter.sent == ["generated reply"]


# === R5-L9：群上下文环顺序与角色回复入环 ===


async def test_group_context_returns_oldest_to_newest() -> None:
    redis = FakeRedis()
    adapter = OneBotAdapter()

    for sender, text in [("a", "1"), ("b", "2"), ("c", "3")]:
        await adapter._record_group_message(redis, "888", sender, text)

    ctx = await adapter._read_group_context(redis, "888")

    assert [(e["sender"], e["text"]) for e in ctx] == [("a", "1"), ("b", "2"), ("c", "3")]


async def test_bot_group_reply_recorded_after_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(onebot_module, "_resolve_character_id", lambda is_group, group_id: UUID(int=7))

    class StubRepo:
        def __init__(self, session: object) -> None:
            pass

        async def get_by_id(self, cid: UUID) -> SimpleNamespace:
            return SimpleNamespace(name="小雪")

    monkeypatch.setattr("src.db.repositories.CharacterRepository", StubRepo)
    monkeypatch.setattr(onebot_module, "db", StubDB())

    adapter = RecordingSendAdapter()
    await adapter.send_message(cast(WebSocket, object()), "group", None, "888", "大家好，我是小雪")

    ctx = await adapter._read_group_context(redis, "888")
    assert len(ctx) == 1
    assert ctx[0]["sender"] == "小雪"
    assert "小雪" in ctx[0]["text"]


async def test_private_reply_not_recorded_into_group_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(onebot_module, "_resolve_character_id", lambda is_group, group_id: UUID(int=7))

    class StubRepo:
        def __init__(self, session: object) -> None:
            pass

        async def get_by_id(self, cid: UUID) -> SimpleNamespace:
            return SimpleNamespace(name="小雪")

    monkeypatch.setattr("src.db.repositories.CharacterRepository", StubRepo)
    monkeypatch.setattr(onebot_module, "db", StubDB())

    adapter = RecordingSendAdapter()
    await adapter.send_message(cast(WebSocket, object()), "private", 42, None, "悄悄说一句")

    assert adapter.sent == ["悄悄说一句"]
    assert redis.lists == {}


async def test_triggering_user_message_not_duplicated_with_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """触发消息读前写入一次 + 回复发送后写入一次：环内恰好各一条且时序正确"""
    redis = FakeRedis()
    monkeypatch.setattr(runtime, "_redis", redis)
    monkeypatch.setattr(onebot_module, "_resolve_character_id", lambda is_group, group_id: UUID(int=7))

    class StubRepo:
        def __init__(self, session: object) -> None:
            pass

        async def get_by_id(self, cid: UUID) -> SimpleNamespace:
            return SimpleNamespace(name="小雪")

    monkeypatch.setattr("src.db.repositories.CharacterRepository", StubRepo)
    monkeypatch.setattr(onebot_module, "db", StubDB())

    adapter = RecordingSendAdapter()
    assert await adapter._read_group_context(redis, "888") == []

    await adapter._record_group_message(redis, "888", "alice", "在吗")
    await adapter.send_message(cast(WebSocket, object()), "group", None, "888", "在的")

    ctx = await adapter._read_group_context(redis, "888")
    assert [(e["sender"], e["text"]) for e in ctx] == [("alice", "在吗"), ("小雪", "在的")]


# === R5-L10：self_id 缺失一次性告警 ===


async def test_self_id_missing_warns_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
    reset_flags: None,
) -> None:
    monkeypatch.setattr(onebot_module, "_get_configured_self_id", lambda: None)
    adapter = OneBotAdapter()
    ws = cast(WebSocket, object())
    event = {"post_type": "message", "message_type": "private", "user_id": 1, "raw_message": ""}

    with capture_logs() as logs:
        await adapter.handle_event(dict(event), ws)
        await adapter.handle_event(dict(event), ws)

    assert len([e for e in logs if e.get("event") == "onebot_self_id_unavailable"]) == 1
