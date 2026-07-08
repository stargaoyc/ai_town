"""小镇模块

提供场景 YAML 加载、动态状态管理。
"""
from src.modules.town.loader import SceneLoader
from src.modules.town.schema import Scene, WorldMap

__all__ = ["Scene", "WorldMap", "SceneLoader"]
