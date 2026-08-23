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
    - 字段值漂移 → 以 Redis 为准修正 PG（redis_to_pg）

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
            if not await redis.exists(key):
                await restore_character_to_redis(redis, pg_state)
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

            # 以 Redis 为准修正 PG
            update_values: dict[str, Any] = {}
            for field in drift:
                raw = raw_hash.get(field)
                update_values[field] = _decode(field, raw) if raw is not None else None
            from sqlalchemy import update

            stmt = update(CharacterState).where(CharacterState.character_id == character.id).values(**update_values)
            await session.execute(stmt)
            stats["value_drift"] += 1
            stats["repairs"] += 1
            RECONCILE_DRIFT_TOTAL.labels(kind="value_drift").inc()
            RECONCILE_REPAIR_TOTAL.labels(direction="redis_to_pg").inc()
            logger.warning(
                "reconcile_drift_detected",
                character_id=str(character.id),
                fields=drift,
                direction="redis_to_pg",
            )

    if stats["repairs"]:
        logger.info("reconciliation_completed_with_repairs", **stats)
    else:
        logger.info("reconciliation_completed_clean", scanned=len(rows))
    return stats
