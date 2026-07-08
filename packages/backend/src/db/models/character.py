"""角色与角色状态模型

- Character: 角色档案（静态属性，由角色卡导入）
- CharacterState: 角色实时状态（PG 镜像，Redis 为主）
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Character(Base):
    """角色档案表 - 存储角色的静态属性

    来源：角色卡 YAML 导入
    关联：character_states / action_records / memory_episodes / plans / relations

    ⚠️ personality 列已在 0002_optimize 迁移中删除：
    - 性格标签统一存储在 traits.personality（list[str]）中
    - 角色卡导入时 importer 会自动将 personality 合并到 traits
    """
    __tablename__ = "characters"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, comment="角色 ID（UUID v7）")
    name: Mapped[str] = mapped_column(String(100), comment="角色名")
    age: Mapped[int | None] = mapped_column(Integer, comment="年龄")
    occupation: Mapped[str | None] = mapped_column(String(100), comment="职业")
    traits: Mapped[dict] = mapped_column(
        JSONB, default=dict,
        comment="特征字典（personality/hobby/schedule/mbti 等）"
    )
    backstory: Mapped[str | None] = mapped_column(Text, comment="背景故事")
    avatar_url: Mapped[str | None] = mapped_column(String(500), comment="头像 URL")
    voice_preset: Mapped[str | None] = mapped_column(String(100), comment="语音预设")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否参与世界")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default="now()", comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default="now()", onupdate="now()", comment="更新时间（触发器自动维护）"
    )

    # 关联
    state: Mapped["CharacterState | None"] = relationship(back_populates="character", uselist=False)


class CharacterState(Base):
    """角色实时状态表 - PG 镜像（Redis 为主要读写源）

    字段说明：
    - stamina: 体力（0-100），影响可执行 Action
    - satiety: 饱腹度（0-100），低于阈值触发饥饿
    - mood: 情绪（happy/calm/sad/anxious/...）
    - money: 金钱（影响购物类 Action）
    - phone_battery: 手机电量（0-100）
    - social_energy: 社交能量（0-100），影响社交 Action
    - current_action: 当前正在执行的动作（JSON: {action_id, params, end_time}）
    """
    __tablename__ = "character_states"

    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
    location: Mapped[str | None] = mapped_column(String(50), comment="当前场景 ID")
    stamina: Mapped[int] = mapped_column(Integer, default=80, comment="体力 0-100")
    satiety: Mapped[int] = mapped_column(Integer, default=60, comment="饱腹度 0-100")
    mood: Mapped[str | None] = mapped_column(String(20), comment="情绪")
    money: Mapped[int] = mapped_column(Integer, default=500, comment="金钱")
    inventory: Mapped[dict] = mapped_column(JSONB, default=dict, comment="物品栏")
    current_action: Mapped[dict | None] = mapped_column(JSONB, comment="当前动作")
    phone_battery: Mapped[int] = mapped_column(Integer, default=75, comment="手机电量 0-100")
    social_energy: Mapped[int] = mapped_column(Integer, default=60, comment="社交能量 0-100")
    version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False,
        comment="乐观锁版本号（防止并发覆盖）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default="now()", onupdate="now()", comment="更新时间"
    )

    # 关联
    character: Mapped[Character] = relationship(back_populates="state")