"""Character Tick 接入点链路集成测试（T4 残余补齐）

覆盖 Tick 主流程新接入的两个 handler：
- _propagate_gossip：好友显著经历 -> 听者第二手记忆（传闻传播）
- _handle_group_activity：群活动叙事生成 + 全员共同经历记忆
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.core.character.tick import CharacterTickEngine
from src.db.models import Character, MemoryEpisode, Relation
from src.llm.client import LLMClient
from src.llm.prompts import PromptTemplates


class StubLLM:
    async def chat(self, prompt: str, model: str | None = None) -> str:
        return '{"narrative": "三人在咖啡店聊起了小镇趣事"}'

    async def embed(self, content: str) -> list[float]:
        return [0.0]


class StubPrompts:
    def render(self, name: str, **kwargs: Any) -> str:
        return f"[{name}]"


@asynccontextmanager
async def _ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


def _engine(session: AsyncSession) -> CharacterTickEngine:
    """以最小依赖构造引擎实例：gossip/群活动路径仅用 llm/prompts 两个属性"""
    engine = CharacterTickEngine.__new__(CharacterTickEngine)
    engine.llm = cast(LLMClient, StubLLM())
    engine.prompts = cast(PromptTemplates, StubPrompts())
    return engine


@pytest_asyncio.fixture
async def social_pair(it_session: AsyncSession) -> tuple[Character, Character]:
    listener = Character(id=uuid7(), name="小听", is_active=True)
    friend = Character(id=uuid7(), name="小传", is_active=True)
    it_session.add_all([listener, friend])
    await it_session.flush()
    it_session.add(
        Relation(
            character_id=listener.id,
            target_id=friend.id,
            strength=50,
            relationship_type="friend",
        )
    )
    await it_session.flush()
    return listener, friend


class TestPropagateGossipDispatch:
    async def test_tick_dispatch_creates_second_hand_memory(
        self, it_session: AsyncSession, social_pair: tuple[Character, Character]
    ) -> None:
        listener, friend = social_pair
        it_session.add(
            MemoryEpisode(
                character_id=friend.id,
                content="在冒险中找到了失落的宝藏",
                importance=8,
                timestamp=datetime.now(UTC),
                source_type="action",
            )
        )
        await it_session.flush()

        engine = _engine(it_session)
        await engine._propagate_gossip(listener.id, session_factory=lambda: _ctx(it_session))

        rows = list(
            (await it_session.execute(select(MemoryEpisode).where(MemoryEpisode.character_id == listener.id))).scalars()
        )
        gossip_rows = [r for r in rows if r.source_type == "gossip"]
        assert len(gossip_rows) == 1
        assert gossip_rows[0].content.startswith("听小传说：")


class TestGroupActivityDispatch:
    async def test_handler_writes_memories_for_all_participants(self, it_session: AsyncSession) -> None:
        initiator = Character(id=uuid7(), name="小艾", is_active=True)
        b = Character(id=uuid7(), name="小博", is_active=True)
        c = Character(id=uuid7(), name="小陈", is_active=True)
        it_session.add_all([initiator, b, c])
        await it_session.flush()

        engine = _engine(it_session)
        nearby = [
            {"id": str(b.id), "name": b.name},
            {"id": str(c.id), "name": c.name},
        ]
        context: dict[str, Any] = {
            "character": initiator,
            "state": {"location": "咖啡店"},
            "nearby_characters": nearby,
        }
        decision = cast(Any, SimpleNamespace(params={}))

        result = await engine._handle_group_activity(
            initiator.id,
            decision,
            context,
            session_factory=lambda: _ctx(it_session),
        )

        assert result is not None
        assert "小艾" in result and "咖啡店" in result

        rows = list(
            (
                await it_session.execute(select(MemoryEpisode).where(MemoryEpisode.action_id == "group_activity"))
            ).scalars()
        )
        # 发起者 + 2 名在场者各一条共同经历记忆
        assert len(rows) == 3
        for row in rows:
            assert row.importance == 6
            others = {p["id"] for p in nearby} | {str(initiator.id)}
            others.discard(str(row.character_id))
            assert set(map(str, row.related_characters)) == others

        # 两两关系加固：3 人共 6 条有向关系，各 +2（默认 20 起步 -> 22）
        relations = list((await it_session.execute(select(Relation))).scalars())
        assert len(relations) == 6
        assert all(r.strength == 22 for r in relations)

    async def test_insufficient_nearby_returns_none(self, it_session: AsyncSession) -> None:
        engine = _engine(it_session)
        context: dict[str, Any] = {
            "character": SimpleNamespace(name="独行者"),
            "state": {"location": "home"},
            "nearby_characters": [],
        }
        decision = cast(Any, SimpleNamespace(params={}))
        result = await engine._handle_group_activity(
            uuid7(),
            decision,
            context,
            session_factory=lambda: _ctx(it_session),
        )
        assert result is None
