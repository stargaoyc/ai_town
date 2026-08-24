"""角色 Repository - 封装角色档案与实时状态的查询/更新

Character 为静态档案，CharacterState 为 PG 镜像（Redis 为主）。
"""

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Character, CharacterState
from src.db.repositories.base import BaseRepository

if TYPE_CHECKING:
    from redis.asyncio import Redis

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

    async def get_all_states(self) -> list[CharacterState]:
        """获取所有角色的实时状态（启动回灌用，P0-3）"""
        stmt = select(CharacterState)
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

    async def get_state_version(self, character_id: UUID) -> int | None:
        """读取角色状态当前乐观锁版本；无状态行时返回 None"""
        stmt = select(CharacterState.version).where(CharacterState.character_id == character_id)
        version = await self.session.scalar(stmt)
        return None if version is None else int(version)

    async def update_state(
        self,
        character_id: UUID,
        *,
        expected_version: int | None = None,
        **fields: Any,
    ) -> bool:
        """更新角色实时状态字段（任意合法列名通过关键字参数传入）

        每次写入自动递增 version：version 列声明为乐观锁版本号但从未自增，
        前端与对账链路无法感知状态新鲜度（审查 §七-P1）。

        Args:
            expected_version: 乐观锁期望版本。传入时追加 ``WHERE version = :expected``
                条件更新，版本不匹配（Tick/API 并发写）则不写入并返回 False；
                None 保持无条件写入——Tick 主链路以 Redis 为真相源，无需 CAS。

        Returns:
            是否实际写入一行。
        """
        if not fields:
            return True
        fields.pop("version", None)
        stmt = update(CharacterState).where(CharacterState.character_id == character_id)
        if expected_version is not None:
            stmt = stmt.where(CharacterState.version == expected_version)
        result = cast(
            "CursorResult[Any]", await self.session.execute(stmt.values(version=CharacterState.version + 1, **fields))
        )
        await self.session.flush()
        if result.rowcount == 0:
            if expected_version is not None:
                logger.warning(
                    "character_state_cas_conflict",
                    character_id=str(character_id),
                    expected_version=expected_version,
                    fields=list(fields.keys()),
                )
            else:
                logger.info("character_state_row_missing", character_id=str(character_id))
            return False
        logger.info(
            "character_state_updated",
            character_id=str(character_id),
            fields=list(fields.keys()),
        )
        return True

    async def update_state_cas(self, character_id: UUID, *, max_attempts: int = 2, **fields: Any) -> bool:
        """带乐观锁重试的镜像写入：读当前版本 → 条件更新，冲突时重读再试

        供 API 侧低频写路径使用，缩小 Tick/API 并发写的 last-write-wins 窗口
        （审查二轮 N4）。全部尝试失败返回 False，镜像漂移由 reconcile 以
        Redis 为准修复。
        """
        for _ in range(max_attempts):
            version = await self.get_state_version(character_id)
            if version is None:
                # 无状态行无可比版本，退化为无条件写入（与历史行为一致）
                return await self.update_state(character_id, **fields)
            if await self.update_state(character_id, expected_version=version, **fields):
                return True
        return False

    async def get_by_name(self, name: str) -> Character | None:
        """按角色名查询角色（用于导入时同名冲突检测）"""
        stmt = select(Character).where(Character.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_character(
        self,
        character_id: UUID,
        redis: "Redis | None" = None,
    ) -> bool:
        """删除角色及其所有相关数据

        PG 删除依赖 ON DELETE CASCADE 自动清理：
        character_states / character_state_history / action_records / memory_episodes /
        reflections / reflection_sources / plans / person_memories /
        conversations→messages / relations / character_diaries

        若传入 redis，同时清理 Redis 中的 char:{id}:state 键。
        返回 True 表示已删除，False 表示角色不存在。
        """
        char = await self.session.get(Character, character_id)
        if char is None:
            return False

        name = char.name
        await self.session.execute(delete(Character).where(Character.id == character_id))
        await self.session.flush()

        if redis is not None:
            await redis.delete(f"char:{character_id}:state")

        logger.info(
            "character_deleted",
            character_id=str(character_id),
            name=name,
        )
        return True
