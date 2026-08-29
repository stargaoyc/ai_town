"""社交会话服务单测（交互-02 方案：会话实体 + 三层终止防无休止）

覆盖：
- 会话创建/读取/持久化
- 轮数硬上限终止（advance_turn 达到 chat_max_turns）
- 软结束检测（LLM 回复含结束意图）
- 超时死亡（超过 chat_idle_ticks 个世界 Tick 无回应）
- max_turns 从配置读取
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.memory.social_conversation import SocialConversationService


class FakeRedis:
    """最小 Redis 替身：hset/hgetall/expire/scan"""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.store.setdefault(key, {}).update(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.store.get(key, {})

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def scan(self, cursor: int = 0, match: str = "*", count: int = 10) -> tuple[int, list[str]]:
        import re

        pattern = re.compile(match.replace("*", ".*"))
        keys = [k for k in self.store if pattern.match(k)]
        return 0, keys


def _make_service(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> SocialConversationService:
    import src.memory.social_conversation as module

    monkeypatch.setattr(module, "get_redis", lambda: fake_redis)
    return SocialConversationService()


class TestConversationLifecycle:
    async def test_create_and_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv = await svc.create_or_get("char-a", "char-b", "cafe", topic="天气")
        assert conv.status == "pending"
        assert conv.char_a == "char-a"
        assert conv.char_b == "char-b"

        loaded = await svc.get(conv.id)
        assert loaded is not None
        assert loaded.topic == "天气"

    async def test_reuses_active_conversation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv1 = await svc.create_or_get("char-a", "char-b", "cafe")
        # 再次发起应复用同一会话（非新建）
        conv2 = await svc.create_or_get("char-a", "char-b", "cafe")
        assert conv1.id == conv2.id

    async def test_creates_new_after_ended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv1 = await svc.create_or_get("char-a", "char-b", "cafe")
        await svc.end_with_reason(conv1, "soft_end")
        conv2 = await svc.create_or_get("char-a", "char-b", "cafe")
        assert conv1.id != conv2.id


class TestTermination:
    async def test_hard_limit_ends_conversation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)
        monkeypatch.setattr(settings, "chat_max_turns", 3, raising=False)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        for _ in range(3):
            conv, ended = await svc.advance_turn(conv)

        assert ended is True
        assert conv.status == "ended"
        assert conv.ended_reason == "hard_limit"

    async def test_max_turns_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)
        monkeypatch.setattr(settings, "chat_max_turns", 8, raising=False)
        assert svc.max_turns == 8

    async def test_soft_end_detects_intent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        ended = await svc.soft_end_if_intended(conv, "那我先走了，下次再说！")
        assert ended is True
        assert conv.status == "ended"
        assert conv.ended_reason == "soft_end"

    async def test_soft_end_ignores_normal_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        ended = await svc.soft_end_if_intended(conv, "今天天气真不错，我们去散步吧")
        assert ended is False
        assert conv.status != "ended"

    async def test_timeout_ends_conversation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)
        monkeypatch.setattr(settings, "world_tick_seconds", 30, raising=False)
        monkeypatch.setattr(settings, "chat_idle_ticks", 2, raising=False)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        # 模拟 65 秒无回应（> 30×2=60s）
        conv.last_turn_at = conv.last_turn_at - 65
        ended = await svc.check_timeout(conv)
        assert ended is True
        assert conv.status == "ended"
        assert conv.ended_reason == "timeout"

    async def test_no_timeout_within_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)
        monkeypatch.setattr(settings, "world_tick_seconds", 30, raising=False)
        monkeypatch.setattr(settings, "chat_idle_ticks", 2, raising=False)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        conv.last_turn_at = conv.last_turn_at - 30  # 30s < 60s
        ended = await svc.check_timeout(conv)
        assert ended is False


class TestPendingFor:
    async def test_pending_when_other_spoke_last(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        conv.status = "active"
        conv.last_speaker = "char-a"
        await svc._save(conv)

        pending = await svc.pending_for("char-b")
        assert len(pending) == 1
        assert pending[0].id == conv.id

    async def test_no_pending_when_self_spoke_last(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        conv.status = "active"
        conv.last_speaker = "char-b"
        await svc._save(conv)

        # 本方最后发言则等待对方回应，不重复触发
        pending = await svc.pending_for("char-b")
        assert pending == []

    async def test_no_pending_for_ended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        svc = _make_service(monkeypatch, fake)

        conv = await svc.create_or_get("char-a", "char-b", "cafe")
        conv.status = "ended"
        conv.last_speaker = "char-a"
        await svc._save(conv)

        pending = await svc.pending_for("char-b")
        assert pending == []
