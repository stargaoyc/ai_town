"""角色卡导入器

从 YAML 文件加载角色卡，校验后写入 PG + Redis。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.character import Character, CharacterState
from src.db.models.plan import Plan
from src.modules.character.schema import CharacterCard

logger = structlog.get_logger(__name__)


class CharacterImporter:
    """角色卡导入器

    用法：
        importer = CharacterImporter(db_session, redis)
        character = await importer.import_from_file("configs/characters/yuina.yaml")

    流程：
        1. 读取 YAML 文件
        2. Pydantic 校验
        3. 写入 characters 表（角色档案）
        4. 写入 character_states 表（初始状态镜像）
        5. 写入 plans 表（初始计划）
        6. 写入 Redis（实时状态缓存）
    """

    def __init__(self, session: AsyncSession, redis: Redis):
        self.session = session
        self.redis = redis

    async def import_from_file(self, yaml_path: str | Path) -> Character:
        """从 YAML 文件导入角色卡

        Args:
            yaml_path: YAML 文件路径

        Returns:
            创建的 Character 对象（含 id）

        Raises:
            FileNotFoundError: 文件不存在
            ValidationError: 校验失败
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"角色卡文件不存在: {path}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return await self.import_from_dict(raw)

    async def import_from_dict(self, data: dict[str, Any]) -> Character:
        """从字典导入角色卡（已解析的 YAML）"""
        # 1. Pydantic 校验
        card = CharacterCard.model_validate(data)
        logger.info("角色卡校验通过: %s", card.name)

        # 2. 写入 characters 表
        # ⚠️ personality 列已在 0002_optimize 迁移中删除
        # 角色卡的 personality 字段合并到 traits.personality 中
        traits = dict(card.traits)
        if card.personality:
            traits["personality"] = card.personality

        character = Character(
            name=card.name,
            age=card.age,
            occupation=card.occupation,
            is_active=card.is_active,
            traits=traits,
            backstory=card.backstory,
            avatar_url=card.avatar_url,
            voice_preset=card.voice_preset,
        )
        self.session.add(character)
        await self.session.flush()  # 获取 id
        logger.info("角色已创建: id=%s, name=%s", character.id, character.name)

        # 3. 写入 character_states 表（PG 镜像）
        state = CharacterState(
            character_id=character.id,
            location=card.initial_state.location,
            stamina=card.initial_state.stamina,
            satiety=card.initial_state.satiety,
            mood=card.initial_state.mood,
            money=card.initial_state.money,
            phone_battery=card.initial_state.phone_battery,
            social_energy=card.initial_state.social_energy,
        )
        self.session.add(state)

        # 4. 写入初始计划
        for plan_data in card.initial_plans:
            plan = Plan(
                character_id=character.id,
                type=plan_data.type,
                title=plan_data.title,
                priority=plan_data.priority,
                status="active",
            )
            self.session.add(plan)

        await self.session.flush()

        # 5. 写入 Redis（实时状态缓存）
        await self._cache_state_to_redis(character.id, state)

        logger.info("角色导入完成: %s (%s)", card.name, character.id)
        return character

    async def update_from_dict(self, existing: Character, data: dict[str, Any]) -> Character:
        """更新已有角色卡（保留历史数据，仅更新角色档案和初始状态）

        Args:
            existing: 已有的 Character 实体
            data: 新的角色卡字典（已解析的 YAML）

        Returns:
            更新后的 Character 实体
        """
        card = CharacterCard.model_validate(data)
        logger.info("角色卡校验通过（更新模式）: %s", card.name)

        # 合并 personality 到 traits
        traits = dict(card.traits)
        if card.personality:
            traits["personality"] = card.personality

        # 更新 Character 基础字段
        existing.age = card.age
        existing.occupation = card.occupation
        existing.is_active = card.is_active
        existing.traits = traits
        existing.backstory = card.backstory
        existing.avatar_url = card.avatar_url
        existing.voice_preset = card.voice_preset
        self.session.add(existing)
        await self.session.flush()
        logger.info("角色已更新: id=%s, name=%s", existing.id, existing.name)

        # 更新 CharacterState（仅重置初始状态字段）
        from sqlalchemy import select

        result = await self.session.execute(select(CharacterState).where(CharacterState.character_id == existing.id))
        state = result.scalar_one_or_none()
        if state:
            state.location = card.initial_state.location
            state.stamina = card.initial_state.stamina
            state.satiety = card.initial_state.satiety
            state.mood = card.initial_state.mood
            state.money = card.initial_state.money
            state.phone_battery = card.initial_state.phone_battery
            state.social_energy = card.initial_state.social_energy
            self.session.add(state)
            await self.session.flush()
        else:
            # 状态记录不存在，创建新的
            state = CharacterState(
                character_id=existing.id,
                location=card.initial_state.location,
                stamina=card.initial_state.stamina,
                satiety=card.initial_state.satiety,
                mood=card.initial_state.mood,
                money=card.initial_state.money,
                phone_battery=card.initial_state.phone_battery,
                social_energy=card.initial_state.social_energy,
            )
            self.session.add(state)
            await self.session.flush()

        # 更新 Redis（实时状态缓存）
        await self._cache_state_to_redis(existing.id, state)

        logger.info("角色更新完成: %s (%s)", existing.name, existing.id)
        return existing

    async def _cache_state_to_redis(self, character_id: UUID, state: CharacterState) -> None:
        """将角色状态缓存到 Redis

        Redis 结构：
            char:{id}:state -> Hash, 字段对应 CharacterState
        """
        key = f"char:{character_id}:state"
        from src.core.state_codec import encode_state_value

        mapping = {
            "location": encode_state_value(state.location or "home"),
            "stamina": encode_state_value(state.stamina),
            "satiety": encode_state_value(state.satiety),
            "mood": encode_state_value(state.mood or "calm"),
            "money": encode_state_value(state.money),
            "phone_battery": encode_state_value(state.phone_battery),
            "social_energy": encode_state_value(state.social_energy),
        }
        await self.redis.hset(key, mapping=mapping)  # type: ignore[arg-type]
        logger.debug("Redis 状态缓存已更新: %s", key)

    async def import_directory(self, dir_path: str | Path) -> list[Character]:
        """批量导入目录下所有 YAML 角色卡

        Args:
            dir_path: 目录路径

        Returns:
            成功导入的 Character 列表
        """
        path = Path(dir_path)
        if not path.is_dir():
            raise NotADirectoryError(f"不是目录: {path}")

        characters: list[Character] = []
        yaml_files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))

        for yaml_file in yaml_files:
            try:
                character = await self.import_from_file(yaml_file)
                characters.append(character)
            except Exception as e:
                logger.error("导入角色卡失败 %s: %s", yaml_file, e)

        logger.info("批量导入完成: %d/%d 成功", len(characters), len(yaml_files))
        return characters
