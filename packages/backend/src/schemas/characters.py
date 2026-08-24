"""角色域响应模型（对应 api/characters.py）"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CharacterItem(BaseModel):
    """角色列表项"""

    id: str
    name: str
    age: int | None = None
    occupation: str | None = None
    is_active: bool


class CharacterListOut(BaseModel):
    data: list[CharacterItem]
    total: int


class CharacterStateOut(BaseModel):
    location: str | None = None
    stamina: int | None = None
    satiety: int | None = None
    mood: str | None = None
    money: int | None = None
    phone_battery: int | None = None
    social_energy: int | None = None
    current_action: dict[str, Any] | None = None
    version: int | None = None


class CharacterDetail(BaseModel):
    id: str
    name: str
    age: int | None = None
    occupation: str | None = None
    personality: list[str] | None = None
    traits: dict[str, Any] | None = None
    backstory: str | None = None
    is_active: bool


class CharacterDetailOut(BaseModel):
    character: CharacterDetail
    state: CharacterStateOut


class ReflectionOut(BaseModel):
    id: str
    content: str
    created_at: str


class ReflectionsOut(BaseModel):
    data: list[ReflectionOut]
    total: int


class PlanOut(BaseModel):
    id: str
    type: str
    title: str
    description: str | None = None
    status: str
    priority: int
    progress: int
    deadline: str | None = None
    created_at: str
    updated_at: str | None = None


class PlansOut(BaseModel):
    data: list[PlanOut]
    total: int


class ActionRecordOut(BaseModel):
    id: str
    action_id: str
    action_name: str
    params: dict[str, Any] | None = None
    reason: str | None = None
    result: str | None = None
    duration_minutes: int
    location: str | None = None
    related_characters: list[Any] | None = None
    timestamp: str


class ActionsOut(BaseModel):
    data: list[ActionRecordOut]
    total: int


class ScheduleBlockOut(BaseModel):
    name: str
    start_hour: int
    end_hour: int
    activity_level: str | None = None


class ScheduleOut(BaseModel):
    character_id: str
    schedule_type: str
    blocks: list[ScheduleBlockOut] = []


class RelationOut(BaseModel):
    target_id: str
    target_name: str | None = None
    strength: int
    relationship_type: str


class RelationsOut(BaseModel):
    data: list[RelationOut]
    total: int


class NearbyCharacterOut(BaseModel):
    id: str
    name: str
    personality: str | None = None
    mood: str | None = None
    current_action_name: str | None = None
    relationship_type: str | None = None
    strength: int | None = None
    location: str | None = None


class NearbyOut(BaseModel):
    data: list[NearbyCharacterOut]
    total: int


class StateHistoryPointOut(BaseModel):
    """状态历史点（与端点实际返回对齐：时间戳字段为 updated_at）"""

    location: str | None = None
    stamina: int | None = None
    satiety: int | None = None
    money: int | None = None
    social_energy: int | None = None
    phone_battery: int | None = None
    mood: str | None = None
    action_id: str | None = None
    updated_at: str


class StateHistoryOut(BaseModel):
    data: list[StateHistoryPointOut]
    total: int
    source: str | None = None
