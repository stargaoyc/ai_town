"""角色 Repository - 封装角色档案与实时状态的查询/更新

Character 为静态档案，CharacterState 为 PG 镜像（Redis 为主）。
"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Character, CharacterState
from src.db.repositories.base import BaseRepository

logger = get_logger()


class CharacterRepository(BaseRepository[Character]):
    """角色档案与状态 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Character)

    async def get_active_characters(self) -> list[Character]:
        """获取所有参与世界（is_active=True）的角色"""
        stmt = select(Character).where(Character.is_active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_characters_by_location(
        self,
        location: str,
        exclude_id: UUID | None = None,
    ) -> list[tuple[Character, CharacterState]]:
        """查询同一场景中的所有活跃角色（用于多智能体交互感知）

        Args:
            location: 场景 ID
            exclude_id: 需排除的角色 ID（通常是感知方自己）

        Returns:
            [(Character, CharacterState), ...] 同场景其他角色列表
        """
        stmt = (
            select(Character, CharacterState)
            .join(CharacterState, CharacterState.character_id == Character.id)
            .where(
                Character.is_active.is_(True),
                CharacterState.location == location,
            )
        )
        if exclude_id is not None:
            stmt = stmt.where(Character.id != exclude_id)
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_character_with_state(self, character_id: UUID) -> tuple[Character, CharacterState] | None:
        """一次性获取角色档案与其实时状态（JOIN 查询）

        返回 (Character, CharacterState) 元组；角色或状态不存在时返回 None。
        """
        stmt = (
            select(Character, CharacterState)
            .join(CharacterState, CharacterState.character_id == Character.id)
            .where(Character.id == character_id)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        return row[0], row[1]

    async def update_state(self, character_id: UUID, **fields) -> None:
        """更新角色实时状态字段（任意合法列名通过关键字参数传入）"""
        if not fields:
            return
        stmt = update(CharacterState).where(CharacterState.character_id == character_id).values(**fields)
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info(
            "character_state_updated",
            character_id=str(character_id),
            fields=list(fields.keys()),
        )
