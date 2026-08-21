"""场景演化器 - 更新场景开放状态与拥挤度

根据当前虚拟时间判断各场景是否开放，并基于在场角色数 / 容量计算拥挤度。
状态存储于 Redis Hash: `world:state:scenes`（field: scene_id → JSON{open, crowded, visitors, capacity}）。
"""

from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.core.world.evolutions.base import WorldEvolution
from src.core.world.evolutions.time_evolution import TIME_KEY

logger = get_logger(__name__)

# 场景状态在 Redis 中的 Key
SCENES_KEY = "world:state:scenes"
# 各场景在场角色数（scene_id → count），由 Character Tick / Action 执行维护
VISITORS_KEY = "world:scene:visitors"

# 默认场景注册表：开放时段 (start_hour, end_hour) 与容量
# end_hour 可大于 24，表示跨午夜（如 bar 18 → 次日 02:00 记作 26）
DEFAULT_SCENES: dict[str, dict[str, Any]] = {
    "cafe": {"open_hours": (7, 22), "capacity": 30},
    "school": {"open_hours": (8, 17), "capacity": 100},
    "park": {"open_hours": (6, 22), "capacity": 50},
    "plaza": {"open_hours": (0, 24), "capacity": 80},
    "shop": {"open_hours": (9, 21), "capacity": 20},
    "bar": {"open_hours": (18, 26), "capacity": 40},
}


def is_open(open_hours: tuple[int, int], hour: int) -> bool:
    """判断给定小时是否在开放时段内（支持跨午夜）

    Args:
        open_hours: (start, end)，end 可大于 24 表示次日
        hour: 当前小时（0-23）
    """
    start, end = open_hours
    hour = hour % 24
    start = start % 24
    end = end % 24
    if start <= end:
        return start <= hour < end
    # 跨午夜，如 18 → 02
    return hour >= start or hour < end


class SceneEvolution(WorldEvolution):
    """场景演化器

    每 Tick 根据虚拟时间刷新所有场景的开放状态与拥挤度。
    拥挤度 = min(100, round(visitors / capacity * 100))。
    """

    name = "scene"

    def __init__(self, scenes: dict[str, dict[str, Any]] | None = None) -> None:
        self.scenes = scenes or DEFAULT_SCENES

    async def setup(self, redis: Redis) -> None:
        """首次运行时初始化场景状态"""
        existing = await redis.hgetall(SCENES_KEY)
        if not existing:
            await self._refresh(redis, hour=8, visitors_map={})
            logger.info("scene_evolution_initialized", scenes=list(self.scenes.keys()))

    async def evolve(self, redis: Redis, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        """刷新所有场景状态"""
        # 读取当前虚拟时间以获取小时
        time_state = await self.hgetall_json(redis, TIME_KEY)
        if time_state and "world_time" in time_state:
            hour = datetime.fromisoformat(time_state["world_time"]).hour
        else:
            hour = world_state.get("hour", 8)

        # 读取各场景在场角色数
        visitors_map = await self.hgetall_json(redis, VISITORS_KEY)

        scenes_state = await self._refresh(redis, hour, visitors_map)

        logger.info("scenes_updated", tick_id=tick_id, hour=hour, scene_count=len(scenes_state))
        return {"locations": scenes_state}

    async def _refresh(self, redis: Redis, hour: int, visitors_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """根据小时与在场人数重算并写回所有场景状态"""
        scenes_state: dict[str, dict[str, Any]] = {}
        for scene_id, cfg in self.scenes.items():
            visitors = int(visitors_map.get(scene_id, 0))
            capacity = max(1, cfg["capacity"])
            crowdedness = min(100, round(visitors / capacity * 100))
            scene_state = {
                "open": is_open(cfg["open_hours"], hour),
                "crowded": crowdedness,
                "visitors": visitors,
                "capacity": capacity,
            }
            scenes_state[scene_id] = scene_state

        await self.hset_json(redis, SCENES_KEY, scenes_state)
        return scenes_state
