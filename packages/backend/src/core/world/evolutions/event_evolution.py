"""事件演化器 - 节日触发与活跃事件维护

根据虚拟日期触发节日事件，事件持续 N 天后自动结束。
状态存储于 Redis Hash: `world:state:events`（field: event_id → JSON{id, name, description, start_date, end_date}）。
"""

from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.core.world.evolutions.base import WorldEvolution
from src.core.world.evolutions.time_evolution import TIME_KEY

logger = get_logger(__name__)

# 事件状态在 Redis 中的 Key
EVENTS_KEY = "world:state:events"

# 节日日历：(month, day) → 事件定义（name / 持续天数 / 描述）
FESTIVAL_CALENDAR: dict[tuple[int, int], dict[str, Any]] = {
    (1, 1): {"name": "新年祭", "duration_days": 1, "description": "新年伊始，小镇共庆。"},
    (4, 5): {"name": "樱花祭", "duration_days": 3, "description": "樱花盛放，镇民齐聚公园赏花。"},
    (7, 15): {"name": "夏日祭", "duration_days": 2, "description": "夏日烟花与捞金鱼。"},
    (10, 31): {"name": "万圣节", "duration_days": 1, "description": "南瓜灯与变装巡游。"},
    (12, 25): {"name": "圣诞节", "duration_days": 3, "description": "圣诞集市与交换礼物。"},
}


class EventEvolution(WorldEvolution):
    """事件演化器

    每 Tick：
    1. 根据当前虚拟日期匹配节日日历，触发尚未激活的事件；
    2. 清理 end_date 已过的结束事件。
    """

    name = "event"

    async def setup(self, redis: Redis) -> None:
        """首次运行时确保事件哈希为空"""
        existing = await redis.hgetall(EVENTS_KEY)
        if not existing:
            await redis.delete(EVENTS_KEY)
            logger.info("event_evolution_initialized")

    async def evolve(self, redis: Redis, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        """触发节日并清理已结束事件"""
        time_state = await self.hgetall_json(redis, TIME_KEY)
        if not time_state or "world_time" not in time_state:
            logger.warning("event_evolution_no_time", tick_id=tick_id)
            return {"active_events": []}

        now = datetime.fromisoformat(time_state["world_time"])
        today = now.date()

        current = await self.hgetall_json(redis, EVENTS_KEY)

        # 1. 触发今日节日
        key = (today.month, today.day)
        festival = FESTIVAL_CALENDAR.get(key)
        if festival:
            event_id = f"{today.year:04d}-{key[0]:02d}-{key[1]:02d}"
            if event_id not in current:
                current[event_id] = {
                    "id": event_id,
                    "name": festival["name"],
                    "description": festival["description"],
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=festival["duration_days"] - 1)).isoformat(),
                }
                logger.info(
                    "event_triggered",
                    event_id=event_id,
                    name=festival["name"],
                    tick_id=tick_id,
                )

        # 2. 清理已结束事件（end_date 早于今天）
        ended = [
            eid
            for eid, ev in current.items()
            if "end_date" in ev and datetime.fromisoformat(ev["end_date"]).date() < today
        ]
        for eid in ended:
            logger.info("event_ended", event_id=eid, tick_id=tick_id)
            current.pop(eid, None)

        # 3. 写回 Redis（无活跃事件时清空 Key）
        if current:
            await self.hset_json(redis, EVENTS_KEY, current)
        else:
            await redis.delete(EVENTS_KEY)

        active_events = list(current.values())
        logger.info("events_updated", tick_id=tick_id, active_count=len(active_events))
        return {"active_events": active_events}
