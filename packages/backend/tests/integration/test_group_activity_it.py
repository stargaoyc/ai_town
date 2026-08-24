"""群活动持久化集成测试 - 共同经历记忆 + 两两关系加固（B5）"""

from __future__ import annotations

from typing import Any, cast

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode, Relation
from src.db.repositories.memory_repo import MemoryRepository
from src.llm import LLMClient
from src.memory.episode_service import EpisodeService
from src.memory.group_activity_service import GroupActivityService, parse_group_narrative


@pytest_asyncio.fixture
async def trio(it_session: AsyncSession) -> list[Character]:
    chars = [Character(id=uuid7(), name=f"群聚角色{i}", is_active=True) for i in range(3)]
    it_session.add_all(chars)
    await it_session.flush()
    return chars


def _service(it_session: AsyncSession) -> GroupActivityService:
    # gossip/群活动路径不触发 LLM 评分（不传 character_name），llm 依赖可安全置空
    return GroupActivityService(
        it_session,
        EpisodeService(cast(LLMClient, None), MemoryRepository(it_session)),
    )


class TestParseGroupNarrative:
    def test_valid_json(self) -> None:
        assert parse_group_narrative('{"narrative": "三人聊得很开心"}') == "三人聊得很开心"

    def test_code_fence_stripped(self) -> None:
        raw = '```json\n{"narrative": "围坐闲聊"}\n```'
        assert parse_group_narrative(raw) == "围坐闲聊"

    def test_invalid_returns_none(self) -> None:
        assert parse_group_narrative("不是 JSON") is None


class TestPersistGroupActivity:
    async def test_memory_per_participant_with_cross_references(
        self, it_session: AsyncSession, trio: list[Character]
    ) -> None:
        participants = [{"id": str(c.id), "name": c.name} for c in trio]

        written = await _service(it_session).persist(
            initiator_id=trio[0].id,
            participants=participants,
            location="咖啡店",
            narrative="三人拼了一桌下午茶，聊起了小镇的趣事",
        )

        assert written == 3
        rows = list((await it_session.execute(select(MemoryEpisode))).scalars())
        assert len(rows) == 3
        for row in rows:
            assert row.source_type == "action"
            assert row.action_id == "group_activity"
            assert row.importance == 6
            others = {c.id for c in trio if c.id != row.character_id}
            assert set(row.related_characters) == others

    async def test_relations_boosted_both_directions_capped(
        self, it_session: AsyncSession, trio: list[Character]
    ) -> None:
        # 预置一对关系：一条接近上限（99），验证钳制
        it_session.add(
            Relation(
                character_id=trio[0].id,
                target_id=trio[1].id,
                strength=99,
                relationship_type="friend",
            )
        )
        await it_session.flush()

        participants = [{"id": str(c.id), "name": c.name} for c in trio]
        kwargs: dict[str, Any] = {
            "initiator_id": trio[0].id,
            "participants": participants,
            "location": "广场",
            "narrative": "一起看了街头表演",
        }
        await _service(it_session).persist(**kwargs)

        relations = list((await it_session.execute(select(Relation))).scalars())
        by_pair = {(r.character_id, r.target_id): r.strength for r in relations}
        # 三人两两共 6 条有向关系（0->1 原本存在走更新，其余新建默认 20+2）
        assert len(relations) == 6
        assert by_pair[(trio[0].id, trio[1].id)] == 100  # 99+2 钳到 100
        assert by_pair[(trio[1].id, trio[0].id)] == 22  # 新建默认 20 + 2

    async def test_duplicate_content_skipped_gracefully(self, it_session: AsyncSession, trio: list[Character]) -> None:
        """同一叙事重复持久化时，去重命中不抛异常（写入数减少）"""
        participants = [{"id": str(c.id), "name": c.name} for c in trio]
        service = _service(it_session)
        kwargs: dict[str, Any] = {
            "initiator_id": trio[0].id,
            "participants": participants,
            "location": "公园",
            "narrative": "一起喂了鸽子",
        }
        first = await service.persist(**kwargs)
        second = await service.persist(**kwargs)

        assert first == 3
        assert second == 0
