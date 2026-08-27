"""WorldEngine._execute_tick 编排集成测试（round-7 P2-8）

验证：演化器链执行、world:state 写入、事件差分去重、快照持久化。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.world.engine import WorldEngine
from src.core.world.evolutions.base import WorldEvolution
from src.db.models import WorldEvent, WorldSnapshot


class StubEvolution(WorldEvolution):
    """可控演化器：返回固定状态更新"""

    def __init__(self, name: str, updates: dict[str, Any]) -> None:
        self.name = name
        self._updates = updates

    async def evolve(self, redis: Any, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        return self._updates


@asynccontextmanager
async def _ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


class TestWorldEngineExecuteTick:
    async def test_execute_tick_increments_id_and_writes_redis(
        self, it_redis: AsyncRedis, it_session: AsyncSession, monkeypatch: MonkeyPatch
    ) -> None:
        engine = WorldEngine(redis=it_redis)
        engine.evolutions = [
            StubEvolution("time", {"time": {"world_time": "2026-08-27T10:00:00"}}),
            StubEvolution("weather", {"weather": "sunny"}),
        ]
        import src.core.world.engine as engine_mod

        monkeypatch.setattr(engine_mod, "db", SimpleNamespace(session=lambda: _ctx(it_session)))

        engine.tick_id += 1
        await engine._execute_tick()

        state = await it_redis.hgetall("world:state")
        assert state.get("tick_id") == "1"
        assert state.get("weather") == "sunny"

    async def test_evolution_failure_does_not_block_chain(
        self, it_redis: AsyncRedis, it_session: AsyncSession, monkeypatch: MonkeyPatch
    ) -> None:
        engine = WorldEngine(redis=it_redis)

        class FailingEvolution(WorldEvolution):
            name = "failing"

            async def evolve(self, redis: Any, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("boom")

        engine.evolutions = [
            FailingEvolution(),
            StubEvolution("weather", {"weather": "rainy"}),
        ]
        import src.core.world.engine as engine_mod

        monkeypatch.setattr(engine_mod, "db", SimpleNamespace(session=lambda: _ctx(it_session)))

        engine.tick_id += 1
        await engine._execute_tick()

        state = await it_redis.hgetall("world:state")
        assert state.get("weather") == "rainy"
        assert state.get("tick_id") == "1"

    async def test_world_events_only_on_changed_state(
        self, it_redis: AsyncRedis, it_session: AsyncSession, monkeypatch: MonkeyPatch
    ) -> None:
        """状态变化时写入事件，无变化时不写入（去重基线生效）"""
        engine = WorldEngine(redis=it_redis)
        engine.evolutions = [StubEvolution("time", {"time": {"world_time": "2026-08-27T10:00:00"}})]
        import src.core.world.engine as engine_mod

        monkeypatch.setattr(engine_mod, "db", SimpleNamespace(session=lambda: _ctx(it_session)))
        from src.config import settings as _s

        monkeypatch.setattr(_s, "world_snapshot_interval", 1)

        # 第一次 tick：状态变化 → 写入事件（time 维度从空基线变为有值）
        engine.tick_id += 1
        await engine._execute_tick()
        await it_session.flush()
        events1 = list((await it_session.execute(select(WorldEvent))).scalars())
        assert len(events1) >= 1

        # 第二次 tick：状态无变化（返回相同 world_time）→ 不写入新事件
        engine.tick_id += 1
        await engine._execute_tick()
        await it_session.flush()
        events2 = list((await it_session.execute(select(WorldEvent))).scalars())
        assert len(events2) == len(events1)  # 去重基线生效，无新增事件

    async def test_snapshot_written_at_interval(
        self, it_redis: AsyncRedis, it_session: AsyncSession, monkeypatch: MonkeyPatch
    ) -> None:
        engine = WorldEngine(redis=it_redis)
        engine.evolutions = [StubEvolution("weather", {"weather": "sunny"})]
        import src.core.world.engine as engine_mod

        monkeypatch.setattr(engine_mod, "db", SimpleNamespace(session=lambda: _ctx(it_session)))
        from src.config import settings as _s

        monkeypatch.setattr(_s, "world_full_snapshot_interval", 1)
        # 确保 snapshot 写入不受 snapshot_interval 干扰
        monkeypatch.setattr(_s, "world_snapshot_interval", 999)

        engine.tick_id += 1
        await engine._execute_tick()
        await it_session.flush()
        snaps = list((await it_session.execute(select(WorldSnapshot))).scalars())
        assert len(snaps) == 1
        assert snaps[0].tick_id == 1
        assert snaps[0].weather == "sunny"
