"""记忆 Repository - ORM + 原生 SQL 混合策略

设计要点：
- 常规增删改查使用 SQLAlchemy 2.0 ORM（保持类型安全与可组合性）
- 向量混合检索使用原生 SQL（text()），充分利用 pgvector HNSW 索引与
  重要性/时间衰减的混合排序能力，这是 ORM 难以表达的关键路径

⚠️ 性能优化（0002_optimize 迁移后）：
- memory_episodes 已按 character_id HASH 分区（16 分区）
- 查询 WHERE character_id = :cid 会触发分区裁剪，仅搜索单分区
- HNSW 索引在父表创建，自动传播到所有子分区（含未来新增）
- materialized 标志区分原始日志与向量化记忆

⚠️ 引用完整性（v4 修复）：
- memory_episodes.character_id 已建立外键 REFERENCES characters(id) ON DELETE CASCADE
- PostgreSQL 11+ 支持分区表引用非分区表，无需应用层兜底
- 角色删除时记忆数据自动级联清理

混合排序公式：
    final_score = sim_score * 0.6 + importance * 0.05 - time_decay
详见 architecture.md §5.7
"""
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import MemoryEpisode
from src.db.repositories.base import BaseRepository

logger = get_logger()


class MemoryRepository(BaseRepository[MemoryEpisode]):
    """记忆 Repository - ORM + 原生 SQL 混合策略"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, MemoryEpisode)

    async def add(self, obj: MemoryEpisode) -> MemoryEpisode:
        """添加记忆（ORM）

        ⚠️ 新增记忆时 materialized=false，embedding=NULL。
        embedding 由异步 worker 批量生成，不阻塞 Tick 循环。

        引用完整性由数据库外键保证（character_id REFERENCES characters.id ON DELETE CASCADE）。
        """
        self.session.add(obj)
        await self.session.flush()
        return obj

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

        利用 idx_mem_unmaterialized 部分索引（已排除 fail_count >= 5 的熔断记忆）。
        v4: 同时排除未到 next_retry_at 时间的记忆（指数退避）。
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.materialized.is_(False),
                MemoryEpisode.fail_count < 5,  # 跳过熔断记忆
                # v4: 仅拉取 next_retry_at 为 NULL（未失败过）或已到重试时间的记忆
                (MemoryEpisode.next_retry_at.is_(None))
                | (MemoryEpisode.next_retry_at <= now),
            )
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

        成功时清空 fail_count、last_error、next_retry_at。

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
            .values(
                embedding=embedding,
                materialized=True,
                fail_count=0,        # 成功后清空失败计数
                last_error=None,
                next_retry_at=None,  # v4: 清空重试时间
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def mark_embedding_failed(
        self,
        episode_id: UUID,
        character_id: UUID,
        error: str,
    ) -> None:
        """标记向量化失败（v3 新增，v4 增加指数退避）

        累加 fail_count，记录 last_error（截断 1000 字），
        并根据 fail_count 设置 next_retry_at（指数退避）。
        达到最大重试次数（5）后，由 fetch_unmaterialized 自动过滤。

        退避策略：
            retry 1 → 60s 后
            retry 2 → 180s 后
            retry 3 → 600s 后
            retry 4 → 1800s 后
            retry 5 → 熔断（不再重试）

        Args:
            episode_id: 记忆 ID
            character_id: 角色 ID（分区键，必须提供）
            error: 错误信息
        """
        from datetime import datetime, timedelta, timezone

        # 指数退避表（秒）：fail_count 累加后的值 → 等待秒数
        backoff_seconds = {1: 60, 2: 180, 3: 600, 4: 1800}

        truncated_error = error[:1000] if error else "unknown error"

        # 先读取当前 fail_count 以计算 next_retry_at
        stmt_select = select(MemoryEpisode.fail_count).where(
            MemoryEpisode.id == episode_id,
            MemoryEpisode.character_id == character_id,
        )
        result = await self.session.execute(stmt_select)
        current_fail_count = result.scalar_one()

        new_fail_count = current_fail_count + 1
        wait_seconds = backoff_seconds.get(new_fail_count, 0)
        next_retry = (
            datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
            if wait_seconds > 0
            else None
        )

        stmt = (
            update(MemoryEpisode)
            .where(
                MemoryEpisode.id == episode_id,
                MemoryEpisode.character_id == character_id,
            )
            .values(
                fail_count=new_fail_count,
                last_error=truncated_error,
                next_retry_at=next_retry,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()
        logger.warning(
            "embedding_marked_failed",
            episode_id=str(episode_id),
            character_id=str(character_id),
            error=truncated_error[:200],
            fail_count=new_fail_count,
            next_retry_at=next_retry.isoformat() if next_retry else None,
            circuit_broken=new_fail_count >= 5,
        )

    async def search_hybrid(
        self, character_id: UUID, query_vec: list[float], top_k: int = 10
    ) -> list[dict]:
        """混合检索（原生 SQL - HNSW + 重要性 + 时间衰减）

        ⚠️ 分区裁剪：WHERE character_id = $1 触发 HASH 分区裁剪，
        仅搜索单分区，HNSW 只扫描该角色的数据（< 10ms）。

        执行流程：
        1. SET LOCAL hnsw.ef_search = 100 —— 提升 HNSW 召回质量
        2. CTE candidates：先按向量距离召回 Top-K*2 候选，限定角色范围
        3. 计算 final_score = sim_score*0.6 + importance*0.05 + time_decay
           （time_decay = -距今天数*0.05，越久远扣分越多）
        4. 按 final_score 排序取 Top-K

        注意：
        - 使用 asyncpg 原生连接执行，避免 SQLAlchemy text() 与 :: 类型转换冲突
        - SET LOCAL 必须与查询在同一事务内执行
        - 仅检索 materialized=true 的记忆（embedding 已生成）
        """
        # 1. 获取底层 asyncpg 连接
        connection = await self.session.connection()
        raw_conn = await connection.get_raw_connection()
        dbapi_conn = raw_conn.driver_connection

        # 2. 设置 HNSW 检索参数（事务内生效）
        await dbapi_conn.execute("SET LOCAL hnsw.ef_search = 100")

        # 3. 向量召回 + 混合排序（使用 asyncpg 原生 $1 占位符）
        query_sql = """
            WITH candidates AS (
                SELECT id, content, importance, timestamp,
                       1 - (embedding <=> $2::halfvec) AS sim_score
                FROM memory_episodes
                WHERE character_id = $1 AND materialized = TRUE
                ORDER BY embedding <=> $2::halfvec
                LIMIT $3
            )
            SELECT id, content,
                   sim_score * 0.6 + importance * 0.05
                   - EXTRACT(EPOCH FROM (now() - timestamp)) / 86400.0 * 0.05 AS final_score
            FROM candidates
            ORDER BY final_score DESC
            LIMIT $4
        """
        vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
        result = await dbapi_conn.fetch(
            query_sql,
            character_id,
            vec_str,
            top_k * 2,
            top_k,
        )
        rows = [dict(row) for row in result]
        logger.info(
            "memory_search_hybrid",
            character_id=str(character_id),
            top_k=top_k,
            returned=len(rows),
        )
        return rows
