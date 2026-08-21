"""P0-3 回归测试：启动时 PG→Redis 状态回灌

验证 rehydrate_states 的编排逻辑：
- 缺失的 char:{id}:state 从 PG 回灌，已存在的跳过
- 缺失的 world:state 从最新快照回灌，已存在的跳过
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from pytest import MonkeyPatch
from redis.asyncio import Redis

from src.core import rehydration

_CHAR_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, Any]] = {}
        self.hset_calls: list[tuple[str, dict[str, Any]]] = []

    async def exists(self, key: str) -> int:
        return 1 if key in self.hashes else 0

    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.hset_calls.append((key, mapping or {}))


class FakeSession:
    pass


class FakeCtx:
    async def __aenter__(self) -> FakeSession:
        return FakeSession()

    async def __aexit__(self, *args: Any) -> bool:
        return False


class FakeSessionFactory:
    def __call__(self) -> FakeCtx:
        return FakeCtx()


def _fake_char_state(**overrides: Any) -> SimpleNamespace:
    base = {
        "character_id": _CHAR_ID,
        "location": "home",
        "stamina": 80,
        "satiety": 60,
        "mood": "calm",
        "money": 500,
        "inventory": {"coffee": 1},
        "current_action": None,
        "phone_battery": 75,
        "social_energy": 60,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_repos(monkeypatch: MonkeyPatch, char_states: list[Any], snapshot: Any) -> None:
    class FakeCharRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def get_all_states(self) -> list[Any]:
            return char_states

    class FakeSnapshotRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def get_latest(self) -> Any:
            return snapshot

    monkeypatch.setattr(rehydration, "CharacterRepository", FakeCharRepo)
    monkeypatch.setattr(rehydration, "WorldSnapshotRepository", FakeSnapshotRepo)
    monkeypatch.setattr(rehydration, "db", SimpleNamespace(session=FakeSessionFactory()))


async def test_rehydrate_restores_missing_character_state(monkeypatch: MonkeyPatch) -> None:
    redis = FakeRedis()
    _patch_repos(monkeypatch, [_fake_char_state()], None)

    await rehydration.rehydrate_states(cast(Redis, redis))

    assert len(redis.hset_calls) == 1
    key, mapping = redis.hset_calls[0]
    assert key == f"char:{_CHAR_ID}:state"
    assert mapping["money"] == "500"
    assert mapping["inventory"] == '{"coffee": 1}'
    assert "current_action" not in mapping  # None 值不落 Redis


async def test_rehydrate_skips_existing_character_state(monkeypatch: MonkeyPatch) -> None:
    redis = FakeRedis()
    redis.hashes[f"char:{_CHAR_ID}:state"] = {"money": "999"}
    _patch_repos(monkeypatch, [_fake_char_state()], None)

    await rehydration.rehydrate_states(cast(Redis, redis))

    assert redis.hset_calls == []


async def test_rehydrate_restores_world_state_from_snapshot(monkeypatch: MonkeyPatch) -> None:
    redis = FakeRedis()
    snapshot = SimpleNamespace(
        tick_id=1234,
        world_time=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        weather="rainy",
    )
    _patch_repos(monkeypatch, [], snapshot)

    await rehydration.rehydrate_states(cast(Redis, redis))

    assert len(redis.hset_calls) == 1
    key, mapping = redis.hset_calls[0]
    assert key == "world:state"
    assert mapping["tick_id"] == "1234"
    assert mapping["weather"] == "rainy"
    assert mapping["world_time"] == "2026-08-20T12:00:00+00:00"


async def test_rehydrate_skips_existing_world_state(monkeypatch: MonkeyPatch) -> None:
    redis = FakeRedis()
    redis.hashes["world:state"] = {"tick_id": "999"}
    snapshot = SimpleNamespace(tick_id=1234, world_time=None, weather=None)
    _patch_repos(monkeypatch, [], snapshot)

    await rehydration.rehydrate_states(cast(Redis, redis))

    assert redis.hset_calls == []
