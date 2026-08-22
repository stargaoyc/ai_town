"""P0-4 回归测试：角色 Tick 并发化 + Semaphore 热更新

验证目标（docs/design-improvement-and-fixes.md P0-4）：
- tick_all_active 并发执行并返回逐角色结果（成功/异常），供主循环统计与退避
- character_max_concurrent 配置热更新后信号量按新容量重建
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from src.actions import ActionRegistry
from src.config import settings
from src.core.character.tick import CharacterTickEngine
from src.llm import LLMClient, PromptTemplates

_CHAR_A = UUID("01964000-0000-7000-8000-000000000001")
_CHAR_B = UUID("01964000-0000-7000-8000-000000000002")


class FakeRedis:
    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        return True

    async def delete(self, key: str) -> int:
        return 1


@pytest.fixture(autouse=True)
def _reset_semaphore_state() -> Iterator[None]:
    CharacterTickEngine.SEMAPHORE = None
    CharacterTickEngine.SEMAPHORE_LIMIT = 0
    yield
    CharacterTickEngine.SEMAPHORE = None
    CharacterTickEngine.SEMAPHORE_LIMIT = 0


def _make_engine() -> CharacterTickEngine:
    return CharacterTickEngine(
        redis=cast(Redis, FakeRedis()),
        registry=ActionRegistry(),
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
    )


def test_ensure_semaphore_creates_with_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "character_max_concurrent", 5)
    CharacterTickEngine._ensure_semaphore()
    assert CharacterTickEngine.SEMAPHORE is not None
    assert CharacterTickEngine.SEMAPHORE_LIMIT == 5


def test_ensure_semaphore_rebuilds_on_hot_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "character_max_concurrent", 3)
    CharacterTickEngine._ensure_semaphore()
    old = CharacterTickEngine.SEMAPHORE
    assert old is not None

    monkeypatch.setattr(settings, "character_max_concurrent", 8)
    CharacterTickEngine._ensure_semaphore()

    assert CharacterTickEngine.SEMAPHORE is not old
    assert CharacterTickEngine.SEMAPHORE_LIMIT == 8


def test_ensure_semaphore_keeps_instance_when_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "character_max_concurrent", 4)
    CharacterTickEngine._ensure_semaphore()
    first = CharacterTickEngine.SEMAPHORE
    CharacterTickEngine._ensure_semaphore()
    assert CharacterTickEngine.SEMAPHORE is first


async def test_tick_all_active_returns_per_character_results() -> None:
    engine = _make_engine()
    calls: list[UUID] = []

    async def fake_tick(character_id: UUID) -> None:
        calls.append(character_id)
        if character_id == _CHAR_B:
            raise RuntimeError("boom")

    engine.tick_character = fake_tick  # type: ignore[method-assign]

    characters = [SimpleNamespace(id=_CHAR_A), SimpleNamespace(id=_CHAR_B)]

    outcomes = await engine.tick_all_active(characters)  # type: ignore[arg-type]

    assert sorted(calls) == sorted([_CHAR_A, _CHAR_B])
    assert len(outcomes) == 2
    by_id = {char.id: exc for char, exc in outcomes}
    assert by_id[_CHAR_A] is None
    assert isinstance(by_id[_CHAR_B], RuntimeError)


async def test_tick_all_active_runs_concurrently() -> None:
    engine = _make_engine()
    overlap = {"current": 0, "max": 0}

    async def slow_tick(character_id: UUID) -> None:
        overlap["current"] += 1
        overlap["max"] = max(overlap["max"], overlap["current"])
        await asyncio.sleep(0.05)
        overlap["current"] -= 1

    engine.tick_character = slow_tick  # type: ignore[method-assign]

    characters = [SimpleNamespace(id=uuid4()) for _ in range(4)]
    await engine.tick_all_active(characters)  # type: ignore[arg-type]

    assert overlap["max"] > 1
