"""场景演化器 - 更新场景开放状态与拥挤度

根据当前虚拟时间判断各场景是否开放，并基于在场角色数 / 容量计算拥挤度。
状态存储于 Redis Hash: `world:state:scenes`（field: scene_id → JSON{open, crowded, visitors, capacity}）。

场景元数据（开放时段/容量）单一真相源是 configs/scenes.yaml，经 SceneLoader 注入；
本模块不维护场景副本（审查 P0-3：硬编码副本曾与 yaml 脱节，导致拥挤度只覆盖部分场景）。
"""

from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.core.world.evolutions.base import WorldEvolution
from src.core.world.evolutions.time_evolution import TIME_KEY
from src.modules.town.loader import SceneLoader
from src.modules.town.schema import is_open_hours
from src.runtime import get_scene_loader

logger = get_logger(__name__)

# 场景状态在 Redis 中的 Key
SCENES_KEY = "world:state:scenes"
# 各场景在场角色数（scene_id → count），由 SceneLoader.record_movement 统一维护
VISITORS_KEY = SceneLoader.VISITORS_KEY

# 开放时间判断唯一实现收敛到 schema.is_open_hours（审查 P3 双实现收敛）；
# 此处保留 is_open 名称以维持既有导入方与测试兼容
is_open = is_open_hours


class SceneEvolution(WorldEvolution):
    """场景演化器

    每 Tick 根据虚拟时间刷新所有场景的开放状态与拥挤度。
    拥挤度 = min(100, round(visitors / capacity * 100))。
    """

    name = "scene"

    def __init__(self, scenes: dict[str, dict[str, Any]] | None = None) -> None:
        if scenes is not None:
            self.scenes = scenes
            return
        # 未显式传入时从 SceneLoader（configs/scenes.yaml 的运行时形态）解析。
        # WorldEngine 在 lifespan 中晚于 set_scene_loader 构造，此处 loader 必已就绪；
        # loader 属可选模块（见 main.py 模块降级策略），缺失时演化为空操作并告警。
        loader = get_scene_loader()
        if loader is None:
            self.scenes = {}
            logger.warning("scene_evolution_no_scene_source", hint="SceneLoader 未初始化，场景状态将不刷新")
            return
        self.scenes = {
            scene_id: {"open_hours": tuple(scene.open_hours), "capacity": scene.capacity}
            for scene_id, scene in loader.get_all_scenes().items()
        }

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
