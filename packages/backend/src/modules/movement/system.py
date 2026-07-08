"""移动系统

处理角色在场景间的移动，包括路径规划和耗时计算。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.modules.town.loader import SceneLoader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MovementResult:
    """移动结果

    Attributes:
        success: 是否成功（可达且场景开放）
        path: 移动路径（场景 ID 列表，含起点和终点）
        total_minutes: 总耗时（虚拟分钟）
        reason: 失败原因（success=False 时有值）
    """

    success: bool
    path: list[str]
    total_minutes: int
    reason: str | None = None


class MovementSystem:
    """移动系统

    用法：
        system = MovementSystem(scene_loader)
        result = await system.move(character_id, "home", "cafe", hour=10)
        if result.success:
            # 应用移动结果
            ...

    职责：
    1. 查询两场景间是否可达
    2. 计算移动耗时（基础耗时 + 动态调整）
    3. 更新 Redis 中的角色位置和场景人数
    """

    def __init__(self, scene_loader: SceneLoader):
        self.scene_loader = scene_loader

    async def calculate_move(
        self,
        from_scene: str,
        to_scene: str,
        hour: int | None = None,
        is_workday: bool = True,
    ) -> MovementResult:
        """计算移动结果（不实际执行）

        Args:
            from_scene: 起始场景 ID
            to_scene: 目标场景 ID
            hour: 当前小时（用于场景开放判断），None 跳过判断
            is_workday: 是否工作日

        Returns:
            MovementResult
        """
        # 同场景无需移动
        if from_scene == to_scene:
            return MovementResult(
                success=True,
                path=[from_scene],
                total_minutes=0,
            )

        # 查询移动耗时
        travel_time = self.scene_loader.get_travel_time(from_scene, to_scene)
        if travel_time is None:
            return MovementResult(
                success=False,
                path=[],
                total_minutes=0,
                reason=f"场景 {from_scene} 无法直达 {to_scene}",
            )

        # 检查目标场景是否开放
        if hour is not None:
            if not self.scene_loader.is_scene_open(to_scene, hour, is_workday):
                return MovementResult(
                    success=False,
                    path=[from_scene, to_scene],
                    total_minutes=travel_time,
                    reason=f"场景 {to_scene} 当前未开放",
                )

        return MovementResult(
            success=True,
            path=[from_scene, to_scene],
            total_minutes=travel_time,
        )

    async def execute_move(
        self,
        character_id: str,
        from_scene: str,
        to_scene: str,
        hour: int | None = None,
        is_workday: bool = True,
    ) -> MovementResult:
        """执行移动（更新 Redis 状态）

        Args:
            character_id: 角色 ID
            from_scene: 起始场景
            to_scene: 目标场景
            hour: 当前小时
            is_workday: 是否工作日

        Returns:
            MovementResult
        """
        result = await self.calculate_move(
            from_scene, to_scene, hour, is_workday
        )

        if not result.success:
            logger.warning(
                "移动失败: %s %s->%s, 原因: %s",
                character_id, from_scene, to_scene, result.reason,
            )
            return result

        # 同场景无需更新
        if from_scene != to_scene:
            # 离开旧场景
            await self.scene_loader.character_leave(character_id, from_scene)
            # 进入新场景
            await self.scene_loader.character_enter(character_id, to_scene)

            logger.info(
                "角色 %s 从 %s 移动到 %s（耗时 %d 分钟）",
                character_id, from_scene, to_scene, result.total_minutes,
            )

        return result

    def find_shortest_path(
        self, from_scene: str, to_scene: str, max_hops: int = 3
    ) -> tuple[list[str] | None, int]:
        """寻找最短路径（Dijkstra 简化版）

        用于无法直达时的路径规划。

        Args:
            from_scene: 起始场景
            to_scene: 目标场景
            max_hops: 最大中转次数

        Returns:
            (路径, 总耗时)，不可达返回 (None, 0)
        """
        if from_scene == to_scene:
            return [from_scene], 0

        # 简化的 BFS 搜索
        import heapq

        # 优先队列：(总耗时, 路径)
        queue: list[tuple[int, list[str]]] = [(0, [from_scene])]
        visited: set[str] = set()

        while queue:
            total_time, path = heapq.heappop(queue)
            current = path[-1]

            if current == to_scene:
                return path, total_time

            if current in visited:
                continue
            visited.add(current)

            if len(path) - 1 >= max_hops + 1:  # 限制中转次数
                continue

            neighbors = self.scene_loader._world_map.get_neighbors(current)
            for next_scene, time in neighbors.items():
                if next_scene not in visited:
                    heapq.heappush(
                        queue, (total_time + time, path + [next_scene])
                    )

        return None, 0
