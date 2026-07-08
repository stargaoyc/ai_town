"""db/models 包初始化 - 导出所有 ORM 模型

导入此包即注册所有模型到 Base.metadata，供 Alembic 自动检测变更。
"""
from src.db.models.character import Character, CharacterState
from src.db.models.action_record import ActionRecord
from src.db.models.memory_episode import MemoryEpisode
from src.db.models.plan import Plan
from src.db.models.relation import Relation
from src.db.models.reflection import Reflection
from src.db.models.reflection_source import ReflectionSource
from src.db.models.world_event import WorldEvent
from src.db.models.world_snapshot import WorldSnapshot
from src.db.models.conversation import Conversation, Message

__all__ = [
    "Character",
    "CharacterState",
    "ActionRecord",
    "MemoryEpisode",
    "Plan",
    "Relation",
    "Reflection",
    "ReflectionSource",
    "WorldEvent",
    "WorldSnapshot",
    "Conversation",
    "Message",
]
