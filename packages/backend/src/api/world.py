"""世界状态相关 API 路由

包含：
- 世界当前状态查询
- 单 Tick 世界事件查询
- Tick 区间世界事件查询（事件时间线）
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from structlog import get_logger

from src.db.models import WorldEvent
from src.db.repositories import CharacterRepository, WorldEventRepository
from src.db.session import db
from src.runtime import get_redis, get_world_engine

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["world"])


@router.get("/world")
async def get_world_state() -> dict[str, Any]:
    """获取世界状态

    Returns:
        世界当前状态（与前端 WorldState 接口对齐）
    """
    redis = get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    state = await redis.hgetall("world:state")
    tick_id = int(state.get("tick_id", 0)) if state.get("tick_id") else 0
    world_time_raw = str(state.get("world_time", ""))
    # 兼容历史数据：world_time 可能被 JSON 序列化过两次
    try:
        world_time = json.loads(world_time_raw)
        if not isinstance(world_time, str):
            world_time = world_time_raw
    except (json.JSONDecodeError, TypeError):
        world_time = world_time_raw
    weather = str(state.get("weather", "sunny"))
    temperature = state.get("temperature")

    # 查询活跃角色数
    active_characters = 0
    try:
        async with db.session() as session:
            repo = CharacterRepository(session)
            active_characters = len(await repo.get_active_characters())
    except Exception as e:
        logger.warning("world_state_active_characters_failed", error=str(e))

    return {
        "tick_id": tick_id,
        "world_time": world_time,
        "weather": weather,
        "temperature": int(temperature) if temperature is not None else None,
        "active_characters": active_characters,
    }


@router.get("/world/events/{tick_id}")
async def get_world_events(tick_id: int) -> dict[str, Any]:
    """获取指定 Tick 的世界事件

    Args:
        tick_id: Tick ID

    Returns:
        该 Tick 的所有世界事件（差分记录）
    """
    async with db.session() as session:
        repo = WorldEventRepository(session)
        events = await repo.get_by_tick(tick_id)

    if not events:
        raise HTTPException(status_code=404, detail="No events found for this tick")

    return {
        "tick_id": tick_id,
        "events": [
            {
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


@router.get("/world/events")
async def get_world_events_range(
    start_tick: int = 0,
    end_tick: int = 0,
    event_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """查询 Tick 区间内的所有世界事件（用于事件时间线）

    Args:
        start_tick: 起始 Tick（默认 0）
        end_tick: 结束 Tick（默认 0 表示当前 tick_id）
        event_type: 事件类型过滤（可选）
        limit: 返回数量上限

    Returns:
        世界事件列表（按 tick_id, created_at 排序）
    """
    world_engine = get_world_engine()
    if end_tick == 0 and world_engine:
        end_tick = world_engine.tick_id

    async with db.session() as session:
        stmt = (
            select(WorldEvent)
            .where(
                WorldEvent.tick_id >= start_tick,
                WorldEvent.tick_id <= end_tick,
            )
            .order_by(WorldEvent.tick_id, WorldEvent.created_at)
            .limit(limit)
        )
        if event_type:
            stmt = stmt.where(WorldEvent.event_type == event_type)

        result = await session.execute(stmt)
        events = list(result.scalars())

    return {
        "data": [
            {
                "id": str(e.id),
                "tick_id": e.tick_id,
                "event_type": e.event_type,
                "event_key": e.event_key,
                "payload": e.payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "total": len(events),
    }
