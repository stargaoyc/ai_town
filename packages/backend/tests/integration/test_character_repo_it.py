"""CharacterRepository 集成测试 - PG 镜像层行为验证

覆盖文档「测试覆盖缺口」中的 P0 项：
- 状态更新字段真实落库（P0-1 工具 delta 进 PG 的下游保障）
- get_characters_by_location 场景感知查询（多智能体交互依赖）
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, CharacterState
from src.db.repositories.character_repo import CharacterRepository

CharacterFactory = Callable[..., Awaitable[tuple[Character, CharacterState]]]


@pytest_asyncio.fixture
async def character_factory(it_session: AsyncSession) -> Callable[..., Awaitable[tuple[Character, CharacterState]]]:
    """创建带初始状态的活跃角色，返回工厂函数"""

    async def _create(
        name: str,
        location: str | None = "home",
        is_active: bool = True,
        **state_fields: Any,
    ) -> tuple[Character, CharacterState]:
        char = Character(id=uuid7(), name=name, is_active=is_active)
        state = CharacterState(character_id=char.id, location=location, **state_fields)
        it_session.add_all([char, state])
        await it_session.flush()
        return char, state

    return _create


class TestUpdateState:
    async def test_update_state_persists_fields(
        self, it_session: AsyncSession, character_factory: CharacterFactory
    ) -> None:
        char, _ = await character_factory("小艾", location="home")

        repo = CharacterRepository(it_session)
        await repo.update_state(char.id, location="cafe", money=320)

        fetched = await repo.get_by_id(char.id)
        assert fetched is not None
        state = await it_session.get(CharacterState, char.id)
        assert state is not None
        assert state.location == "cafe"
        assert state.money == 320

    async def test_update_empty_fields_is_noop(
        self, it_session: AsyncSession, character_factory: CharacterFactory
    ) -> None:
        char, state = await character_factory("小北", location="park")
        before_version = state.version

        repo = CharacterRepository(it_session)
        await repo.update_state(char.id)

        refreshed = await it_session.get(CharacterState, char.id)
        assert refreshed is not None
        assert refreshed.location == "park"
        assert refreshed.version == before_version


class TestUpdateStateCas:
    async def test_version_mismatch_rejects_write(
        self, it_session: AsyncSession, character_factory: CharacterFactory
    ) -> None:
        char, state = await character_factory("小艾", location="home")

        repo = CharacterRepository(it_session)
        applied = await repo.update_state(char.id, expected_version=state.version + 999, location="cafe")

        refreshed = await it_session.get(CharacterState, char.id)
        assert applied is False
        assert refreshed is not None
        assert refreshed.location == "home"
        assert refreshed.version == state.version

    async def test_matching_version_writes_and_increments(
        self, it_session: AsyncSession, character_factory: CharacterFactory
    ) -> None:
        char, state = await character_factory("小博", location="home")
        before_version = state.version

        repo = CharacterRepository(it_session)
        applied = await repo.update_state(char.id, expected_version=before_version, location="cafe")

        refreshed = await it_session.get(CharacterState, char.id)
        assert applied is True
        assert refreshed is not None
        assert refreshed.location == "cafe"
        assert refreshed.version == before_version + 1

    async def test_cas_recovers_from_concurrent_bump(
        self, it_session: AsyncSession, character_factory: CharacterFactory
    ) -> None:
        """模拟 Tick 先行写入抬升版本后，API 侧 CAS 重读重试仍能落库"""
        char, _ = await character_factory("小陈", location="home")

        repo = CharacterRepository(it_session)
        stale_version = await repo.get_state_version(char.id)
        assert stale_version is not None
        # 并发写：版本被 Tick 抬升
        await repo.update_state(char.id, location="park")
        bumped = await repo.get_state_version(char.id)
        assert bumped is not None and bumped > stale_version

        # 若用旧版本会冲突，但 update_state_cas 重读最新版本后成功
        applied = await repo.update_state_cas(char.id, location="cafe")
        refreshed = await it_session.get(CharacterState, char.id)
        assert applied is True
        assert refreshed is not None
        assert refreshed.location == "cafe"

    async def test_missing_state_row_falls_back_to_unconditional(self, it_session: AsyncSession) -> None:
        """无状态行时无可比版本，退化为无条件写入；仍无行可写则如实返回 False"""
        char = Character(id=uuid7(), name="无状态行角色")
        it_session.add(char)
        await it_session.flush()

        repo = CharacterRepository(it_session)
        version = await repo.get_state_version(char.id)
        assert version is None

        applied = await repo.update_state_cas(char.id, location="cafe")
        assert applied is False


class TestGetCharactersByLocation:
    async def test_returns_only_active_chars_in_location(
        self, it_session: AsyncSession, character_factory: CharacterFactory
    ) -> None:
        alice, _ = await character_factory("艾莉丝", location="cafe")
        _, _ = await character_factory("小博", location="cafe")
        await character_factory("老陈", location="home")  # 不同场景
        await character_factory(" inactive", location="cafe", is_active=False)  # 不活跃

        repo = CharacterRepository(it_session)
        found = await repo.get_characters_by_location("cafe", exclude_id=None)

        ids = {c.id for c, _ in found}
        assert alice.id in ids
        assert len(ids) == 2
        assert all(s.location == "cafe" for _, s in found)
        # JOIN 保证返回 (Character, CharacterState) 成对
        pair = next(p for p in found if p[0].id == alice.id)
        assert pair[1].character_id == alice.id

    async def test_exclude_id_removes_self(self, it_session: AsyncSession, character_factory: CharacterFactory) -> None:
        alice, _ = await character_factory("艾莉丝", location="library")
        bob, _ = await character_factory("小博", location="library")

        repo = CharacterRepository(it_session)
        found = await repo.get_characters_by_location("library", exclude_id=alice.id)

        ids = {c.id for c, _ in found}
        assert ids == {bob.id}


class TestGetCharacterWithState:
    async def test_missing_character_returns_none(self, it_session: AsyncSession) -> None:
        repo = CharacterRepository(it_session)
        assert await repo.get_character_with_state(UUID(int=0)) is None

    async def test_returns_joined_pair(self, it_session: AsyncSession, character_factory: CharacterFactory) -> None:
        char, state = await character_factory("小艾", location="shrine", stamina=42)

        repo = CharacterRepository(it_session)
        result = await repo.get_character_with_state(char.id)

        assert result is not None
        got_char, got_state = result
        assert got_char.name == "小艾"
        assert got_state.stamina == 42
