"""反思 Repository - 角色高层认知归纳的写入与查询

反思由反思系统定期从记忆片段中提炼生成，影响角色长期行为。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.config import settings
from src.db.models import Reflection
from src.db.repositories.base import BaseRepository

logger = get_logger()


class ReflectionRepository(BaseRepository[Reflection]):
    """反思 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Reflection)

    async def add(self, obj: Reflection) -> Reflection:
        """写入一条反思"""
        self.session.add(obj)
        await self.session.flush()
        logger.info(
            "reflection_created",
            character_id=str(obj.character_id),
            related_count=0,
        )
        return obj

    async def get_by_character(self, character_id: UUID, limit: int = 10) -> list[Reflection]:
        """获取角色反思记录（元反思优先，其余按创建时间倒序，默认 10 条）"""
        stmt = (
            select(Reflection)
            .where(Reflection.character_id == character_id)
            .order_by(Reflection.tier.desc(), Reflection.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_recent(self, character_id: UUID, since: datetime, tier: int | None = None) -> int:
        """统计 since 之后的反思条数（tier=None 不限层级）"""
        conditions = [Reflection.character_id == character_id, Reflection.created_at >= since]
        if tier is not None:
            conditions.append(Reflection.tier == tier)
        stmt = select(func.count()).select_from(Reflection).where(*conditions)
        return int(await self.session.scalar(stmt) or 0)

    async def get_recent_contents(self, character_id: UUID, limit: int = 10, max_tier: int = 1) -> list[str]:
        """取最近若干条 tier<=max_tier 的反思正文（元反思原料）"""
        stmt = (
            select(Reflection.content)
            .where(Reflection.character_id == character_id, Reflection.tier <= max_tier)
            .order_by(Reflection.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def search_semantic(self, character_id: UUID, query_vec: list[float], limit: int = 5) -> list[Reflection]:
        """语义检索角色反思（HNSW 余弦近邻，距离升序即相似度降序）

        - WHERE character_id 过滤 + embedding IS NOT NULL（生成失败的降级行不参与）
        - SET LOCAL hnsw.ef_search 提升召回质量（与 memory_repo.search_hybrid
          同一 raw-connection 模式；SET LOCAL 必须与查询在同一事务内）
        """
        connection = await self.session.connection()
        raw_conn = await connection.get_raw_connection()
        dbapi_conn = raw_conn.driver_connection
        assert dbapi_conn is not None
        # 事务内生效；int 插值防注入
        await dbapi_conn.execute(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")

        stmt = (
            select(Reflection)
            .where(
                Reflection.character_id == character_id,
                Reflection.embedding.is_not(None),
            )
            .order_by(Reflection.embedding.cosine_distance(query_vec))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())
