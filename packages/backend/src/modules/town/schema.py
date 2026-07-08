"""小镇场景 Schema 定义

对应 configs/scenes.yaml 和 configs/world-map.yaml。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SceneType(str, Enum):
    """场景类型"""

    INDOOR = "indoor"
    OUTDOOR = "outdoor"


class Scene(BaseModel):
    """场景定义

    对应 scenes.yaml 中的一个场景条目。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=50, description="场景 ID（唯一）")
    name: str = Field(min_length=1, max_length=100, description="场景名")
    type: SceneType = Field(description="场景类型")
    open_hours: list[int] = Field(
        min_length=2, max_length=2, description="营业时间 [开始, 结束]（24h 制）"
    )
    capacity: int = Field(ge=1, le=1000, description="最大容量")
    activities: list[str] = Field(default_factory=list, description="支持的活动")
    weather_affected: bool = Field(default=True, description="是否受天气影响")
    workday_only: bool = Field(default=False, description="是否仅工作日开放")

    @field_validator("open_hours")
    @classmethod
    def validate_open_hours(cls, v: list[int]) -> list[int]:
        """校验营业时间范围"""
        start, end = v[0], v[1]
        if not (0 <= start <= 24 and 0 <= end <= 24):
            raise ValueError("open_hours 必须在 0-24 范围内")
        if start > end and end != 0:
            raise ValueError(f"open_hours 开始时间 {start} 不能大于结束时间 {end}")
        return v


class WorldMap(BaseModel):
    """世界地图 - 场景连通矩阵

    对应 world-map.yaml。
    adjacency[from][to] = 移动耗时（虚拟分钟）
    """

    model_config = ConfigDict(extra="forbid")

    adjacency: dict[str, dict[str, int]] = Field(
        default_factory=dict, description="场景连通矩阵"
    )

    @field_validator("adjacency")
    @classmethod
    def validate_adjacency(cls, v: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        """校验连通矩阵对称性（近似）"""
        for src, neighbors in v.items():
            for dst, minutes in neighbors.items():
                if minutes <= 0:
                    raise ValueError(
                        f"移动耗时必须 > 0: {src} -> {dst} = {minutes}"
                    )
        return v

    def get_travel_time(self, from_scene: str, to_scene: str) -> int | None:
        """获取两场景间的移动耗时

        Args:
            from_scene: 起始场景 ID
            to_scene: 目标场景 ID

        Returns:
            移动耗时（分钟），不可达返回 None
        """
        return self.adjacency.get(from_scene, {}).get(to_scene)

    def get_neighbors(self, scene: str) -> dict[str, int]:
        """获取场景的所有邻居"""
        return self.adjacency.get(scene, {})


class SceneRuntimeState(BaseModel):
    """场景运行时状态（Redis 缓存）"""

    scene_id: str
    is_open: bool = True
    current_count: int = 0
    crowdedness: float = Field(default=0.0, ge=0.0, le=1.0, description="拥挤度 0-1")
    present_characters: list[str] = Field(default_factory=list, description="在场角色 ID")
    active_events: list[str] = Field(default_factory=list, description="当前事件")

    model_config = ConfigDict(extra="forbid")
