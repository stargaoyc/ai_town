"""角色查询服务（P2-3：首个 Service 层示范组件）

把「角色详情 = 档案 + 实时状态」的跨表编排从 api/characters.py 下沉，
路由退回参数校验与响应组装。后续 characters/world 路由的内联查询
按同一模式逐步迁移。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Character, CharacterState
from src.db.repositories import CharacterRepository


class CharacterService:
    """角色领域服务：多 Repository 编排，无 HTTP 概念"""

    def __init__(self, session: AsyncSession):
        self._char_repo = CharacterRepository(session)

    @staticmethod
    def serialize_detail(character: Character, state: CharacterState) -> dict[str, Any]:
        """角色详情响应结构（档案 + 实时状态镜像）"""
        return {
            "character": {
                "id": str(character.id),
                "name": character.name,
                "age": character.age,
                "occupation": character.occupation,
                "personality": character.traits.get("personality", []),
                "traits": character.traits,
                "backstory": character.backstory,
                "is_active": character.is_active,
            },
            "state": {
                "location": state.location,
                "stamina": state.stamina,
                "satiety": state.satiety,
                "mood": state.mood,
                "money": state.money,
                "phone_battery": state.phone_battery,
                "social_energy": state.social_energy,
                "current_action": state.current_action,
                "version": state.version,
            },
        }

    async def get_character_detail(self, character_id: UUID) -> dict[str, Any] | None:
        """获取角色详情；不存在返回 None（404 语义由路由层决定）"""
        result = await self._char_repo.get_character_with_state(character_id)
        if not result:
            return None
        character, state = result
        return self.serialize_detail(character, state)
