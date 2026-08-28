"""世界域服务：World 状态/事件查询编排（P-2：api/world.py 内联编排下沉）

将「Redis world:state 读取 + 兼容解析 + 活跃角色数」「世界事件查询 + 序列化」
从路由层下沉，路由退化为参数校验与响应组装。无 HTTP 概念。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import WorldEvent
from src.db.repositories import CharacterRepository, WorldEventRepository
from src.runtime import get_redis, get_world_engine

logger = get_logger(__name__)


class WorldService:
    """世界域服务：Redis/DB 编排 + 响应序列化"""

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _parse_world_time(raw: str) -> str:
        """兼容历史数据：world_time 可能被 JSON 序列化过两次"""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, str):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return raw

    async def get_world_state(self) -> dict[str, Any] | None:
        """世界当前状态（Redis 真相源 + PG 活跃角色数镜像）

        Returns:
            状态 dict；Redis 未连接返回 None（503 语义由路由层决定）
        """
        redis = get_redis()
        if not redis:
            return None

        state = await redis.hgetall("world:state")
        tick_id = int(state.get("tick_id", 0)) if state.get("tick_id") else 0
        world_time_raw = str(state.get("world_time", ""))
        weather = str(state.get("weather", "sunny"))
        temperature = state.get("temperature")

        active_characters = 0
        try:
            repo = CharacterRepository(self._session)
            active_characters = len(await repo.get_active_characters())
        except Exception as e:
            logger.warning("world_state_active_characters_failed", error=str(e))

        return {
            "tick_id": tick_id,
            "world_time": self._parse_world_time(world_time_raw),
            "weather": weather,
            "temperature": int(temperature) if temperature is not None else None,
            "active_characters": active_characters,
        }

    async def get_world_events(self, tick_id: int) -> list[dict[str, Any]] | None:
        """指定 Tick 的世界事件；无事件返回 None（404 语义由路由层决定）"""
        repo = WorldEventRepository(self._session)
        events = await repo.get_by_tick(tick_id)
        if not events:
            return None
        return [
            {
                "event_type": e.event_type,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]

    async def get_world_events_range(
        self,
        start_tick: int,
        end_tick: int,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Tick 区间世界事件（事件时间线）"""
        if end_tick == 0:
            world_engine = get_world_engine()
            if world_engine:
                end_tick = world_engine.tick_id

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

        result = await self._session.execute(stmt)
        events = list(result.scalars())
        return [
            {
                "id": str(e.id),
                "tick_id": e.tick_id,
                "event_type": e.event_type,
                "event_key": e.event_key,
                "payload": e.payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
