"""记忆 Repository - ORM + 原生 SQL 混合策略

设计要点：
- 常规增删改查使用 SQLAlchemy 2.0 ORM（保持类型安全与可组合性）
- 向量混合检索使用原生 SQL（text()），充分利用 pgvector HNSW 索引与
  重要性/时间衰减的混合排序能力，这是 ORM 难以表达的关键路径

⚠️ 性能优化（0002_optimize 迁移后）：
- memory_episodes 已按 character_id HASH 分区（16 分区，2 的幂）
- 查询 WHERE character_id = :cid 会触发分区裁剪，仅搜索单分区
- HNSW 索引在父表创建，自动传播到所有子分区（含未来新增）
- materialized 标志区分原始日志与向量化记忆

⚠️ 引用完整性（v3）：
- memory_episodes 是分区表，无法建立外键引用 characters 表
- 应用层 Repository 在 create 时显式校验 character_id 存在性
- 防止孤儿记忆数据（应用层 Bug / 手动修复 / 批量删除角色场景）

混合排序公式：
    final_score = sim_score * 0.6 + importance * 0.05 - time_decay
详见 architecture.md §5.7
"""
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Character, MemoryEpisode
from src.db.repositories.base import BaseRepository

logger = get_logger()


class MemoryRepository(BaseRepository[MemoryEpisode]):
    """记忆 Repository - ORM + 原生 SQL 混合策略"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, MemoryEpisode)

    async def add(self, episode: MemoryEpisode) -> MemoryEpisode:
        """添加记忆（ORM）

        ⚠️ 新增记忆时 materialized=false，embedding=NULL。
        embedding 由异步 worker 批量生成，不阻塞 Tick 循环。

        ⚠️ 引用完整性校验：memory_episodes 是分区表，无法建立外键
        引用 characters 表。此处显式校验 character_id 是否存在，
        防止孤儿记忆数据。
        """
        # 校验 character_id 存在性（分区表无外键，应用层兜底）
        exists = await self.session.scalar(
            select(func.count())
            .select_from(Character)
            .where(Character.id == episode.character_id)
        )
        if not exists:
            raise ValueError(
                f"character_id {episode.character_id} does not exist, "
                f"cannot create memory episode (orphan data prevention)"
            )

        self.session.add(episode)
        await self.session.flush()
        return episode

    async def recent(
        self, character_id: UUID, limit: int = 50
    ) -> list[MemoryEpisode]:
        """获取角色最近记忆（ORM，按时间倒序）"""
        stmt = (
            select(MemoryEpisode)
            .where(MemoryEpisode.character_id == character_id)
            .order_by(MemoryEpisode.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_unreflected(self, character_id: UUID) -> int:
        """统计角色未反思记忆数（ORM，利用 idx_mem_unreflected 部分索引）"""
        stmt = (
            select(func.count())
            .select_from(MemoryEpisode)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.is_reflected.is_(False),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def mark_reflected(self, episode_ids: list[UUID]) -> None:
        """将指定记忆批量标记为已反思（ORM 批量 UPDATE）"""
        if not episode_ids:
            return
        stmt = (
            update(MemoryEpisode)
            .where(MemoryEpisode.id.in_(episode_ids))
            .values(is_reflected=True)
        )
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info(
            "memory_marked_reflected", count=len(episode_ids)
        )

    async def fetch_unmaterialized(
        self, limit: int = 100
    ) -> list[MemoryEpisode]:
        """拉取未向量化的记忆（供 embedding worker 异步处理）

        利用 idx_mem_unmaterialized 部分索引。
        """
        stmt = (
            select(MemoryEpisode)
            .where(MemoryEpisode.materialized.is_(False))
            .order_by(MemoryEpisode.timestamp)
            .limit(limit)
            .with_for_update(skip_locked=True)  # 跳过被锁的行，避免 worker 竞争
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_embedding(
        self,
        episode_id: UUID,
        character_id: UUID,
        embedding: list[float],
    ) -> None:
        """更新记忆的向量并标记为已 materialize

        Args:
            episode_id: 记忆 ID
            character_id: 角色 ID（分区键，必须提供）
            embedding: 向量
        """
        stmt = (
            update(MemoryEpisode)
            .where(
                MemoryEpisode.id == episode_id,
                MemoryEpisode.character_id == character_id,
            )
            .values(embedding=embedding, materialized=True)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def search_hybrid(
        self, character_id: UUID, query_vec: list[float], top_k: int = 10
    ) -> list[dict]:
        """混合检索（原生 SQL - HNSW + 重要性 + 时间衰减）

        ⚠️ 分区裁剪：WHERE character_id = :cid 触发 HASH 分区裁剪，
        仅搜索单分区，HNSW 只扫描该角色的数据（< 10ms）。

        执行流程：
        1. SET LOCAL hnsw.ef_search = 100 —— 提升 HNSW 召回质量
        2. CTE candidates：先按向量距离召回 Top-K*2 候选，限定角色范围
        3. 计算 final_score = sim_score*0.6 + importance*0.05 + time_decay
           （time_decay = -距今天数*0.05，越久远扣分越多）
        4. 按 final_score 排序取 Top-K

        注意：
        - SET LOCAL 必须与查询在同一事务内执行
        - ef_search=100（原 40），因分区后单分区数据量小，可适当提高召回
        - 仅检索 materialized=true 的记忆（embedding 已生成）
        """
        # 1. 设置 HNSW 检索参数（事务内生效）
        await self.session.execute(text("SET LOCAL hnsw.ef_search = 100"))

        # 2. 向量召回 + 混合排序（仅检索已向量化的记忆）
        stmt = text(
            """
            WITH candidates AS (
                SELECT id, content, importance, timestamp,
                       1 - (embedding <=> :q_vec) AS sim_score
                FROM memory_episodes
                WHERE character_id = :cid AND materialized = TRUE
                ORDER BY embedding <=> :q_vec
                LIMIT :limit
            )
            SELECT id, content,
                   sim_score * 0.6 + importance * 0.05
                   - EXTRACT(EPOCH FROM (now() - timestamp)) / 86400.0 * 0.05 AS final_score
            FROM candidates
            ORDER BY final_score DESC
            LIMIT :top_k;
            """
        )
        result = await self.session.execute(
            stmt,
            {
                "cid": character_id,
                "q_vec": str(query_vec),
                "limit": top_k * 2,
                "top_k": top_k,
            },
        )
        rows = [dict(row._mapping) for row in result]
        logger.info(
            "memory_search_hybrid",
            character_id=str(character_id),
            top_k=top_k,
            returned=len(rows),
        )
        return rows
