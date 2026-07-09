"""db/repositories 包初始化 - 导出所有 Repository

Repository 模式封装数据访问逻辑，业务层通过 Repository 操作数据库，
不直接接触 SQLAlchemy session 与语句细节。

用法示例：
    async with db.session() as session:
        char_repo = CharacterRepository(session)
        characters = await char_repo.get_active_characters()
"""
from src.db.repositories.action_repo import ActionRepository
from src.db.repositories.base import BaseRepository
from src.db.repositories.character_repo import CharacterRepository
from src.db.repositories.conversation_repo import ConversationRepository
from src.db.repositories.memory_repo import MemoryRepository
from src.db.repositories.message_repo import MessageRepository
from src.db.repositories.plan_repo import PlanRepository
from src.db.repositories.reflection_repo import ReflectionRepository
from src.db.repositories.relation_repo import RelationRepository
from src.db.repositories.snapshot_repo import WorldEventRepository, WorldSnapshotRepository

__all__ = [
    "BaseRepository",
    "CharacterRepository",
    "ActionRepository",
    "MemoryRepository",
    "PlanRepository",
    "RelationRepository",
    "ReflectionRepository",
    "WorldEventRepository",
    "WorldSnapshotRepository",
    "ConversationRepository",
    "MessageRepository",
]
