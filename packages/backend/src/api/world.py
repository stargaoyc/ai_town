"""世界状态相关 API 路由

包含：
- 世界当前状态查询
- 单 Tick 世界事件查询
- Tick 区间世界事件查询（事件时间线）

业务编排见 src/services/world_service.py（P-2：内联编排下沉 Service）。
"""

from fastapi import APIRouter, HTTPException

from src.db.session import db
from src.schemas.world import (
    WorldEventEntryOut,
    WorldEventOut,
    WorldEventsOut,
    WorldEventsRangeOut,
    WorldStateOut,
)

router = APIRouter(prefix="/api/v1", tags=["world"])


@router.get("/world", response_model=WorldStateOut)
async def get_world_state() -> WorldStateOut:
    """获取世界状态

    Returns:
        世界当前状态（与前端 WorldState 接口对齐）
    """
    from src.services.world_service import WorldService

    async with db.session() as session:
        state = await WorldService(session).get_world_state()
    if state is None:
        raise HTTPException(status_code=503, detail="Redis not connected")
    return WorldStateOut(**state)


@router.get("/world/events/{tick_id}", response_model=WorldEventsOut)
async def get_world_events(tick_id: int) -> WorldEventsOut:
    """获取指定 Tick 的世界事件

    Args:
        tick_id: Tick ID

    Returns:
        该 Tick 的所有世界事件（差分记录）
    """
    async with db.session() as session:
        from src.services.world_service import WorldService

        events = await WorldService(session).get_world_events(tick_id)

    if events is None:
        raise HTTPException(status_code=404, detail="No events found for this tick")

    return WorldEventsOut(
        tick_id=tick_id,
        events=[WorldEventOut(**e) for e in events],
    )


@router.get("/world/events", response_model=WorldEventsRangeOut)
async def get_world_events_range(
    start_tick: int = 0,
    end_tick: int = 0,
    event_type: str | None = None,
    limit: int = 100,
) -> WorldEventsRangeOut:
    """查询 Tick 区间内的所有世界事件（用于事件时间线）

    Args:
        start_tick: 起始 Tick（默认 0）
        end_tick: 结束 Tick（默认 0 表示当前 tick_id）
        event_type: 事件类型过滤（可选）
        limit: 返回数量上限

    Returns:
        世界事件列表（按 tick_id, created_at 排序）
    """
    async with db.session() as session:
        from src.services.world_service import WorldService

        events = await WorldService(session).get_world_events_range(
            start_tick=start_tick,
            end_tick=end_tick,
            event_type=event_type,
            limit=limit,
        )

    return WorldEventsRangeOut(
        data=[WorldEventEntryOut(**e) for e in events],
        total=len(events),
    )
