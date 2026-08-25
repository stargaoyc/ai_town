"""P-1 回归测试：分布式锁安全原语（唯一 token + compare-and-delete/expire）

验证目标（docs/design-improvement-and-fixes.md P-1）：
- release_lock 仅持有者可释放：token 不匹配时锁保留、返回 False
- renew_lock 仅持有者可续租：防止锁易主后续租他人的锁
- acquire_resource_locks 成功路径释放全部锁；部分失败路径不误删他人锁
- watch_locks 续租失败置位 lock_lost、续租成功保持未置位（round-3 review H10）
"""

import asyncio
from typing import Any, cast

from redis.asyncio import Redis

from src.core.locks import acquire_resource_locks, release_lock, renew_lock, watch_locks


class FakeLockRedis:
    """模拟 Redis 的 set/get/del/eval（Lua compare-and-delete/expire 语义）"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        if "del" in script:
            if self.store.get(key) == args[0]:
                del self.store[key]
                return 1
            return 0
        # expire 脚本
        return 1 if self.store.get(key) == args[0] else 0


async def test_release_lock_succeeds_for_owner() -> None:
    redis = FakeLockRedis()
    await redis.set("lock:a", "token-1", ex=30, nx=True)

    released = await release_lock(cast_redis(redis), "lock:a", "token-1")

    assert released is True
    assert "lock:a" not in redis.store


async def test_release_lock_rejected_for_non_owner() -> None:
    redis = FakeLockRedis()
    await redis.set("lock:a", "token-owner", ex=30, nx=True)

    released = await release_lock(cast_redis(redis), "lock:a", "token-other")

    assert released is False
    assert redis.store["lock:a"] == "token-owner"


async def test_renew_lock_rejected_after_ownership_change() -> None:
    redis = FakeLockRedis()
    await redis.set("lock:a", "token-old", ex=30, nx=True)
    # 锁过期后被他人获取
    redis.store["lock:a"] = "token-new"

    renewed = await renew_lock(cast_redis(redis), "lock:a", "token-old", 30)

    assert renewed is False
    assert redis.store["lock:a"] == "token-new"


async def test_acquire_resource_locks_releases_all_on_exit() -> None:
    redis = FakeLockRedis()

    async with acquire_resource_locks(cast_redis(redis), "11111111", "22222222") as acquired:
        assert acquired is True
        assert "char:resource:lock:11111111" in redis.store
        assert "char:resource:lock:22222222" in redis.store

    assert "char:resource:lock:11111111" not in redis.store
    assert "char:resource:lock:22222222" not in redis.store


async def test_acquire_resource_locks_partial_failure_keeps_others_lock() -> None:
    redis = FakeLockRedis()
    # 预先占用第二把锁（模拟他人持有）
    redis.store["char:resource:lock:22222222"] = "other-holder"

    async with acquire_resource_locks(cast_redis(redis), "11111111", "22222222") as acquired:
        assert acquired is False

    # 第一把锁已释放；他人持有的锁不受影响
    assert "char:resource:lock:11111111" not in redis.store
    assert redis.store["char:resource:lock:22222222"] == "other-holder"


async def test_watch_locks_sets_lock_lost_on_failed_renewal() -> None:
    redis = FakeLockRedis()
    await redis.set("lock:a", "token-old", ex=1, nx=True)
    # 锁过期后被他人获取
    redis.store["lock:a"] = "token-new"

    stop = asyncio.Event()
    lock_lost = asyncio.Event()
    watchdog = asyncio.create_task(watch_locks(cast_redis(redis), stop, {"lock:a": "token-old"}, 1, lock_lost))

    deadline = asyncio.get_running_loop().time() + 5
    while not lock_lost.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    stop.set()
    await watchdog

    assert lock_lost.is_set()


async def test_watch_locks_keeps_lock_lost_clear_while_owner_renews() -> None:
    redis = FakeLockRedis()
    await redis.set("lock:a", "token-owner", ex=1, nx=True)

    stop = asyncio.Event()
    lock_lost = asyncio.Event()
    watchdog = asyncio.create_task(watch_locks(cast_redis(redis), stop, {"lock:a": "token-owner"}, 1, lock_lost))
    await asyncio.sleep(0.5)  # 至少经历一轮成功续租（间隔 ttl/3）
    stop.set()
    await watchdog

    assert not lock_lost.is_set()
    assert redis.store["lock:a"] == "token-owner"


def cast_redis(fake: FakeLockRedis) -> Redis:
    return cast(Redis, fake)
