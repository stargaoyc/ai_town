"""EventQueue / 回复去重语义单元测试（fake Redis，纯逻辑）

覆盖 round-3 审查缺陷：
- H3：remove() = XACK + XDEL 的移除语义（对比仅 XACK 的 ack()）、
  enqueue/dead_letter 的 maxlen 透传、recover_drain 成功后不再重投
- H4：回复槽位 claim/release 时序契约（生成 → 认领 → 发送；失败释放可重试）
- H5：自消息早退、群聊问候层概率闸门
"""

from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import WebSocket
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.onebot import OneBotAdapter, _reply_dedup_key
from src.config import settings
from src.llm import LLMClient, PromptTemplates
from src.messaging.event_queue import DLQ_STREAM, MAX_DELIVERIES, STREAM, EventQueue

# R6-M3：去重键含 self_id 与角色 ID，与 PRIVATE_EVENT 的字段保持一致
_DEDUP_SELF = "99999"
_DEDUP_CHARACTER = UUID(int=1)
_DEDUP_KEY = _reply_dedup_key(_DEDUP_SELF, _DEDUP_CHARACTER, "m1")


class FakeStreamRedis:
    """最小 Redis Streams 语义模拟：仅实现 EventQueue 用到的命令"""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[str, set[str]] = {}
        # PEL：entry_id -> fields（已投递未确认）
        self.pel: dict[str, dict[str, str]] = {}
        self.deliveries: dict[str, int] = {}
        self.xadd_calls: list[dict[str, Any]] = []
        self.xack_calls: list[tuple[str, str, str]] = []
        self.xdel_calls: list[tuple[str, str]] = []
        self._id_counter = 0

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"{self._id_counter}-1"

    async def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = False,
    ) -> str:
        self.xadd_calls.append({"key": key, "fields": fields, "maxlen": maxlen, "approximate": approximate})
        entry_id = self._next_id()
        self.streams.setdefault(key, []).append((entry_id, dict(fields)))
        return entry_id

    async def xgroup_create(self, key: str, group: str, id: str = "0", mkstream: bool = False) -> None:
        from redis.exceptions import ResponseError

        groups_for_key = self.groups.setdefault(key, set())
        if group in groups_for_key:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        groups_for_key.add(group)

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        results: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for key, start in streams.items():
            entries: list[tuple[str, dict[str, str]]]
            if start == ">":
                entries = []
                budget = count if count is not None else 50
                for entry_id, fields in self.streams.get(key, []):
                    if len(entries) >= budget:
                        break
                    if entry_id in self.pel:
                        continue
                    self.pel[entry_id] = dict(fields)
                    self.deliveries[entry_id] = self.deliveries.get(entry_id, 0) + 1
                    entries.append((entry_id, dict(fields)))
            else:
                entries = [(eid, dict(fields)) for eid, fields in self.pel.items()]
            results.append((key, entries))
        return results

    async def xpending_range(
        self,
        key: str,
        group: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> list[dict[str, Any]]:
        if min in self.deliveries:
            return [{"message_id": min, "times_delivered": self.deliveries[min]}]
        return []

    async def xack(self, key: str, group: str, *ids: str) -> int:
        acked = 0
        for entry_id in ids:
            self.xack_calls.append((key, group, entry_id))
            if entry_id in self.pel:
                del self.pel[entry_id]
                acked += 1
        return acked

    async def xdel(self, key: str, *ids: str) -> int:
        id_set = set(ids)
        stream = self.streams.get(key, [])
        survivors: list[tuple[str, dict[str, str]]] = []
        deleted = 0
        for entry_id, fields in stream:
            if entry_id in id_set:
                deleted += 1
                self.xdel_calls.append((key, entry_id))
            else:
                survivors.append((entry_id, fields))
        self.streams[key] = survivors
        return deleted


def make_queue() -> tuple[EventQueue, FakeStreamRedis]:
    fake = FakeStreamRedis()
    return EventQueue(cast(Redis, fake)), fake


async def test_remove_issues_xack_and_xdel() -> None:
    queue, fake = make_queue()
    entry_id = await queue.enqueue({"post_type": "message", "message_id": "m1"})
    assert entry_id in [e for e, _ in fake.streams[STREAM]]

    await queue.remove(entry_id)
    assert (STREAM, "processor", entry_id) in fake.xack_calls
    assert (STREAM, entry_id) in fake.xdel_calls
    assert fake.streams[STREAM] == []


async def test_ack_only_does_not_delete_entry() -> None:
    """对照语义：ack 只清 PEL，条目仍留在流中——正是 round-3 H3 的缺陷形态"""
    queue, fake = make_queue()
    entry_id = await queue.enqueue({"post_type": "message"})
    fake.pel[entry_id] = {"event": "{}"}

    await queue.ack(entry_id)
    assert fake.xdel_calls == []
    assert len(fake.streams[STREAM]) == 1


async def test_enqueue_passes_maxlen_from_settings() -> None:
    queue, fake = make_queue()

    await queue.enqueue({"post_type": "message"})

    call = fake.xadd_calls[-1]
    assert call["key"] == STREAM
    assert call["maxlen"] == settings.onebot_stream_maxlen
    assert call["approximate"] is True


async def test_dead_letter_copies_to_dlq_and_removes_source() -> None:
    queue, fake = make_queue()
    entry_id = await queue.enqueue({"post_type": "message"})
    fields = {"event": '{"post_type":"message"}'}

    await queue.dead_letter(entry_id, fields, "poison")

    dlq_call = fake.xadd_calls[-1]
    assert dlq_call["key"] == DLQ_STREAM
    assert dlq_call["fields"]["source_id"] == entry_id
    assert dlq_call["maxlen"] == settings.onebot_stream_maxlen
    assert dlq_call["approximate"] is True
    assert fake.streams[STREAM] == []
    assert (STREAM, "processor", entry_id) in fake.xack_calls
    assert (STREAM, entry_id) in fake.xdel_calls


async def test_recover_drain_success_removes_entries_and_skips_redelivery() -> None:
    """核心回归：成功处理后条目必须离开流，第二轮 recover_drain 不得再投递"""
    queue, fake = make_queue()
    await queue.enqueue({"post_type": "message", "message_id": "a"})
    await queue.enqueue({"post_type": "message", "message_id": "b"})
    seen: list[dict[str, Any]] = []

    async def handler(event: dict[str, Any]) -> None:
        seen.append(event)

    first = await queue.recover_drain(handler)
    second = await queue.recover_drain(handler)

    assert first == 2
    assert len(seen) == 2
    assert {e["message_id"] for e in seen} == {"a", "b"}
    assert fake.streams[STREAM] == []
    assert fake.pel == {}
    assert second == 0


async def test_recover_drain_handler_failure_keeps_entry_pending() -> None:
    queue, fake = make_queue()
    await queue.enqueue({"post_type": "message", "message_id": "a"})

    async def failing_handler(event: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    processed = await queue.recover_drain(failing_handler)

    assert processed == 1
    assert len(fake.streams[STREAM]) == 1
    assert len(fake.pel) == 1


async def test_recover_drain_poison_goes_to_dlq_and_removed() -> None:
    queue, fake = make_queue()
    entry_id = await queue.enqueue({"post_type": "message", "message_id": "bad"})
    # 模拟已重投超过上限的崩溃残留
    fake.pel[entry_id] = {"event": '{"post_type":"message"}'}
    fake.deliveries[entry_id] = MAX_DELIVERIES + 1

    async def handler(event: dict[str, Any]) -> None:
        raise AssertionError("poison must not reach handler")

    processed = await queue.recover_drain(handler)

    assert processed == 1
    assert len(fake.streams[DLQ_STREAM]) == 1
    assert fake.streams[STREAM] == []
    assert entry_id not in fake.pel


class FakeDedupRedis:
    """SETNX/DELETE 最小模拟：ops 记录操作序列用于时序断言"""

    def __init__(self, ops: list[str]) -> None:
        self.keys: dict[str, str] = {}
        self.ops = ops

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        self.ops.append(f"set:{key}")
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.keys:
                del self.keys[key]
                deleted += 1
                self.ops.append(f"del:{key}")
        return deleted

    async def eval(self, script: str, numkeys: int, key: str, window_seconds: int) -> int:
        """R5-M7 入站限流的 INCR+EXPIRE 原子脚本最小模拟（仅固定窗口计数语义）"""
        count = int(self.keys.get(key, "0")) + 1
        self.keys[key] = str(count)
        return count


PRIVATE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "user_id": 12345,
    "self_id": 99999,
    "message_id": "m1",
    "raw_message": "hello",
}


class ReplyHarness:
    """私聊回复链路桩：共享操作序列，用于断言 认领→发送 的时序"""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.redis = FakeDedupRedis(self.order)
        self.generate_count = 0
        self.fail_send = False
        self.sent_messages: list[str] = []
        self.adapter: OneBotAdapter | None = None

    @property
    def bot(self) -> OneBotAdapter:
        assert self.adapter is not None
        return self.adapter


def _cast_ws() -> WebSocket:
    return cast(WebSocket, object())


@pytest.fixture
def reply_harness(monkeypatch: pytest.MonkeyPatch) -> ReplyHarness:
    """打通私聊路径依赖：角色解析 / LLM 全局 / DB 会话 / MessageService / 发送全部为桩"""
    import src.adapters.onebot as onebot_module
    import src.runtime as runtime

    h = ReplyHarness()
    monkeypatch.setattr(runtime, "_redis", h.redis)

    character_id = UUID(int=1)
    monkeypatch.setattr(onebot_module, "_resolve_character_id", lambda is_group, group_id: character_id)
    monkeypatch.setattr(onebot_module, "_get_llm_globals", lambda: ("llm", "prompts", None))

    class StubMessageService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def handle_user_message(self, **kwargs: Any) -> dict[str, Any]:
            h.order.append("generate")
            h.generate_count += 1
            return {"content": "generated reply"}

    monkeypatch.setattr(onebot_module, "MessageService", StubMessageService)

    class StubSessionCtx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    class StubDB:
        def session(self) -> StubSessionCtx:
            return StubSessionCtx()

    monkeypatch.setattr(onebot_module, "db", StubDB())

    class SendRecordingAdapter(OneBotAdapter):
        async def send_message(
            self,
            onebot_ws: WebSocket,
            event_type: str,
            user_id: str | int | None,
            group_id: str | int | None,
            message: str,
        ) -> None:
            if h.fail_send:
                raise RuntimeError("send failed")
            h.order.append("send")
            h.sent_messages.append(message)

    h.adapter = SendRecordingAdapter()
    return h


async def test_reply_claim_happens_between_generation_and_send(reply_harness: ReplyHarness) -> None:
    """round-3 H4 核心时序：生成 → SETNX 认领 → 发送"""
    h = reply_harness

    await h.bot.handle_event(dict(PRIVATE_EVENT), _cast_ws())

    assert h.sent_messages == ["generated reply"]
    assert h.order == ["generate", f"set:{_DEDUP_KEY}", "send"]


async def test_send_failure_releases_reply_slot(reply_harness: ReplyHarness) -> None:
    """round-3 H4：发送失败后槽位被释放，同一条消息可重新认领"""
    h = reply_harness
    h.fail_send = True

    await h.bot.handle_event(dict(PRIVATE_EVENT), _cast_ws())
    assert await h.bot._claim_reply_slot(_DEDUP_SELF, _DEDUP_CHARACTER, "m1") is True

    assert h.sent_messages == []
    # 生成后认领；发送失败即释放；随后重放可再次认领（最后的 set 即重试成功）
    assert h.order == ["generate", f"set:{_DEDUP_KEY}", f"del:{_DEDUP_KEY}", f"set:{_DEDUP_KEY}"]
    assert _DEDUP_KEY in h.redis.keys


async def test_duplicate_reply_skipped_when_slot_taken(reply_harness: ReplyHarness) -> None:
    """槽位被占用时不重复发送（另一条处理路径已回复）"""
    h = reply_harness

    await h.bot.handle_event(dict(PRIVATE_EVENT), _cast_ws())
    await h.bot.handle_event(dict(PRIVATE_EVENT), _cast_ws())

    assert h.sent_messages == ["generated reply"]
    assert h.generate_count == 2
    assert h.order.count("send") == 1


async def test_self_message_skipped_early(reply_harness: ReplyHarness) -> None:
    """round-3 H5：user_id == self_id 的消息在最前面被丢弃，不触发任何下游动作"""
    h = reply_harness
    event = dict(PRIVATE_EVENT)
    event["user_id"] = 99999

    await h.bot.handle_event(event, _cast_ws())

    assert h.sent_messages == []
    assert h.order == []


async def test_claim_reply_slot_setnx_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.runtime._redis", FakeDedupRedis([]))
    adapter = OneBotAdapter()

    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is True
    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is False


async def test_release_reply_slot_allows_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """release-on-failure 契约：释放后同一条消息可重新认领并重试发送"""
    ops: list[str] = []
    monkeypatch.setattr("src.runtime._redis", FakeDedupRedis(ops))
    adapter = OneBotAdapter()
    key = _reply_dedup_key("111", UUID(int=1), "m1")

    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is True
    await adapter._release_reply_slot("111", UUID(int=1), "m1")
    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is True
    assert ops == [f"set:{key}", f"del:{key}", f"set:{key}"]


async def test_claim_without_redis_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 不可用时视为无去重层（与原行为一致），直接放行"""
    monkeypatch.setattr("src.runtime._redis", None)
    adapter = OneBotAdapter()

    assert await adapter._claim_reply_slot("111", UUID(int=1), "m1") is True


async def test_greeting_layer_probabilistic_name_hit_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """round-3 H5：问候命中走概率闸门，名字命中保持确定性"""
    from src.messaging.service import GROUP_REPLY_GREETING_PROBABILITY, MessageService

    svc = MessageService(
        session=cast(AsyncSession, None),
        llm=cast(LLMClient, object()),
        prompts=cast(PromptTemplates, object()),
    )
    character_id = UUID(int=1)

    monkeypatch.setattr("src.messaging.service._probability_roll", lambda p: False)
    should, reason = await svc.should_reply_in_group(
        character_id=character_id,
        character_name="小雪",
        message="大家好",
        sender_user_id="qq_123",
    )
    assert should is False
    assert reason.startswith("greeting_skip_probability")

    monkeypatch.setattr("src.messaging.service._probability_roll", lambda p: True)
    should, reason = await svc.should_reply_in_group(
        character_id=character_id,
        character_name="小雪",
        message="早上好呀",
        sender_user_id="qq_123",
    )
    assert should is True
    assert reason.startswith("greeting:")

    # 名字命中不受概率闸门影响（显式点名理应回应）
    monkeypatch.setattr("src.messaging.service._probability_roll", lambda p: False)
    should, reason = await svc.should_reply_in_group(
        character_id=character_id,
        character_name="小雪",
        message="小雪在吗",
        sender_user_id="qq_123",
    )
    assert should is True
    assert reason == "name_mentioned"

    assert GROUP_REPLY_GREETING_PROBABILITY == pytest.approx(0.9)
