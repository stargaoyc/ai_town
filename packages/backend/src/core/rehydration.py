"""启动时 PG→Redis 状态回灌（P0-3）

Redis 是实时状态真相源，PG 是镜像。Redis 重启/清空后，
启动时从 PG 镜像回灌缺失的实时状态键，保证世界与角色状态不丢。

配合 P0-1（工具 delta 纳入 PG 事务）保证 PG 镜像完整，
回灌回来的状态与 Redis 丢失前一致。
"""

from datetime import UTC, datetime

from redis.asyncio import Redis
from structlog import get_logger

from src.core.state_codec import encode_state_mapping
from src.db.models import CharacterState
from src.db.repositories import CharacterRepository, WorldSnapshotRepository
from src.db.session import db

logger = get_logger(__name__)


def character_state_mapping(st: CharacterState) -> dict[str, str]:
    """将 PG CharacterState 行编码为 Redis 哈希映射（回灌用）"""
    return encode_state_mapping(
        {
            "location": st.location,
            "stamina": st.stamina,
            "satiety": st.satiety,
            "mood": st.mood,
            "money": st.money,
            "inventory": st.inventory,
            "current_action": st.current_action,
            "phone_battery": st.phone_battery,
            "social_energy": st.social_energy,
        }
    )


async def restore_character_to_redis(redis: Redis, state: CharacterState) -> None:
    """把单个角色的 PG 镜像写回 Redis（键不存在才调用方判断后使用）"""
    await redis.hset(
        f"char:{state.character_id}:state",
        mapping=character_state_mapping(state),  # type: ignore[arg-type]
    )


async def rehydrate_states(redis: Redis) -> None:
    """扫描 PG 镜像，回灌 Redis 缺失的实时状态键

    1. character_states → char:{id}:state（键缺失才写）
    2. 最新 world_snapshots → world:state（键缺失才写）
    """
    # 角色状态回灌
    restored_chars = 0
    async with db.session() as session:
        char_repo = CharacterRepository(session)
        states = await char_repo.get_all_states()
        for st in states:
            key = f"char:{st.character_id}:state"
            if await redis.exists(key):
                continue
            await restore_character_to_redis(redis, st)
            restored_chars += 1
    if restored_chars:
        logger.info("rehydrated_character_states", count=restored_chars)

    # 世界状态回灌（主哈希摘要；各演化器哈希由演化器在下次 Tick 重建）
    async with db.session() as session:
        snapshot_repo = WorldSnapshotRepository(session)
        snapshot = await snapshot_repo.get_latest()
        if snapshot is not None and not await redis.exists("world:state"):
            mapping = {
                "tick_id": str(snapshot.tick_id),
                "world_time": snapshot.world_time.isoformat() if snapshot.world_time else "",
                "weather": snapshot.weather or "sunny",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            await redis.hset("world:state", mapping=mapping)  # type: ignore[arg-type]
            logger.info("rehydrated_world_state", tick_id=snapshot.tick_id)
