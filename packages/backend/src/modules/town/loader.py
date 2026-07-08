"""小镇场景加载器

从 YAML 加载场景配置和世界地图，运行时管理场景动态状态。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from redis.asyncio import Redis

from src.modules.town.schema import Scene, SceneRuntimeState, WorldMap

logger = logging.getLogger(__name__)


class SceneLoader:
    """场景加载器

    职责：
    1. 从 scenes.yaml 加载场景静态配置
    2. 从 world-map.yaml 加载连通矩阵
    3. 运行时查询场景状态（开放/拥挤度）
    4. 维护 Redis 中的场景实时状态

    用法：
        loader = SceneLoader(redis)
        await loader.load_from_files("configs/scenes.yaml", "configs/world-map.yaml")
        is_open = await loader.is_scene_open("cafe", hour=10)
    """

    # Redis key 前缀
    SCENE_STATE_KEY = "scene:{scene_id}:state"
    SCENE_CHARACTERS_KEY = "scene:{scene_id}:characters"

    def __init__(self, redis: Redis):
        self.redis = redis
        self._scenes: dict[str, Scene] = {}
        self._world_map: WorldMap = WorldMap()

    async def load_from_files(
        self, scenes_path: str | Path, map_path: str | Path
    ) -> None:
        """从 YAML 文件加载场景和地图

        Args:
            scenes_path: scenes.yaml 路径
            map_path: world-map.yaml 路径
        """
        # 加载场景
        scenes_raw = yaml.safe_load(
            Path(scenes_path).read_text(encoding="utf-8")
        )
        scenes_list = scenes_raw.get("scenes", [])
        self._scenes = {}
        for scene_data in scenes_list:
            scene = Scene.model_validate(scene_data)
            self._scenes[scene.id] = scene
        logger.info("加载 %d 个场景", len(self._scenes))

        # 加载世界地图
        map_raw = yaml.safe_load(
            Path(map_path).read_text(encoding="utf-8")
        )
        self._world_map = WorldMap(adjacency=map_raw.get("adjacency", {}))
        logger.info("加载世界地图: %d 个节点", len(self._world_map.adjacency))

        # 初始化 Redis 状态
        await self._init_redis_state()

    async def _init_redis_state(self) -> None:
        """初始化所有场景的 Redis 状态"""
        for scene_id, scene in self._scenes.items():
            key = self.SCENE_STATE_KEY.format(scene_id=scene_id)
            # 仅在不存在时初始化（不覆盖已有状态）
            exists = await self.redis.exists(key)
            if not exists:
                state = SceneRuntimeState(scene_id=scene_id)
                await self.redis.hset(key, mapping={
                    "is_open": "1" if state.is_open else "0",
                    "current_count": "0",
                    "crowdedness": "0.0",
                })
        logger.debug("Redis 场景状态已初始化")

    def get_scene(self, scene_id: str) -> Scene | None:
        """获取场景配置"""
        return self._scenes.get(scene_id)

    def get_all_scenes(self) -> dict[str, Scene]:
        """获取所有场景"""
        return self._scenes

    def get_travel_time(self, from_scene: str, to_scene: str) -> int | None:
        """获取移动耗时"""
        return self._world_map.get_travel_time(from_scene, to_scene)

    def is_scene_open(
        self, scene_id: str, hour: int, is_workday: bool = True
    ) -> bool:
        """查询场景是否开放

        Args:
            scene_id: 场景 ID
            hour: 当前小时（0-23）
            is_workday: 是否工作日

        Returns:
            是否开放
        """
        scene = self._scenes.get(scene_id)
        if scene is None:
            return False

        # 工作日限制
        if scene.workday_only and not is_workday:
            return False

        # 营业时间
        start, end = scene.open_hours
        if end == 0:
            end = 24
        if start == 0 and end == 24:
            return True
        return start <= hour < end

    async def get_crowdedness(self, scene_id: str) -> float:
        """获取场景拥挤度（0.0-1.0）

        Redis 中实时更新，缓存未命中时从场景容量计算。
        """
        key = self.SCENE_STATE_KEY.format(scene_id=scene_id)
        count_str = await self.redis.hget(key, "current_count")
        if count_str is None:
            return 0.0

        count = int(count_str)
        scene = self._scenes.get(scene_id)
        if scene is None or scene.capacity == 0:
            return 0.0

        return min(count / scene.capacity, 1.0)

    async def character_enter(self, character_id: str, scene_id: str) -> None:
        """角色进入场景

        更新 Redis 中的场景人数和角色列表。
        """
        # 更新人数
        state_key = self.SCENE_STATE_KEY.format(scene_id=scene_id)
        await self.redis.hincrby(state_key, "current_count", 1)

        # 加入角色集合
        chars_key = self.SCENE_CHARACTERS_KEY.format(scene_id=scene_id)
        await self.redis.sadd(chars_key, character_id)
        logger.debug("角色 %s 进入场景 %s", character_id, scene_id)

    async def character_leave(self, character_id: str, scene_id: str) -> None:
        """角色离开场景"""
        state_key = self.SCENE_STATE_KEY.format(scene_id=scene_id)
        count = await self.redis.hincrby(state_key, "current_count", -1)
        if count < 0:
            await self.redis.hset(state_key, "current_count", "0")

        chars_key = self.SCENE_CHARACTERS_KEY.format(scene_id=scene_id)
        await self.redis.srem(chars_key, character_id)
        logger.debug("角色 %s 离开场景 %s", character_id, scene_id)

    async def get_present_characters(self, scene_id: str) -> list[str]:
        """获取场景内的所有角色 ID"""
        chars_key = self.SCENE_CHARACTERS_KEY.format(scene_id=scene_id)
        members = await self.redis.smembers(chars_key)
        return list(members)
