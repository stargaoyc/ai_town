"""分布式锁集成测试 - 真实 Redis 上的 Lua compare-and-delete/续租/看门狗

覆盖 P-1 锁修复的回归保护：
- SET NX + 唯一 token 基础互斥
- release_lock 只删除自己持有的锁（compare-and-delete）
- renew_lock 只续自己的租（compare-and-expire）
- acquire_resource_locks：多角色排序加锁、失败释放已获取锁、看门狗续租
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from redis.asyncio import Redis

from src.core.locks import (
    _RESOURCE_LOCK_PREFIX,
    acquire_resource_locks,
    release_lock,
    renew_lock,
)


class TestBasicMutex:
    async def test_set_nx_mutual_exclusion(self, it_redis: Redis) -> None:
        key = f"{_RESOURCE_LOCK_PREFIX}{uuid4()}"
        first = await it_redis.set(key, "token-a", ex=30, nx=True)
        second = await it_redis.set(key, "token-b", ex=30, nx=True)

        assert first is True
        assert second is None or second is False
        assert await it_redis.get(key) == "token-a"

    async def test_release_lock_requires_matching_token(self, it_redis: Redis) -> None:
        key = f"{_RESOURCE_LOCK_PREFIX}{uuid4()}"
        await it_redis.set(key, "token-owner", ex=30, nx=True)

        assert await release_lock(it_redis, key, "wrong-token") is False
        assert await it_redis.get(key) == "token-owner"  # 锁未被误删

        assert await release_lock(it_redis, key, "token-owner") is True
        assert await it_redis.get(key) is None

    async def test_renew_lock_requires_matching_token(self, it_redis: Redis) -> None:
        key = f"{_RESOURCE_LOCK_PREFIX}{uuid4()}"
        await it_redis.set(key, "token-owner", ex=30, nx=True)

        assert await renew_lock(it_redis, key, "intruder", ttl=30) is False

        ttl_before = await it_redis.ttl(key)
        assert await renew_lock(it_redis, key, "token-owner", ttl=100) is True
        assert await it_redis.ttl(key) > ttl_before


class TestAcquireResourceLocks:
    async def test_acquires_all_and_releases_on_exit(self, it_redis: Redis) -> None:
        id_a, id_b = uuid4(), uuid4()

        async with acquire_resource_locks(it_redis, id_a, id_b, ttl=30) as acquired:
            assert acquired is True
            keys = sorted(f"{_RESOURCE_LOCK_PREFIX}{x}" for x in (id_a, id_b))
            for k in keys:
                assert await it_redis.exists(k)

        # 退出后全部释放
        keys = [f"{_RESOURCE_LOCK_PREFIX}{x}" for x in (id_a, id_b)]
        for k in keys:
            assert await it_redis.get(k) is None

    async def test_conflict_yields_false_and_first_holder_keeps_lock(self, it_redis: Redis) -> None:
        id_a = uuid4()
        key = f"{_RESOURCE_LOCK_PREFIX}{id_a}"
        await it_redis.set(key, "holder", ex=30, nx=True)

        async with acquire_resource_locks(it_redis, id_a, ttl=30) as acquired:
            assert acquired is False

        assert await it_redis.get(key) == "holder"

    async def test_watchdog_renews_before_ttl_expiry(self, it_redis: Redis) -> None:
        """TTL=2s 时看门狗应在过期前完成续租（间隔 ttl/3 ≈ 0.67s）"""
        id_a = uuid4()
        key = f"{_RESOURCE_LOCK_PREFIX}{id_a}"

        async with acquire_resource_locks(it_redis, id_a, ttl=2) as acquired:
            assert acquired is True
            await asyncio.sleep(3.5)
            # 若无看门狗，2s 后锁已过期消失；有看门狗则仍持有且 TTL 已重置
            remaining = await it_redis.ttl(key)
            assert remaining > 1

    async def test_duplicate_ids_locked_once(self, it_redis: Redis) -> None:
        id_a = uuid4()

        async with acquire_resource_locks(it_redis, id_a, id_a, ttl=30) as acquired:
            assert acquired is True
