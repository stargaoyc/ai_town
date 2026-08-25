"""Redis vs PG 状态对账（roadmap #24）

Redis 是实时状态真相源，PG 是镜像。双写路径上的任何一环失败都会造成
两库分叉（A-2），启动回灌（rehydration.py）只覆盖「Redis 键整体缺失」，
对运行期静默漂移无能为力。

本模块定期 diff 两库并自动修复：
1. Redis 键缺失 → 从 PG 回灌（pg_to_redis）
2. 数值/位置字段漂移 → 以 Redis 为准修正 PG（redis_to_pg）

漂移判定只针对 Tick 链路真正双写的字段；current_action 是纯瞬态
（每 Tick 都变），不参与对账。
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from structlog import get_logger

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
)

# 数值字段集合（PG int 列；Redis 侧经 decode_state_value 已还原为 int）
_INT_FIELDS = frozenset({"stamina", "satiety", "money", "phone_battery", "social_energy"})


def fields_differ(field: str, redis_value: Any, pg_value: Any) -> bool:
    """比较单字段在两库的值是否实质不同"""
    if field in _INT_FIELDS:
        try:
            return int(redis_value) != int(pg_value or 0)
        except (TypeError, ValueError):
            return True
    if field == "inventory":
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
        for character, pg_state in rows:
            key = f"char:{character.id}:state"
            ver_key = f"char:{character.id}:rec_ver"
            if not await redis.exists(key):
                await restore_character_to_redis(redis, pg_state)
                await redis.set(ver_key, pg_state.version)
                stats["missing_keys"] += 1
                stats["repairs"] += 1
                RECONCILE_DRIFT_TOTAL.labels(kind="missing_key").inc()
                RECONCILE_REPAIR_TOTAL.labels(direction="pg_to_redis").inc()
                logger.warning("reconcile_missing_key_rehydrated", character_id=str(character.id))
                continue

            raw_hash = await redis.hgetall(key)
            drift = collect_drift(pg_state, raw_hash)
            if not drift:
                continue

            # 版本感知仲裁：PG 在上次对账后发生过写入 → PG 更可信，
            # 反向把 PG 镜像推回 Redis，而不是用陈旧 Redis 覆盖新写入。
            # 首次对账无基线：无法判定新旧，维持「Redis 为准」的默认方向。
            rec_ver_raw = await redis.get(ver_key)
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
            else:
                # 以 Redis 为准修正 PG。
                # round-3 review M9：修复必须经 update_state 走版本自增路径，
                # 裸 UPDATE 不递增 version，会让 pg_advanced 仲裁的
                # 「版本单调前进」假设失效，后续轮次无法判定新旧
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
            await redis.set(ver_key, baseline_version)
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

    if stats["repairs"]:
        logger.info("reconciliation_completed_with_repairs", **stats)
    else:
        logger.info("reconciliation_completed_clean", scanned=len(rows))
    return stats
