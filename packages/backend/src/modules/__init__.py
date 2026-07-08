"""Phase 2 模块包

包含：
- character: 角色卡导入器
- town: 小镇场景加载器
- schedule: 作息系统
- duration: 动态耗时系统
- movement: 移动系统
- relation: 角色关系图谱
"""
from src.modules.character import CharacterCard, CharacterImporter
from src.modules.duration import DurationCalculator
from src.modules.movement import MovementSystem
from src.modules.relation import RelationGraph
from src.modules.schedule import ScheduleSystem
from src.modules.town import SceneLoader

__all__ = [
    "CharacterCard",
    "CharacterImporter",
    "DurationCalculator",
    "MovementSystem",
    "RelationGraph",
    "ScheduleSystem",
    "SceneLoader",
]
