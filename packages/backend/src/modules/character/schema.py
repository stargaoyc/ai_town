"""角色卡 Schema 定义（Pydantic v2）

对应前端 Zod schema，用于运行时校验角色卡 YAML。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Schedule(str, Enum):
    """作息类型

    - EARLY_BIRD: 早睡早起，活动高峰 6:00-22:00
    - NORMAL: 正常作息，活动高峰 8:00-23:00
    - NIGHT_OWL: 熬夜型，活动高峰 10:00-02:00
    """

    EARLY_BIRD = "early_bird"
    NORMAL = "normal"
    NIGHT_OWL = "night_owl"


class InitialPlan(BaseModel):
    """初始计划"""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(description="计划类型：long_term / short_term / daily")
    title: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=1, le=5, description="优先级 1-5")


class InitialState(BaseModel):
    """初始状态"""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(default="home", description="初始场景 ID")
    stamina: int = Field(default=80, ge=0, le=100)
    satiety: int = Field(default=60, ge=0, le=100)
    mood: str = Field(default="calm")
    money: int = Field(default=500, ge=0)
    phone_battery: int = Field(default=75, ge=0, le=100)
    social_energy: int = Field(default=60, ge=0, le=100)


class CharacterCard(BaseModel):
    """角色卡 Schema

    对应 configs/characters/*.yaml 文件结构。
    所有字段都会被严格校验，未知字段会被拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, description="角色名")
    age: int | None = Field(default=None, ge=0, le=200)
    occupation: str | None = Field(default=None, max_length=100)
    personality: list[str] = Field(default_factory=list, description="性格标签")
    traits: dict[str, Any] = Field(default_factory=dict, description="特征字典")
    backstory: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    voice_preset: str | None = Field(default=None, max_length=100)
    initial_state: InitialState = Field(default_factory=InitialState)
    initial_plans: list[InitialPlan] = Field(default_factory=list)

    @field_validator("personality")
    @classmethod
    def validate_personality(cls, v: list[str]) -> list[str]:
        """性格标签不能为空字符串"""
        if any(not tag.strip() for tag in v):
            raise ValueError("性格标签不能为空字符串")
        return [tag.strip() for tag in v]

    @field_validator("traits")
    @classmethod
    def validate_traits(cls, v: dict[str, Any]) -> dict[str, Any]:
        """校验 traits 中的 schedule 字段"""
        if "schedule" in v:
            schedule = v["schedule"]
            if schedule not in {s.value for s in Schedule}:
                raise ValueError(
                    f"schedule 必须是 {[s.value for s in Schedule]} 之一，"
                    f"得到: {schedule}"
                )
        return v
