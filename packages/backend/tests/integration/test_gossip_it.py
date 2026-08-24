"""GossipService 集成测试 - 传闻传播端到端（群体动力学）

覆盖：
- 好友的高重要性 action 记忆 -> 听者第二手记忆（importance 减半、来源标记、related_characters 指向好友）
- 幂等：同一好友同一窗口内不重复传播
- 关系强度门槛 / source_type='gossip' 不二次传播 / 非活跃好友排除
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode, Relation
from src.db.repositories.memory_repo import MemoryRepository
from src.llm import LLMClient
from src.memory.episode_service import EpisodeService
from src.memory.gossip_service import GossipService

GossipFactory = Callable[..., Awaitable[tuple[Character, Character]]]


@pytest_asyncio.fixture
async def gossip_session_factory(it_session: AsyncSession) -> GossipFactory:
    """创建 (听者, 好友) 角色对与关系边的工厂"""

    async def _create(listener_name: str, friend_name: str, strength: int = 50) -> tuple[Character, Character]:
        listener = Character(id=uuid7(), name=listener_name, is_active=True)
        friend = Character(id=uuid7(), name=friend_name, is_active=True)
        it_session.add_all([listener, friend])
        await it_session.flush()
        it_session.add(
            Relation(
                character_id=listener.id,
                target_id=friend.id,
                strength=strength,
                relationship_type="friend",
            )
        )
        await it_session.flush()
        return listener, friend

    return _create


async def _add_episode(
    session: AsyncSession,
    character_id: UUID,
    content: str,
    importance: int,
    source_type: str = "action",
) -> MemoryEpisode:
    episode = MemoryEpisode(
        character_id=character_id,
        content=content,
        importance=importance,
        timestamp=datetime.now(UTC),
        source_type=source_type,
    )
    session.add(episode)
    await session.flush()
    return episode


def _service(session: AsyncSession) -> GossipService:
    # gossip 路径不触发 LLM 评分（不传 character_name），llm 依赖可安全置空
    return GossipService(session, EpisodeService(cast(LLMClient, None), MemoryRepository(session)))


class TestPropagateFromFriends:
    async def test_notable_friend_experience_becomes_second_hand_memory(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        listener, friend = await gossip_session_factory("小听", "小传", strength=50)
        await _add_episode(it_session, friend.id, "在冒险中找到了失落的宝藏", importance=8)

        propagated = await _service(it_session).propagate_from_friends(listener.id)
        assert propagated == 1

        rows = list(
            (await it_session.execute(select(MemoryEpisode).where(MemoryEpisode.character_id == listener.id))).scalars()
        )
        gossip_rows = [e for e in rows if e.source_type == "gossip"]
        assert len(gossip_rows) == 1
        row = gossip_rows[0]
        assert row.content.startswith("听小传说：")
        assert "失落的宝藏" in row.content
        # importance 减半：8 -> 4；来源指向好友
        assert row.importance == 4
        assert list(row.related_characters) == [friend.id]

    async def test_idempotent_within_window(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        listener, friend = await gossip_session_factory("小听", "小传")
        await _add_episode(it_session, friend.id, "在酒馆赢下了掰手腕比赛", importance=8)

        service = _service(it_session)
        assert await service.propagate_from_friends(listener.id) == 1
        # 同窗口第二次运行：该好友不再重复传播
        assert await service.propagate_from_friends(listener.id) == 0

    async def test_weak_relation_excluded(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        listener, friend = await gossip_session_factory("小听", "路人", strength=5)
        await _add_episode(it_session, friend.id, "完成了惊险的攀岩", importance=9)

        assert await _service(it_session).propagate_from_friends(listener.id) == 0

    async def test_gossip_source_never_repropagates(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        """传闻不再二次传播：A 的八卦不能被 B 当亲身经历继续扩散"""
        listener, friend = await gossip_session_factory("小听", "小传")
        await _add_episode(it_session, friend.id, "听说隔壁街区开了新店", importance=9, source_type="gossip")

        assert await _service(it_session).propagate_from_friends(listener.id) == 0

    async def test_below_importance_threshold_ignored(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        listener, friend = await gossip_session_factory("小听", "小传")
        await _add_episode(it_session, friend.id, "睡了个午觉", importance=3)

        assert await _service(it_session).propagate_from_friends(listener.id) == 0

    async def test_inactive_friend_excluded(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        listener, friend = await gossip_session_factory("小听", "隐居者")
        friend.is_active = False
        await it_session.flush()
        await _add_episode(it_session, friend.id, "离群索居前留下了信件", importance=9)

        assert await _service(it_session).propagate_from_friends(listener.id) == 0

    async def test_stale_source_outside_window_ignored(
        self, it_session: AsyncSession, gossip_session_factory: GossipFactory
    ) -> None:
        listener, friend = await gossip_session_factory("小听", "小传")
        stale = MemoryEpisode(
            character_id=friend.id,
            content="三天前的冒险经历",
            importance=9,
            timestamp=datetime.now(UTC) - timedelta(hours=72),
            source_type="action",
        )
        it_session.add(stale)
        await it_session.flush()

        assert await _service(it_session).propagate_from_friends(listener.id) == 0
