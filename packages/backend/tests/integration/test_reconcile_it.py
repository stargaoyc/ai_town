"""状态对账集成测试（roadmap #24）- 真实 PG + Redis 上的漂移检测与修复

覆盖：
- Redis 键缺失 → 从 PG 回灌（pg_to_redis）
- 数值字段漂移 → 以 Redis 为准修正 PG（redis_to_pg）
- 位置/库存字段漂移修复
- 无漂移时零写入
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.core.reconcile import collect_drift, run_reconciliation
from src.core.state_codec import encode_state_mapping
from src.db.models import Character, CharacterState


@asynccontextmanager
async def _session_ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """把受管 session 包装为上下文管理器，供 run_reconciliation 注入"""
    yield session


async def _make_character(it_session: AsyncSession, **state_fields: object) -> CharacterState:
    char = Character(id=uuid7(), name="对账测试角色")
    state = CharacterState(character_id=char.id, **state_fields)
    it_session.add_all([char, state])
    await it_session.flush()
    return state


class TestCollectDrift:
    def test_no_drift_when_fields_match(self, it_session: AsyncSession) -> None:
        state = CharacterState(character_id=uuid7(), location="cafe", stamina=80, money=100)
        redis_hash = encode_state_mapping({"location": "cafe", "stamina": 80, "money": 100})
        assert collect_drift(state, redis_hash) == []

    def test_numeric_drift_detected(self, it_session: AsyncSession) -> None:
        state = CharacterState(character_id=uuid7(), stamina=80, money=100)
        redis_hash = encode_state_mapping({"stamina": 65, "money": 100})
        assert collect_drift(state, redis_hash) == ["stamina"]

    def test_location_drift_detected(self, it_session: AsyncSession) -> None:
        state = CharacterState(character_id=uuid7(), location="cafe")
        redis_hash = encode_state_mapping({"location": "park"})
        assert collect_drift(state, redis_hash) == ["location"]

    def test_redis_missing_field_is_not_drift(self, it_session: AsyncSession) -> None:
        """Redis 缺字段 = 信息不足（None 被过滤），不判漂移"""
        state = CharacterState(character_id=uuid7(), mood="happy", stamina=80)
        redis_hash = encode_state_mapping({"mood": "happy"})  # 无 stamina
        assert collect_drift(state, redis_hash) == []


class TestRunReconciliation:
    async def test_missing_redis_key_rehydrated_from_pg(self, it_session: AsyncSession, it_redis: Redis) -> None:
        state = await _make_character(it_session, location="library", stamina=70, money=250)

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["missing_keys"] == 1
        assert stats["repairs"] >= 1
        restored = await it_redis.hgetall(f"char:{state.character_id}:state")
        assert restored["location"] == "library"
        assert restored["stamina"] == "70"
        assert restored["money"] == "250"

    async def test_value_drift_repairs_pg_from_redis(self, it_session: AsyncSession, it_redis: Redis) -> None:
        state = await _make_character(it_session, location="cafe", stamina=80, money=100)
        key = f"char:{state.character_id}:state"
        # Redis 是真相源：写入与 PG 不同的值
        await it_redis.hset(key, mapping=encode_state_mapping({"stamina": 55, "money": 320, "location": "cafe"}))  # type: ignore[arg-type]

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["value_drift"] == 1
        # PG 被修正为 Redis 的值
        await it_session.refresh(state)
        assert state.stamina == 55
        assert state.money == 320

    async def test_no_drift_no_writes(self, it_session: AsyncSession, it_redis: Redis) -> None:
        state = await _make_character(it_session, location="park", stamina=90, money=500)
        key = f"char:{state.character_id}:state"
        await it_redis.hset(
            key,
            mapping=encode_state_mapping(  # type: ignore[arg-type]
                {"location": "park", "stamina": 90, "money": 500, "inventory": {}}
            ),
        )

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["repairs"] == 0
        await it_session.refresh(state)
        assert state.stamina == 90

    async def test_inventory_drift_repairs_pg(self, it_session: AsyncSession, it_redis: Redis) -> None:
        state = await _make_character(it_session, inventory={"coffee": 1})
        key = f"char:{state.character_id}:state"
        await it_redis.hset(key, mapping=encode_state_mapping({"inventory": {"coffee": 1, "book": 2}}))  # type: ignore[arg-type]

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["value_drift"] == 1
        await it_session.refresh(state)
        assert state.inventory == {"coffee": 1, "book": 2}


class TestVersionAwareArbitration:
    async def test_pg_advanced_flips_repair_direction(self, it_session: AsyncSession, it_redis: Redis) -> None:
        """PG 在对账基线后发生写入（API 崩溃窗口）-> 方向翻转，Redis 被 PG 新值修复"""
        state = await _make_character(it_session, location="cafe", stamina=80)
        key = f"char:{state.character_id}:state"
        await it_redis.hset(key, mapping=encode_state_mapping({"location": "cafe", "stamina": 80}))  # type: ignore[arg-type]

        # 第一轮：建立对账基线（无漂移）
        await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        # 模拟 API 双写崩溃窗口：PG 写入新值（version+1）但 Redis 未跟上
        state.location = "park"
        state.stamina = 60
        state.version += 1
        await it_session.flush()

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["value_drift"] == 1
        # 方向翻转：Redis 被 PG 新值修复，而非 PG 被陈旧 Redis 覆盖
        redis_now = await it_redis.hgetall(key)
        assert redis_now["location"] == "park"
        assert redis_now["stamina"] == "60"
        await it_session.refresh(state)
        assert state.stamina == 60

    async def test_baseline_unchanged_keeps_redis_authority(self, it_session: AsyncSession, it_redis: Redis) -> None:
        """基线未变（绕过双写的路径产生漂移）-> 维持 Redis 为准修正 PG"""
        state = await _make_character(it_session, location="cafe", stamina=80)
        key = f"char:{state.character_id}:state"
        await it_redis.hset(key, mapping=encode_state_mapping({"location": "cafe", "stamina": 80}))  # type: ignore[arg-type]
        await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        # 绕过双写直接改 PG（version 不变）
        from sqlalchemy import update as sa_update

        await it_session.execute(
            sa_update(CharacterState).where(CharacterState.character_id == state.character_id).values(stamina=10)
        )
        await it_session.flush()

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))
        assert stats["value_drift"] == 1

        await it_session.refresh(state)
        assert state.stamina == 80  # 被 Redis 权威值修复


class TestPgToRedisRepairTickLock:
    """round-5 review L7：pg_to_redis 修复写 Redis 前必须持有角色 Tick 锁"""

    async def _setup_pg_advanced_drift(self, it_session: AsyncSession, it_redis: Redis) -> CharacterState:
        """建立「Tick 刚写完 PG（version 前进）但 Redis 未跟上」的对账场景并落基线"""
        state = await _make_character(it_session, location="cafe", stamina=80)
        key = f"char:{state.character_id}:state"
        await it_redis.hset(key, mapping=encode_state_mapping({"location": "cafe", "stamina": 80}))  # type: ignore[arg-type]
        await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        state.location = "park"
        state.version += 1
        await it_session.flush()
        return state

    async def test_pg_to_redis_repair_skipped_when_tick_lock_busy(
        self, it_session: AsyncSession, it_redis: Redis
    ) -> None:
        state = await self._setup_pg_advanced_drift(it_session, it_redis)
        key = f"char:{state.character_id}:state"
        # Tick 正在运行：占用角色互斥锁
        await it_redis.set(f"char:tick:lock:{state.character_id}", "tick-owner", ex=30)

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["value_drift"] == 0
        redis_now = await it_redis.hgetall(key)
        assert redis_now["location"] == "cafe", "Tick 持锁期间修复不得写入 Redis"
        # 基线未动，下轮 Tick 结束后以新快照重新仲裁
        assert await it_redis.get(f"char:{state.character_id}:rec_ver") is not None

    async def test_pg_to_redis_repair_proceeds_and_releases_lock_when_free(
        self, it_session: AsyncSession, it_redis: Redis
    ) -> None:
        state = await self._setup_pg_advanced_drift(it_session, it_redis)
        key = f"char:{state.character_id}:state"

        stats = await run_reconciliation(it_redis, lambda: _session_ctx(it_session))

        assert stats["value_drift"] == 1
        redis_now = await it_redis.hgetall(key)
        assert redis_now["location"] == "park"
        # 修复完成后锁必须释放，不能阻塞下一轮 Tick 启动
        assert not await it_redis.exists(f"char:tick:lock:{state.character_id}")
