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

# 角色 Tick 互斥锁前缀（与 CharacterTickEngine.LOCK_PREFIX 同一键族）。
# 对账等旁路写入要与「正在写状态的 Tick」互斥，必须抢同一把锁；
# 不直接 import tick 模块取常量：其携带 LLM/Prompt 重依赖，
# locks 应保持零业务依赖可独立单测（一致性由 tests/test_locks.py 钉死）。
TICK_LOCK_PREFIX = "char:tick:lock:"

# 旁路临界区的锁 TTL（秒）：对账修复、API 传送等短临界区借 Tick 锁时使用。
# 远短于 Tick 锁的 30s——这些操作只做「复核 + 一次写入」，持有者崩溃时
# 最多阻塞下一轮 Tick 5 秒。集中定义以免各调用方各定一个值并逐渐漂移。
SHORT_CRITICAL_SECTION_TTL = 5

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


# fencing 原子写：仅当锁键仍等于本持有者的 token 时才写状态哈希。
#
# 背景（审查 §4.1.3 并发-01）：角色 Tick 的并发保护此前是**协作式**的——
# 看门狗每 ttl/3（10s）才检查一次续租结果，锁过期后本实例最长要 10 秒才
# 感知。看门狗能覆盖的是「流程内的 await 边界」，覆盖不了「进程停顿苏醒
# 后紧接着的一次写入」：锁在 T 时刻易主，本实例 T+9 苏醒直接 HSET 覆盖。
#
# 把「校验持有权」与「写入状态」合并进单个 Redis 原子操作后，旧持有者的
# 任何迟到写入都会被直接拒绝（返回 0），与 WorldEngine 的纪元 CAS 同构。
_FENCED_STATE_WRITE_LUA = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
for i = 2, #ARGV, 2 do
  redis.call('HSET', KEYS[2], ARGV[i], ARGV[i + 1])
end
return 1
"""


async def fenced_state_write(
    redis: Redis,
    lock_key: str,
    token: str,
    state_key: str,
    mapping: dict[str, str],
) -> bool:
    """带 fencing 的状态写入：仅当仍持有锁时才落盘

    Args:
        redis: Redis 客户端
        lock_key: 保护该状态的锁键（Tick 锁 / 对账修复锁）
        token: 获取锁时写入的持有者令牌
        state_key: 目标状态哈希键
        mapping: 待写入的字段映射（须已编码为字符串）

    Returns:
        True 表示写入生效；False 表示锁已易主，调用方**必须**中止后续写入
    """
    args: list[str] = [token]
    for field, value in mapping.items():
        args.extend((field, value))
    result = await redis.eval(_FENCED_STATE_WRITE_LUA, 2, lock_key, state_key, *args)
    return int(result) == 1


async def try_acquire_lock(redis: Redis, key: str, ttl: int) -> str | None:
    """无等待尝试获取单把锁（SET NX EX），成功返回持有者 token，失败立即返回 None

    acquire_resource_locks 面向跨角色多锁场景；周期性后台任务（如对账修复）
    只需与单个角色的 Tick 互斥，且抢不到时应跳过本轮而非阻塞——
    单键 fail-fast 语义独立成原语，避免为单键场景套用多锁协议。
    """
    token = uuid4().hex
    acquired = await redis.set(key, token, ex=ttl, nx=True)
    return token if acquired else None


@asynccontextmanager
async def acquire_resource_locks(
    redis: Redis,
    *character_ids: UUID | str,
    ttl: int = _DEFAULT_TTL,
    lock_lost: asyncio.Event | None = None,
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
        lock_lost: 失锁信号（审查 §4.1.3 并发-03）。跨角色操作（对话、
            送礼）含多轮 LLM 往返，耗时可能越过 TTL；锁易主后受其保护的
            关系/记忆写入必须中止。传入后由看门狗在续租失败时置位，调用方
            在写入前自查。缺省表示调用方不关心失锁（仅用于短临界区）。

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
    watchdog = asyncio.create_task(watch_locks(redis, renew_stop, acquired, ttl, lock_lost=lock_lost))

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


async def watch_locks(
    redis: Redis,
    stop: asyncio.Event,
    locks: dict[str, str],
    ttl: int,
    lock_lost: asyncio.Event | None = None,
) -> asyncio.Event:
    """锁看门狗：定期续租，任一续租失败即置位 lock_lost

    受锁保护的操作（Tick、跨角色交互）含多次 LLM 调用，耗时可能超过 TTL，
    需定期续租防止锁过期易主。续租失败（renew_lock 返回 False 或抛异常）
    转化为失锁信号，供调用方在 await 边界检查并中止后续状态写入——
    失锁后继续写会造成跨实例 double-tick（round-3 review H10）。

    原先有 lock_watchdog / watch_locks 两个变体，前者只记日志、让跨角色
    锁路径无法感知失锁（审查 §4.1.3 并发-03），已合并为本函数。

    Args:
        redis: Redis 客户端
        stop: 停止信号，置位后看门狗退出
        locks: 锁键 -> 持有者 token
        ttl: 锁 TTL（秒），续租间隔为 ttl/3
        lock_lost: 外部预先创建的失锁事件（调用方需在看门狗启动前持有引用）；
            缺省时内部创建并通过返回值交还

    Returns:
        lock_lost 事件：任一锁续租失败后被置位
    """
    lost = lock_lost if lock_lost is not None else asyncio.Event()
    interval = ttl / 3
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            for lock_key, token in list(locks.items()):
                try:
                    renewed = await renew_lock(redis, lock_key, token, ttl)
                except Exception as e:
                    # Redis 故障按失锁处理：宁可误停一个 Tick，不可双写（H10）
                    logger.warning("lock_watchdog_renew_error", lock_key=lock_key, error=str(e))
                    renewed = False
                if not renewed:
                    logger.warning("lock_watchdog_renew_failed", lock_key=lock_key)
                    lost.set()
    return lost
