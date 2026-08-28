"""Redis vs PG 状态对账（roadmap #24）

Redis 是实时状态真相源，PG 是镜像。双写路径上的任何一环失败都会造成
两库分叉（A-2），启动回灌（rehydration.py）只覆盖「Redis 键整体缺失」，
对运行期静默漂移无能为力。

本模块定期 diff 两库并自动修复：
1. Redis 键缺失 → 从 PG 回灌（pg_to_redis）
2. 数值/位置字段漂移 → 版本感知仲裁修复（方向见 run_reconciliation）
3. 场景占用漂移 → 以 PG location 重算计数修正 visitors（P0-2）
4. 优先队列：tick 双写失败路径写入 reconcile:prioritize，
   每轮先处理队列内角色再全量扫描（P1-2）

漂移判定只针对 Tick 链路真正双写的字段；current_action 自 P1-3 起纳入
对账（dict 比较）——中途崩溃残留的陈旧动作由下轮对账收敛。
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from structlog import get_logger

from src.core.locks import TICK_LOCK_PREFIX, release_lock, try_acquire_lock
from src.core.rehydration import restore_character_to_redis
from src.core.state_codec import decode_state_value
from src.db.models import Character, CharacterState

logger = get_logger(__name__)

# 参与对账的字段（与 tick 双写、PG 镜像列一一对应）
_RECONCILE_FIELDS = (
    "location",
    "stamina",
    "satiety",
    "mood",
    "money",
    "phone_battery",
    "social_energy",
    "inventory",
    "current_action",
)

# 数值字段集合（PG int 列；Redis 侧经 decode_state_value 已还原为 int）
_INT_FIELDS = frozenset({"stamina", "satiety", "money", "phone_battery", "social_energy"})

# 复合字段集合（PG JSONB 列；dict 语义比较）
_JSON_FIELDS = frozenset({"inventory", "current_action"})

# tick 双写失败时的优先修复队列（P1-2）：SADD 幂等，对账每轮先 SPOP 排空
_PRIORITIZE_KEY = "reconcile:prioritize"

# 修复临界区（复核+HSET）远短于 Tick 的 30s 锁 TTL，短 TTL 即可：
# 持有者崩溃时最多阻塞下一轮 Tick 5 秒
_REPAIR_LOCK_TTL = 5

# 基线版本键 TTL（审查 §4.1.3）：此前 redis.set 不带 TTL，键只在删除角色时清理，
# 角色频繁增删会持续泄漏。取 7 天——远长于 600s 对账周期，重启后基线仍在；
# 键缺失时回退为「PG 版本即基线」，语义安全。
_VER_KEY_TTL = 7 * 24 * 3600


async def request_character_repair(redis: Redis, character_id: Any) -> None:
    """把角色加入优先修复队列（tick 写 Redis 失败时调用，P1-2）

    队列消费后角色立即进入本轮对账，无需等待最长一个全量周期；
    Redis 本身不可用时入队失败——此时全量扫描路径会照常兜底。
    """
    try:
        await redis.sadd(_PRIORITIZE_KEY, str(character_id))
    except Exception as e:
        logger.warning("reconcile_prioritize_enqueue_failed", error=str(e))


def fields_differ(field: str, redis_value: Any, pg_value: Any) -> bool:
    """比较单字段在两库的值是否实质不同"""
    if field in _INT_FIELDS:
        try:
            return int(redis_value) != int(pg_value or 0)
        except (TypeError, ValueError):
            return True
    if field in _JSON_FIELDS:
        return dict(redis_value or {}) != dict(pg_value or {})
    # location / mood：字符串比较（空值归一为 None）
    return (str(redis_value) if redis_value else None) != (str(pg_value) if pg_value else None)


def collect_drift(pg_state: CharacterState, redis_hash: dict[Any, Any]) -> list[str]:
    """找出 Redis 哈希与 PG 镜像之间漂移的字段名列表"""
    drift: list[str] = []
    for field in _RECONCILE_FIELDS:
        raw = redis_hash.get(field)
        if raw is None or raw == "":
            # Redis 未写该字段（None 被编码过滤 / 空串）：信息不足，不判漂移，
            # 交给下次 Tick 全量写入后自然对齐
            continue
        redis_value = decode_state_value(field, raw)
        if fields_differ(field, redis_value, getattr(pg_state, field)):
            drift.append(field)
    return drift


SessionFactory = Any  # () -> AbstractAsyncContextManager[AsyncSession]


def _expected_occupancy(rows: list[tuple[Character, CharacterState]]) -> dict[str, int]:
    """从 (Character, CharacterState) 行集推导各场景期望在场人数（P0-2）"""
    counts: dict[str, int] = {}
    for _character, pg_state in rows:
        loc = pg_state.location
        if loc:
            counts[loc] = counts.get(loc, 0) + 1
    return counts


async def _reconcile_scene_visitors(redis: Redis, rows: list[tuple[Character, CharacterState]]) -> int:
    """校验并修复 world:scene:visitors 与 PG location 的漂移（P0-2）

    Returns:
        修复的场景数
    """
    from src.modules.town.loader import SceneLoader
    from src.observability.metrics import RECONCILE_DRIFT_TOTAL, RECONCILE_REPAIR_TOTAL

    expected = _expected_occupancy(rows)
    raw = await redis.hgetall(SceneLoader.VISITORS_KEY)
    actual: dict[str, int] = {}
    for scene_id, count_raw in raw.items():
        scene_str = scene_id.decode("utf-8") if isinstance(scene_id, bytes | bytearray) else str(scene_id)
        try:
            actual[scene_str] = int(count_raw)
        except (TypeError, ValueError):
            actual[scene_str] = -1

    drifted = {sid: cnt for sid, cnt in expected.items() if actual.get(sid, 0) != cnt}
    stale = [sid for sid in actual if sid not in expected and actual[sid] != 0]
    if not drifted and not stale:
        return 0

    pipe = redis.pipeline(transaction=False)
    for sid in set(drifted) | set(stale):
        target = drifted.get(sid, 0)
        if target:
            pipe.hset(SceneLoader.VISITORS_KEY, sid, str(target))
        else:
            pipe.hdel(SceneLoader.VISITORS_KEY, sid)
    await pipe.execute()

    RECONCILE_DRIFT_TOTAL.labels(kind="scene_visitors").inc()
    RECONCILE_REPAIR_TOTAL.labels(direction="pg_to_redis").inc()
    logger.warning("reconcile_scene_visitors_repaired", scenes=sorted(set(drifted) | set(stale)))
    return len(set(drifted) | set(stale))


async def run_reconciliation(redis: Redis, session_factory: SessionFactory) -> dict[str, int]:
    """执行一轮全量对账，返回统计计数。

    修复方向：
    - Redis 键缺失 → 整键从 PG 回灌（pg_to_redis）
    - 字段值漂移 → 版本感知仲裁：
        * PG version 相对上次对账基线有前进（说明 Tick/API 刚写过 PG）
          → 本次以 PG 为准修正 Redis（pg_to_redis），避免把刚写入的合法
            变更回滚成陈旧的 Redis 值；
        * 基线未变（漂移来自绕过双写的路径）→ 以 Redis 为准修正 PG。
      每轮处理完记录基线 `char:{id}:rec_ver`。
    - 场景占用漂移 → 以 PG location 重算修正 visitors 计数（P0-2）

    P1-4：每角色的 exists/hgetall/get 三次往返合并进 pipeline；
    P1-2：每轮先排空 reconcile:prioritize 优先队列。

    Args:
        redis: Redis 客户端（decode_responses=True）
        session_factory: 会话上下文工厂；生产传 db.session，
            测试注入受管 session 以参与调用方事务

    由 main.py 后台循环周期性调用。
    """
    from src.core.state_codec import decode_state_value as _decode  # 局部别名，保持导入集中
    from src.observability.metrics import RECONCILE_DRIFT_TOTAL, RECONCILE_REPAIR_TOTAL

    stats = {"missing_keys": 0, "value_drift": 0, "repairs": 0}

    async with session_factory() as session:
        rows = (await session.execute(select(Character, CharacterState).join(CharacterState))).unique().all()
        by_id = {character.id: (character, pg_state) for character, pg_state in rows}

        # P1-2：优先队列排空——双写失败的角色不等全量周期
        prioritized: list[str] = []
        while True:
            cid_raw = await redis.spop(_PRIORITIZE_KEY)
            if cid_raw is None:
                break
            cid_str = cid_raw.decode("utf-8") if isinstance(cid_raw, bytes | bytearray) else str(cid_raw)
            prioritized.append(cid_str)

        ordered: list[tuple[Character, CharacterState]] = []
        seen: set[Any] = set()
        for cid_str in prioritized:
            try:
                from uuid import UUID

                cid = UUID(cid_str)
            except ValueError:
                continue
            pair = by_id.get(cid)
            if pair is not None and cid not in seen:
                ordered.append(pair)
                seen.add(cid)
        for character, pg_state in rows:
            if character.id not in seen:
                ordered.append((character, pg_state))
                seen.add(character.id)

        for character, pg_state in ordered:
            key = f"char:{character.id}:state"
            ver_key = f"char:{character.id}:rec_ver"
            # P1-4：exists + hgetall + get 合并为一次 pipeline 往返
            pipe = redis.pipeline(transaction=False)
            pipe.exists(key)
            pipe.hgetall(key)
            pipe.get(ver_key)
            exists, raw_hash, rec_ver_raw = await pipe.execute()

            if not exists:
                await restore_character_to_redis(redis, pg_state)
                await redis.set(ver_key, pg_state.version, ex=_VER_KEY_TTL)
                stats["missing_keys"] += 1
                stats["repairs"] += 1
                RECONCILE_DRIFT_TOTAL.labels(kind="missing_key").inc()
                RECONCILE_REPAIR_TOTAL.labels(direction="pg_to_redis").inc()
                logger.warning("reconcile_missing_key_rehydrated", character_id=str(character.id))
                continue

            drift = collect_drift(pg_state, raw_hash)
            if not drift:
                # 无漂移也要推进基线：rec_ver 语义是「上次对账时 PG version」，
                # 否则 pg_advanced 仲裁缺少起点，第一轮后的 PG 写入永远无法
                # 被判定为「前进」（round-7：集成测试首次运行暴露，此前该路径
                # 因 conftest 探测缺陷从未真正执行）
                if rec_ver_raw is None:
                    await redis.set(ver_key, pg_state.version, ex=_VER_KEY_TTL)
                continue

            # 版本感知仲裁：PG 在上次对账后发生过写入 → PG 更可信，
            # 反向把 PG 镜像推回 Redis，而不是用陈旧 Redis 覆盖新写入。
            # 首次对账无基线：无法判定新旧，维持「Redis 为准」的默认方向。
            if rec_ver_raw is None:
                pg_advanced = False
                rec_ver = 0
            else:
                try:
                    rec_ver = int(rec_ver_raw)
                except (TypeError, ValueError):
                    rec_ver = 0
                pg_advanced = int(pg_state.version) > rec_ver

            if pg_advanced:
                # 以 PG 为准修正 Redis（方向翻转）。
                # round-3 review M9：循环开头的快照可能在 diff 期间被 Tick 推进，
                # 直接 HSET 会把陈旧镜像盖到刚写入的新状态上——写前重读版本复核新鲜度，
                # 过期则本轮跳过（下轮以新快照重新仲裁），且不更新基线
                #
                # round-5 review L7：新鲜度复核只封住「Tick 已写 PG」的窗口；
                # Tick 在复核之后、HSET 之前完成双写仍会交错（PG 提交后才写 Redis）。
                # 故写前持有该角色的 Tick 互斥锁（与 tick_character 同一把
                # char:tick:lock:{id}），把「复核+写入」整体关进临界区；
                # 拿不到锁 = Tick 正在运行，跳过本角色即可——对账周期性执行，
                # 下轮以新快照自愈。
                tick_lock_key = f"{TICK_LOCK_PREFIX}{character.id}"
                repair_token = await try_acquire_lock(redis, tick_lock_key, _REPAIR_LOCK_TTL)
                if repair_token is None:
                    logger.info("reconcile_repair_skipped_tick_active", character_id=str(character.id))
                    continue
                try:
                    fresh_version = await session.scalar(
                        select(CharacterState.version).where(CharacterState.character_id == character.id)
                    )
                    if fresh_version is None or int(fresh_version) != int(pg_state.version):
                        logger.debug("reconcile_skip_stale_snapshot", character_id=str(character.id))
                        continue
                    from src.core.state_codec import encode_state_mapping

                    await redis.hset(
                        key,
                        mapping=encode_state_mapping({f: getattr(pg_state, f) for f in drift}),  # type: ignore[arg-type]
                    )
                    direction = "pg_to_redis"
                    RECONCILE_REPAIR_TOTAL.labels(direction="pg_to_redis").inc()
                    baseline_version = int(fresh_version)
                finally:
                    # 释放失败仅告警不中断整轮：TTL 到期自动解锁，
                    # 最坏代价是下一轮 Tick 晚启动几秒
                    try:
                        await release_lock(redis, tick_lock_key, repair_token)
                    except Exception:
                        logger.warning("reconcile_repair_lock_release_failed", character_id=str(character.id))
            else:
                # 以 Redis 为准修正 PG。
                # round-3 review M9：修复必须经 update_state 走版本自增路径，
                # 裸 UPDATE 不递增 version，会让 pg_advanced 仲裁的
                # 「版本单调前进」假设失效，后续轮次无法判定新旧。
                # 此方向无需 Tick 锁（round-5 review L7 不对称性）：
                # update_state 版本自增本身参与 pg_advanced 仲裁，与并发 Tick
                # 的 PG 写入天然串行安全，且本方向不写 Redis
                from src.db.repositories import CharacterRepository

                update_values: dict[str, Any] = {}
                for field in drift:
                    raw = raw_hash.get(field)
                    update_values[field] = _decode(field, raw) if raw is not None else None
                await CharacterRepository(session).update_state(character.id, **update_values)
                direction = "redis_to_pg"
                RECONCILE_REPAIR_TOTAL.labels(direction="redis_to_pg").inc()
                repaired_version = await session.scalar(
                    select(CharacterState.version).where(CharacterState.character_id == character.id)
                )
                assert repaired_version is not None  # 刚完成条件更新，行必存在
                baseline_version = int(repaired_version)

            # 基线取修复后的版本：redis_to_pg 方向的 +1 是对账自己制造的，
            # 若仍记旧值，下轮会把这次前进误判为 Tick 写入而翻转修复方向（M9）
            await redis.set(ver_key, baseline_version, ex=_VER_KEY_TTL)
            stats["value_drift"] += 1
            stats["repairs"] += 1
            RECONCILE_DRIFT_TOTAL.labels(kind="value_drift").inc()
            logger.warning(
                "reconcile_drift_detected",
                character_id=str(character.id),
                fields=drift,
                direction=direction,
                pg_version=int(pg_state.version),
                reconciled_version=rec_ver,
            )

        # P0-2：场景占用对账（复用同一批行集推导期望值）
        stats["scene_visitors_repairs"] = await _reconcile_scene_visitors(redis, rows)

    if stats["repairs"]:
        logger.info("reconciliation_completed_with_repairs", **stats)
    else:
        logger.info("reconciliation_completed_clean", scanned=len(rows))
    return stats
