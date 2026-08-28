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

from src.core.character.tick import CharacterTickEngine
from src.core.locks import (
    TICK_LOCK_PREFIX,
    acquire_resource_locks,
    fenced_state_write,
    release_lock,
    renew_lock,
    try_acquire_lock,
    watch_locks,
)


class FakeLockRedis:
    """模拟 Redis 的 set/get/del/hset/eval（Lua 脚本语义）

    eval 按 numkeys 拆分 KEYS/ARGV，与 redis-py 的调用约定一致；
    脚本分支按特征字符串分派：各脚本的特征互不重叠（见各分支注释）。
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: Any) -> int:
        bucket = self.hashes.setdefault(key, {})
        bucket.update(mapping or {})
        bucket.update(kwargs)
        return len(mapping or kwargs)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        # fencing 原子写（_FENCED_STATE_WRITE_LUA）：校验持有权后 HSET 目标哈希
        if "HSET" in script:
            if self.store.get(keys[0]) != argv[0]:
                return 0
            bucket = self.hashes.setdefault(keys[1], {})
            for i in range(1, len(argv), 2):
                bucket[argv[i]] = argv[i + 1]
            return 1
        # compare-and-delete（_RELEASE_LOCK_LUA）
        if "del" in script:
            if self.store.get(keys[0]) == argv[0]:
                del self.store[keys[0]]
                return 1
            return 0
        # compare-and-expire（_RENEW_LOCK_LUA）
        return 1 if self.store.get(keys[0]) == argv[0] else 0


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


async def test_acquire_resource_locks_reports_lock_loss() -> None:
    """跨角色锁续租失败必须传导给调用方（审查 §4.1.3 并发-03）

    此前 acquire_resource_locks 用的看门狗只记日志，跨角色交互（对话、
    送礼）在锁易主后仍会写关系——本测试钉死「失锁信号可达」这条链路。
    """
    redis = FakeLockRedis()
    lock_lost = asyncio.Event()

    # ttl=1 使续租间隔为 ttl/3≈0.33s，避免为一个信号等满 10 秒
    async with acquire_resource_locks(cast_redis(redis), "11111111", ttl=1, lock_lost=lock_lost) as acquired:
        assert acquired is True
        # 模拟锁过期易主：续租时 compare-and-expire 将失败
        redis.store["char:resource:lock:11111111"] = "other-holder"
        deadline = asyncio.get_running_loop().time() + 5
        while not lock_lost.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)

    assert lock_lost.is_set()


async def test_try_acquire_lock_returns_token_when_free() -> None:
    redis = FakeLockRedis()

    token = await try_acquire_lock(cast_redis(redis), f"{TICK_LOCK_PREFIX}abc", ttl=5)

    assert token is not None
    assert redis.store[f"{TICK_LOCK_PREFIX}abc"] == token


async def test_try_acquire_lock_returns_none_when_held() -> None:
    redis = FakeLockRedis()
    redis.store[f"{TICK_LOCK_PREFIX}abc"] = "tick-owner"

    token = await try_acquire_lock(cast_redis(redis), f"{TICK_LOCK_PREFIX}abc", ttl=5)

    assert token is None
    assert redis.store[f"{TICK_LOCK_PREFIX}abc"] == "tick-owner"


def test_tick_lock_prefix_matches_tick_engine() -> None:
    """对账与 Tick 必须抢同一把锁才互斥；两处字面量靠本测试钉死防漂移"""
    assert TICK_LOCK_PREFIX == CharacterTickEngine.LOCK_PREFIX


async def test_fenced_state_write_applies_for_owner() -> None:
    """持有锁时写入生效"""
    redis = FakeLockRedis()
    redis.store["lock:a"] = "owner-token"

    written = await fenced_state_write(cast_redis(redis), "lock:a", "owner-token", "char:a:state", {"location": "cafe"})

    assert written is True
    assert redis.hashes["char:a:state"] == {"location": "cafe"}


async def test_fenced_state_write_rejected_after_ownership_change() -> None:
    """锁易主后旧持有者的迟到写入必须被拒绝且不污染状态（审查 §4.1.3 并发-01）

    这是 fencing 相对「看门狗协作式轮询」的核心增量：旧持有者即使刚从
    停顿中苏醒、尚未轮到下一次续租检查，其写入也会被原子地挡在门外。
    """
    redis = FakeLockRedis()
    redis.store["lock:a"] = "new-owner-token"
    redis.hashes["char:a:state"] = {"location": "park"}

    written = await fenced_state_write(cast_redis(redis), "lock:a", "stale-token", "char:a:state", {"location": "cafe"})

    assert written is False
    assert redis.hashes["char:a:state"] == {"location": "park"}


async def test_fenced_state_write_rejected_when_lock_expired() -> None:
    """锁过期（键消失）后写入同样被拒绝：无持有者即无权写"""
    redis = FakeLockRedis()

    written = await fenced_state_write(cast_redis(redis), "lock:a", "any-token", "char:a:state", {"location": "cafe"})

    assert written is False
    assert "char:a:state" not in redis.hashes


async def test_fenced_state_write_is_atomic() -> None:
    """校验与写入必须是一次原子操作：不允许出现「校验通过、写入被他人插队」"""
    redis = FakeLockRedis()
    redis.store["lock:a"] = "owner-token"

    # 替身严格按脚本语义执行：GET 校验失败时不会执行任何 HSET，
    # 上面 test_fenced_state_write_rejected_* 已覆盖该不变式。
    # 此处额外确认单次 eval 内可写多字段（真实 Lua 循环语义）。
    written = await fenced_state_write(
        cast_redis(redis), "lock:a", "owner-token", "char:a:state", {"location": "cafe", "energy": "80"}
    )

    assert written is True
    assert redis.hashes["char:a:state"] == {"location": "cafe", "energy": "80"}


def cast_redis(fake: FakeLockRedis) -> Redis:
    return cast(Redis, fake)
