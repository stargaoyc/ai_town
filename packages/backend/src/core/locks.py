"""分布式锁 - 跨角色资源原子化

解决跨角色操作（如 chat_with、give_gift）的并发竞争问题。
当角色 A 与角色 B 交互时，需要同时锁定双方状态，防止：
- A 和 B 同时 tick 并互相 chat_with，导致关系更新竞争
- give_gift 时双方库存/关系同时变更的数据不一致

设计要点：
- 按 ID 排序获取锁，防止死锁（A→B 和 B→A 同时发生时不会互相等待）
- TTL 自动过期，防止死锁（持有者崩溃时锁自动释放）
- 获取失败时立即返回，不阻塞等待（fail-fast）
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from uuid import UUID, uuid4

from redis.asyncio import Redis
from structlog import get_logger

logger = get_logger(__name__)

# 锁前缀与默认 TTL
_RESOURCE_LOCK_PREFIX = "char:resource:lock:"
_DEFAULT_TTL = 30  # 秒

# compare-and-delete：仅当 value 与持有者 token 一致时才删除，
# 防止锁过期被他人获取后误删他人的锁
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""

# compare-and-expire：仅持有者可续租
_RENEW_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
else
  return 0
end
"""


async def release_lock(redis: Redis, key: str, token: str) -> bool:
    """安全释放锁（Lua compare-and-delete）

    Args:
        redis: Redis 客户端
        key: 锁键
        token: 获取锁时写入的唯一令牌

    Returns:
        是否由本持有者成功释放；False 表示锁已易主或不存在
    """
    result = await redis.eval(_RELEASE_LOCK_LUA, 1, key, token)
    return int(result) == 1


async def renew_lock(redis: Redis, key: str, token: str, ttl: int) -> bool:
    """安全续租锁（Lua compare-and-expire）

    Returns:
        是否续租成功；False 表示锁已易主或不存在（持有者应立即停止受锁保护的工作）
    """
    result = await redis.eval(_RENEW_LOCK_LUA, 1, key, token, ttl)
    return int(result) == 1


@asynccontextmanager
async def acquire_resource_locks(
    redis: Redis,
    *character_ids: UUID | str,
    ttl: int = _DEFAULT_TTL,
) -> AsyncIterator[bool]:
    """跨角色资源锁上下文管理器

    按 ID 字符串排序后依次获取锁，防止死锁。
    任一锁获取失败则释放已获取的锁并 yield False。

    用法：
        async with acquire_resource_locks(redis, char_a_id, char_b_id) as acquired:
            if not acquired:
                # 锁获取失败，跳过本次操作
                return
            # 执行跨角色原子操作...

    Args:
        redis: Redis 客户端
        *character_ids: 参与交互的角色 ID（可变参数）
        ttl: 锁 TTL（秒），默认 30

    Yields:
        bool: True 表示所有锁获取成功，False 表示至少一个失败
    """
    # 按 ID 字符串排序，防止 A→B 和 B→A 死锁
    sorted_ids = sorted(str(cid) for cid in character_ids)
    # 去重（同一角色只锁一次）
    unique_ids = list(dict.fromkeys(sorted_ids))

    # key -> 持有者 token（释放时 compare-and-delete 校验）
    acquired: dict[str, str] = {}
    all_acquired = False
    renew_stop = asyncio.Event()
    watchdog = asyncio.create_task(lock_watchdog(redis, renew_stop, acquired, ttl))

    try:
        for cid in unique_ids:
            lock_key = f"{_RESOURCE_LOCK_PREFIX}{cid}"
            token = uuid4().hex
            success = await redis.set(lock_key, token, ex=ttl, nx=True)
            if success:
                acquired[lock_key] = token
            else:
                logger.debug(
                    "resource_lock_acquire_failed",
                    character_id=cid,
                    lock_key=lock_key,
                )
                break
        else:
            # 所有锁都获取成功
            all_acquired = True
            if len(unique_ids) > 1:
                logger.debug(
                    "resource_locks_acquired",
                    character_ids=unique_ids,
                    count=len(unique_ids),
                )

        yield all_acquired

    finally:
        renew_stop.set()
        with suppress(asyncio.CancelledError):
            await watchdog
        for lock_key, token in acquired.items():
            try:
                await release_lock(redis, lock_key, token)
            except Exception:
                logger.warning("resource_lock_release_failed", lock_key=lock_key)


async def lock_watchdog(redis: Redis, renew_stop: asyncio.Event, acquired: dict[str, str], ttl: int) -> None:
    """锁看门狗：受锁保护的操作可能超过 TTL（含多次 LLM 调用），定期续租防止锁过期易主"""
    interval = ttl / 3
    while not renew_stop.is_set():
        try:
            await asyncio.wait_for(renew_stop.wait(), timeout=interval)
        except TimeoutError:
            for lock_key, token in list(acquired.items()):
                renewed = await renew_lock(redis, lock_key, token, ttl)
                if not renewed:
                    logger.warning("resource_lock_renew_failed", lock_key=lock_key)
