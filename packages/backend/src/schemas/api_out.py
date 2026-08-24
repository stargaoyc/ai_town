"""API 响应模型 - 全域 Out 模型（类型收敛专项）

命名约定：<前端接口名>Out，前端经 components["schemas"]["XxxOut"] 引用。
字段形状以各端点实际返回 dict 为准（镜像 lib/api.ts 手写接口）；
嵌套载荷尚未稳定的以 dict[str, Any] 过渡，后续逐域收紧。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# ===== 角色（characters.py）=====


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
    personality: list[Any] | None = None
    traits: dict[str, Any] | None = None
    backstory: str | None = None
    is_active: bool


class CharacterDetailOut(BaseModel):
    character: CharacterDetail
    state: CharacterStateOut


class CharacterListItemOut(BaseModel):
    id: str
    name: str
    age: int | None = None
    occupation: str | None = None
    is_active: bool


class CharacterListOut(BaseModel):
    data: list[CharacterListItemOut]
    total: int


class ReflectionEntryOut(BaseModel):
    id: str
    content: str
    created_at: str


class ReflectionsListOut(BaseModel):
    data: list[ReflectionEntryOut]
    total: int


class PlanEntryOut(BaseModel):
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


class PlansListOut(BaseModel):
    data: list[PlanEntryOut]
    total: int


class ActionEntryOut(BaseModel):
    id: str
    action_id: str
    action_name: str
    params: dict[str, Any] | None = None
    reason: str | None = None
    result: Any | None = None
    duration_minutes: int
    location: str | None = None
    related_characters: list[Any] | None = None
    timestamp: str


class ActionsListOut(BaseModel):
    data: list[ActionEntryOut]
    total: int


class RelationEntryOut(BaseModel):
    target_id: str
    target_name: str | None = None
    relation_type: str | None = None
    relationship_type: str | None = None
    trust: int | None = None
    intimacy: int | None = None
    strength: int | None = None
    last_interaction_at: str | None = None
    notes: str | None = None


class RelationsListOut(BaseModel):
    data: list[RelationEntryOut]
    total: int


class NearbyCharacterEntryOut(BaseModel):
    id: str
    name: str
    personality: str | None = None
    mood: str | None = None
    current_action_name: str | None = None
    relationship_type: str | None = None
    strength: int | None = None
    location: str | None = None


class NearbyListOut(BaseModel):
    data: list[NearbyCharacterEntryOut]
    total: int
    location: str | None = None


class StateHistoryEntryOut(BaseModel):
    stamina: int
    satiety: int
    mood: str
    money: int
    phone_battery: int
    social_energy: int
    location: str
    updated_at: str


class StateHistoryListOut(BaseModel):
    data: list[StateHistoryEntryOut]
    total: int


# ===== 世界（world.py）=====


class WorldEventEntryOut(BaseModel):
    id: str
    tick_id: int
    event_type: str
    event_key: str | None = None
    payload: dict[str, Any] | None = None
    created_at: str | None = None


class WorldEventsRangeOut(BaseModel):
    data: list[WorldEventEntryOut]
    total: int


class WorldStateOut(BaseModel):
    tick_id: int
    world_time: str
    weather: str
    temperature: int | None = None
    active_characters: int


# ===== 系统（system.py）=====


class HealthOut(BaseModel):
    status: str
    world_tick: int
    redis: str
    must_modules: dict[str, bool]
    optional_modules: dict[str, bool]
    current_world_time: dict[str, Any] | None = None


class LoginOut(BaseModel):
    token: str
    user_id: str


class ModuleEntryOut(BaseModel):
    name: str
    type: str
    status: str
    description: str


class ModulesListOut(BaseModel):
    data: list[ModuleEntryOut]
    total: int


# ===== 消息（messages.py）=====


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    sender: str
    content: str
    tokens: int | None = None
    cost: float | None = None
    created_at: str


class MessagesListOut(BaseModel):
    data: list[MessageOut]
    total: int


class ConversationOut(BaseModel):
    id: str
    character_id: str
    user_id: str
    platform: str
    last_message_at: str


class ConversationsListOut(BaseModel):
    data: list[ConversationOut]
    total: int


class SendMessageDataOut(BaseModel):
    conversation_id: str
    message_id: str | None = None
    content: str
    tokens: int | None = None
    cost: float | None = None
    error: str | None = None


class SendMessageOut(BaseModel):
    data: SendMessageDataOut


class MessageStatsOut(BaseModel):
    total_messages: int
    total_tokens: int
    total_cost: float
    by_character: dict[str, Any] | None = None
    by_day: dict[str, Any] | None = None


# ===== 通知（notifications.py）=====


class AppNotificationOut(BaseModel):
    id: str
    type: str
    title: str
    content: str
    created_at: str
    read: bool


class NotificationsListOut(BaseModel):
    data: list[AppNotificationOut]
    total: int
    unread: int


class NotificationOut(BaseModel):
    data: AppNotificationOut


class SuccessIdOut(BaseModel):
    success: bool
    id: str


class SuccessUpdatedOut(BaseModel):
    success: bool
    updated: int


class SuccessOut(BaseModel):
    success: bool


# ===== 小镇（town.py）=====


class SceneOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    type: str | None = None
    capacity: int | None = None
    crowdedness: int | None = None
    characters_present: list[str] | None = None


class ScenesListOut(BaseModel):
    data: list[SceneOut]
    total: int


# ===== Action 定义（actions.py）=====


class ActionDefOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    category: str | None = None
    duration_minutes: int | None = None
    scene: str | None = None
    precondition_met: bool | None = None


class ActionDefsListOut(BaseModel):
    data: list[ActionDefOut]
    total: int


# ===== 管理（admin.py）=====


class OnebotMessageEntryOut(BaseModel):
    message_id: str
    conversation_id: str
    character_id: str
    user_id: str
    sender: str
    content: str
    tokens: int | None = None
    cost: float | None = None
    created_at: str


class OnebotMessagesListOut(BaseModel):
    data: list[OnebotMessageEntryOut]
    total: int


class ShareEntryOut(BaseModel):
    message_id: str
    conversation_id: str
    character_id: str | None = None
    character_name: str | None = None
    share_id: str | None = None
    sender: str
    content: str
    tokens: int | None = None
    cost: float | None = None
    created_at: str


class SharesListOut(BaseModel):
    data: list[ShareEntryOut]
    total: int


class VectorSearchResultOut(BaseModel):
    id: str
    content: str
    importance: int
    timestamp: str
    similarity: float
    is_reflected: bool
    source_type: str


class VectorSearchOut(BaseModel):
    data: list[VectorSearchResultOut]
    total: int
    query: str


class SnapshotEntryOut(BaseModel):
    id: str
    tick_id: int
    state: dict[str, Any] | None = None
    created_at: str


class SnapshotsListOut(BaseModel):
    data: list[SnapshotEntryOut]
    total: int


class LogEntryOut(BaseModel):
    model_config = {"extra": "allow"}

    timestamp: str | None = None
    level: str | None = None
    event: str | None = None


class LogsListOut(BaseModel):
    data: list[LogEntryOut]
    total: int
    source: str


class DeleteCharacterOut(BaseModel):
    success: bool
    message: str
    character_id: str
