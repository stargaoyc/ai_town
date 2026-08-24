"""世界域与系统域响应模型（对应 api/world.py、api/system.py）"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WorldStateOut(BaseModel):
    tick_id: int
    world_time: str
    weather: str
    temperature: int | None = None
    active_characters: int


class WorldEventOut(BaseModel):
    event_type: str
    payload: dict[str, Any] | None = None
    created_at: str


class WorldEventEntryOut(BaseModel):
    id: str
    tick_id: int
    event_type: str
    event_key: str | None = None
    payload: dict[str, Any] | None = None
    created_at: str | None = None


class WorldEventsOut(BaseModel):
    tick_id: int
    events: list[WorldEventOut]


class WorldEventsRangeOut(BaseModel):
    data: list[WorldEventEntryOut]
    total: int


class HealthOut(BaseModel):
    """健康检查（api/system.py /health）"""

    status: str  # ok | degraded
    world_tick: int
    redis: str  # connected | disconnected
    must_modules: dict[str, bool]
    optional_modules: dict[str, bool]
    current_world_time: dict[str, Any] | None = None
