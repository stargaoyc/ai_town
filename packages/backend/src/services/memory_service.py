"""记忆域服务（round-7 E1：api/memory.py 内联编排下沉）

将「角色名查询」「Person Memory 列表」「记忆列表」的跨表编排与序列化
从路由层下沉，路由退化为参数校验与响应组装。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import MemoryRepository


class MemoryService:
    """记忆域服务：Repository 查询 + 响应序列化，无 HTTP 概念"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_character_name(self, character_id: UUID) -> str | None:
        """查询角色名（日记生成等场景需要真实角色名）"""
        result = await self._session.execute(
            text("SELECT name FROM characters WHERE id = :cid"),
            {"cid": str(character_id)},
        )
        row = result.fetchone()
        return str(row[0]) if row else None

    async def list_person_memories(self, character_id: UUID, limit: int) -> list[dict[str, Any]]:
        """角色对所有用户的记忆列表（按热度倒序）"""
        result = await self._session.execute(
            text(
                """
                SELECT id, character_id, user_id, platform, content,
                       heat, last_interaction_at, created_at, updated_at
                FROM person_memories
                WHERE character_id = :cid
                ORDER BY heat DESC, last_interaction_at DESC
                LIMIT :limit
                """
            ),
            {"cid": str(character_id), "limit": limit},
        )
        rows = [dict(r._mapping) for r in result]
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif isinstance(v, UUID):
                    r[k] = str(v)
        return rows

    async def list_recent_memories(self, character_id: UUID, limit: int) -> list[dict[str, Any]]:
        """角色最近记忆片段（响应结构序列化）"""
        episodes = await MemoryRepository(self._session).recent(character_id, limit)
        return [
            {
                "id": str(e.id),
                "content": e.content,
                "timestamp": e.timestamp.isoformat(),
                "importance": e.importance,
                "is_reflected": e.is_reflected,
            }
            for e in episodes
        ]
