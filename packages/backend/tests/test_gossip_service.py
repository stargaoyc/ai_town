"""GossipService 单元测试 - 传闻第二手记忆的构造语义（群体动力学）

纯逻辑验证（不依赖 DB）：
- importance 减半且下限为 2（传闻保真度递减）
- 内容模板拼接取源记忆原文，超长截断
- 源角色缺失时安全跳过
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import MemoryEpisode
from src.memory.gossip_service import GossipService


class StubSession:
    def __init__(self, name: str | None) -> None:
        self._name = name

    async def scalar(self, stmt: Any) -> str | None:
        return self._name


class StubEpisodeService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, dict[str, Any]]] = []

    async def create_episode(self, character_id: UUID, content: str, **kwargs: Any) -> MemoryEpisode:
        self.calls.append((character_id, content, kwargs))
        return MemoryEpisode(character_id=character_id, content=content)


def _service(name: str | None, sink: StubEpisodeService) -> GossipService:
    # 测试替身注入生产签名：仅依赖 scalar/create_episode 协议（测试规范 §5.2）
    return GossipService(cast(AsyncSession, StubSession(name)), cast(Any, sink))


def _source(importance: int, content: str = "在冒险中找到了失落的宝藏") -> MemoryEpisode:
    return MemoryEpisode(
        character_id=uuid4(),
        content=content,
        importance=importance,
        timestamp=datetime.now(UTC),
    )


class TestCreateSecondHand:
    async def test_importance_halved_with_floor(self) -> None:
        sink = StubEpisodeService()
        svc = _service("小艾", sink)
        listener = uuid4()

        await svc._create_second_hand(listener, _source(importance=9), importance=max(2, 9 // 2))

        assert len(sink.calls) == 1
        _, _, kwargs = sink.calls[0]
        assert kwargs["importance"] == 4

    async def test_low_importance_floors_at_two(self) -> None:
        sink = StubEpisodeService()
        svc = _service("小艾", sink)
        listener = uuid4()

        await svc._create_second_hand(listener, _source(importance=3), importance=max(2, 3 // 2))

        _, _, kwargs = sink.calls[0]
        assert kwargs["importance"] == 2

    async def test_content_prefix_and_truncation(self) -> None:
        sink = StubEpisodeService()
        svc = _service("小艾", sink)
        long_content = "非常" * 200
        listener = uuid4()

        await svc._create_second_hand(listener, _source(importance=8, content=long_content), importance=4)

        _, content, kwargs = sink.calls[0]
        assert content.startswith("听小艾说：")
        assert len(content) <= len("听小艾说：") + 120
        assert kwargs["source_type"] == "gossip"
        assert len(kwargs["related_characters"]) == 1

    async def test_missing_friend_name_skips(self) -> None:
        sink = StubEpisodeService()
        svc = _service(None, sink)

        result = await svc._create_second_hand(uuid4(), _source(importance=8), importance=4)

        assert result is None
        assert sink.calls == []
