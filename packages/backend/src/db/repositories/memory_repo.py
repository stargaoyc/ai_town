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
    recency = exp(-距今天数/30)
    final_score = (sim_score * 0.6 + importance * 0.05) * (0.25 + 0.75 * recency)
详见 architecture.md §5.7
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.config import settings
from src.db.models import MemoryEpisode
from src.db.repositories.base import BaseRepository

logger = get_logger()

# 混合检索评分公式（R4-M8 单一真相源）：此前在 search_hybrid 与
# search_hybrid_global 双处硬编码，公式演进需同步两处否则「同一公式」
# 不变量静默破坏。sim_score/importance/timestamp 为外层 SELECT 的列名，
# 两处查询的候选 CTE 均以同名输出这些列，故可安全共享。
# GREATEST(0, ·) 钳制时钟回拨（round-3 L1）。
_HYBRID_SCORE_SQL = (
    "(sim_score * 0.6 + importance * 0.05)"
    " * (0.25 + 0.75 * exp(- GREATEST(0,"
    " EXTRACT(EPOCH FROM (now() - timestamp)) / 86400.0) / 30.0))"
)


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

    async def recent(self, character_id: UUID, limit: int = 50) -> list[MemoryEpisode]:
        """获取角色最近记忆（ORM，按时间倒序）"""
        stmt = (
            select(MemoryEpisode)
            .where(MemoryEpisode.character_id == character_id)
            .order_by(MemoryEpisode.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def search_shared_with(
        self,
        character_id: UUID,
        other_id: UUID,
        limit: int = 8,
    ) -> list[MemoryEpisode]:
        """检索与另一角色共同经历的记忆（related_characters 含对方）

        结构化相遇升级（"关系记忆注入"）：chat_with 对话上下文注入双方历史
        共同经历，实现「还记得上次…」。GIN 索引 idx_mem_related 覆盖
        related_characters 的 @> 查询（仅本角色分区内，无跨分区扫描）。
        按时间倒序取最近共同经历。
        """
        stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.related_characters.contains([other_id]),
            )
            .order_by(MemoryEpisode.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_by_character_and_time_range(
        self,
        character_id: UUID,
        start_date: datetime,
        end_date: datetime,
        limit: int = 100,
    ) -> list[MemoryEpisode]:
        """获取角色在指定时间范围内的记忆（按时间正序）

        用于日记生成等需要按时间段聚合记忆的场景。

        Args:
            character_id: 角色 ID
            start_date: 起始时间（包含）
            end_date: 结束时间（包含）
            limit: 返回数量上限
        """
        stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.timestamp >= start_date,
                MemoryEpisode.timestamp <= end_date,
            )
            .order_by(MemoryEpisode.timestamp.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def count_unreflected(self, character_id: UUID) -> int:
        """统计角色未反思记忆数（ORM，利用 idx_mem_unreflected 部分索引）

        Round-3 H2：必须与 fetch_unreflected 同口径排除 is_duplicate——
        mark_duplicate 此前不清 is_reflected，改写式重复会永久滞留在
        「未反思」计数里，一旦 ≥20 反思每个 Tick 都会触发。
        """
        stmt = (
            select(func.count())
            .select_from(MemoryEpisode)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.is_reflected.is_(False),
                MemoryEpisode.is_duplicate.is_(False),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def fetch_unreflected(self, character_id: UUID, limit: int = 20) -> list[MemoryEpisode]:
        """获取角色未反思的记忆（重要性降序优先，时间升序次之）

        P2-9：此前纯时间正序 FIFO，池窗口固定 30 条会截断跨月长程主题——
        高重要性旧事件先入池，保证跨期归纳不被近期流水淹没。
        利用 idx_mem_unreflected 部分索引加速查询。
        """
        stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.is_reflected.is_(False),
                MemoryEpisode.is_duplicate.is_(False),
            )
            .order_by(MemoryEpisode.importance.desc(), MemoryEpisode.timestamp.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def find_paraphrase_duplicate(
        self,
        character_id: UUID,
        embedding: list[float],
        before_ts: datetime,
        window_hours: int = 24,
        similarity_threshold: float = 0.95,
    ) -> bool:
        """向量化时改写式去重：与同角色近窗口已向量化记忆做余弦比对

        相似度 >= threshold 判定为改写式重复（复审 N7 的正确实现路径——
        pg_trgm 对中文无效，向量比对才是可靠信号）。

        Round-3 M3：距离过滤改为「ORDER BY 距离 LIMIT 1 后在应用层比对阈值」。
        HNSW 只加速有序 Top-K 查询，原先 WHERE distance <= x 无排序会让
        planner 退化为对窗口的顺序扫描；布尔语义不变——
        「存在任一行 ≤ 阈值」⟺「最近邻 ≤ 阈值」。
        """
        since = before_ts - timedelta(hours=window_hours)
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        distance_limit = 1.0 - similarity_threshold
        connection = await self.session.connection()
        raw_conn = await connection.get_raw_connection()
        dbapi_conn = raw_conn.driver_connection
        assert dbapi_conn is not None
        row = await dbapi_conn.fetchrow(
            """
            SELECT id, (embedding <=> $4::halfvec) AS dist FROM memory_episodes
            WHERE character_id = $1
              AND materialized = TRUE
              AND is_duplicate = FALSE
              AND embedding IS NOT NULL
              AND timestamp >= $2
              AND timestamp <= $3
            ORDER BY embedding <=> $4::halfvec
            LIMIT 1
            """,
            character_id,
            since,
            before_ts,
            vec_str,
        )
        if row is None:
            return False
        return float(row["dist"]) <= distance_limit

    async def mark_duplicate(self, episode_id: UUID, character_id: UUID) -> None:
        """标记为改写式重复：不落向量，materialized 置位防止 worker 重复拉取

        Round-3 H2：同时置 is_reflected=True——重复记忆不应进入反思池，
        否则 count_unreflected 会把它们永久计入，反思被幻影计数反复触发。
        """
        stmt = (
            update(MemoryEpisode)
            .where(MemoryEpisode.id == episode_id, MemoryEpisode.character_id == character_id)
            .values(is_duplicate=True, is_reflected=True, materialized=True, embedding=None)
        )
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info("memory_marked_duplicate", episode_id=str(episode_id))

    async def fetch_recent_gossip(self, character_id: UUID, hours: int = 24, limit: int = 2) -> list[str]:
        """取角色最近听说的传闻内容（供决策 Prompt 社交话题提示）"""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(MemoryEpisode.content)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.source_type == "gossip",
                MemoryEpisode.timestamp >= cutoff,
            )
            .order_by(MemoryEpisode.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def fetch_retention_candidates(
        self,
        low_cutoff: datetime,
        mid_cutoff: datetime,
        limit: int = 300,
    ) -> list[MemoryEpisode]:
        """拉取达到删除标准的记忆（压缩归档候选，跨角色）

        - importance<=3 且早于 low_cutoff（默认 90 天）
        - importance 4-6 且早于 mid_cutoff（默认 180 天）
        - 归档行（source_type='archive'）豁免：其本身已是压缩形态
        """
        stmt = (
            select(MemoryEpisode)
            .where(
                or_(
                    and_(
                        MemoryEpisode.importance <= 3,
                        MemoryEpisode.timestamp < low_cutoff,
                    ),
                    and_(
                        MemoryEpisode.importance >= 4,
                        MemoryEpisode.importance <= 6,
                        MemoryEpisode.timestamp < mid_cutoff,
                    ),
                ),
                MemoryEpisode.source_type != "archive",
            )
            .order_by(MemoryEpisode.timestamp.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def delete_by_ids(self, episode_ids: list[UUID]) -> None:
        """按 ID 批量删除记忆（压缩归档成功后清理原始行）"""
        if not episode_ids:
            return
        stmt = delete(MemoryEpisode).where(MemoryEpisode.id.in_(episode_ids))
        await self.session.execute(stmt)
        await self.session.flush()

    async def mark_reflected(self, episode_ids: list[UUID]) -> None:
        """将指定记忆批量标记为已反思（ORM 批量 UPDATE）"""
        if not episode_ids:
            return
        stmt = update(MemoryEpisode).where(MemoryEpisode.id.in_(episode_ids)).values(is_reflected=True)
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info("memory_marked_reflected", count=len(episode_ids))

    async def exists_recent_duplicate(
        self,
        character_id: UUID,
        normalized_content: str,
        hours: int = 24,
    ) -> bool:
        """检查近 N 小时内是否已存在归一化后相同的记忆（写入去重）

        归一化规则与调用方一致：折叠全部空白字符为单个空格。
        命中返回 True，调用方跳过写入，抑制重复行为产生的重复记忆。

        已知局限：只能拦精确重复，拦不住改写式复述。曾试验 pg_trgm
        相似度补充，中文文本实测相似度过低（真实改写对仅 0.3-0.4）不可用；
        正确方案是 embedding worker 落向量后做余弦比对（待办，见复审文档 N7）。
        """
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(MemoryEpisode.id)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.timestamp >= cutoff,
                func.trim(func.regexp_replace(MemoryEpisode.content, r"\s+", " ", "g")) == normalized_content,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def fetch_unmaterialized(self, limit: int = 100) -> list[MemoryEpisode]:
        """拉取未向量化的记忆（供 embedding worker 异步处理）

        利用 idx_mem_unmaterialized 部分索引（已排除 fail_count >= 5 的熔断记忆）。
        v4: 同时排除未到 next_retry_at 时间的记忆（指数退避）。
        """
        from datetime import datetime

        now = datetime.now(UTC)
        stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.materialized.is_(False),
                MemoryEpisode.fail_count < 5,  # 跳过熔断记忆
                # v4: 仅拉取 next_retry_at 为 NULL（未失败过）或已到重试时间的记忆
                (MemoryEpisode.next_retry_at.is_(None)) | (MemoryEpisode.next_retry_at <= now),
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
                fail_count=0,  # 成功后清空失败计数
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
        from datetime import datetime, timedelta

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
        next_retry = datetime.now(UTC) + timedelta(seconds=wait_seconds) if wait_seconds > 0 else None

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

    async def search_hybrid(self, character_id: UUID, query_vec: list[float], top_k: int = 10) -> list[dict[str, Any]]:
        """混合检索（原生 SQL - HNSW + 重要性 + 时间衰减）

        ⚠️ 分区裁剪：WHERE character_id = $1 触发 HASH 分区裁剪，
        仅搜索单分区，HNSW 只扫描该角色的数据（< 10ms）。

        执行流程：
        1. SET LOCAL hnsw.ef_search = 100 —— 提升 HNSW 召回质量
        2. CTE candidates：先按向量距离召回 Top-K*multiplier 候选（R6-L2：候选池
           放大倍数可配 RETRIEVAL_CANDIDATE_MULTIPLIER，默认 4），限定角色范围
        3. 计算 final_score：
           recency = exp(-距今天数/30)（指数衰减）
           final_score = (sim_score*0.6 + importance*0.05) * (0.25 + 0.75*recency)
           指数衰减使老记忆得分有 25% 下限、永不为负——重要事件数月后
           仍可被召回（原线性衰减在 22 天后使其不可达，见审查 §五-P0）
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
        assert dbapi_conn is not None

        # 2. 设置 HNSW 检索参数（事务内生效；int 插值防注入）
        await dbapi_conn.execute(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")

        # 3. 向量召回 + 混合排序（使用 asyncpg 原生 $1 占位符）
        query_sql = f"""
            WITH candidates AS (
                SELECT id, content, importance, timestamp, source_type, is_reflected,
                       1 - (embedding <=> $2::halfvec) AS sim_score
                FROM memory_episodes
                WHERE character_id = $1 AND materialized = TRUE
                  AND is_duplicate = FALSE AND embedding IS NOT NULL
                ORDER BY embedding <=> $2::halfvec
                LIMIT $3
            )
            SELECT id, content, importance, timestamp, source_type, is_reflected, sim_score,
                   {_HYBRID_SCORE_SQL} AS final_score
            FROM candidates
            ORDER BY final_score DESC
            LIMIT $4
        """
        # L1（round-3）：days 项必须钳到 >=0——时钟漂移/回拨产生的未来 timestamp
        # 会让 exp(+x) 突破衰减因子的 1.0 上限，未来记忆反而获得最高分
        vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
        result = await dbapi_conn.fetch(
            query_sql,
            character_id,
            vec_str,
            top_k * settings.retrieval_candidate_multiplier,
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

    async def search_hybrid_global(
        self,
        query_vec: list[float],
        top_k: int = 10,
        *,
        allow_cross_character: bool,
    ) -> list[dict[str, Any]]:
        """跨角色全局混合检索（无 character_id 谓词，探测全部 HASH 分区）

        与 search_hybrid 完全同一评分公式（含 GREATEST(0, ·) 时钟回拨钳制）；
        无分区键谓词时 planner 对每个分区的 HNSW 做有序扫描再 MergeAppend。
        JOIN characters 带出角色名，供管理端调试展示归属。

        ⚠️ 跨角色检索仅限管理面：调用方必须显式传 allow_cross_character=True
        声明范围扩张，并自行确保上游有 admin 鉴权（当前唯一调用方
        admin.vector_search 由 Admin RBAC 依赖守护）；缺省不传即 TypeError，
        防止未来非管理调用方无意识越过角色边界（round-5 review L8）。
        Tick 主流程必须走带角色过滤的 search_hybrid，
        避免跨角色记忆串扰污染决策上下文。
        """
        # 1. 获取底层 asyncpg 连接
        connection = await self.session.connection()
        raw_conn = await connection.get_raw_connection()
        dbapi_conn = raw_conn.driver_connection
        assert dbapi_conn is not None

        # 2. 设置 HNSW 检索参数（事务内生效；int 插值防注入）
        await dbapi_conn.execute(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")

        # 3. 向量召回 + 混合排序（评分公式与 search_hybrid 共享单一真相源）
        query_sql = f"""
            WITH candidates AS (
                SELECT m.id, m.character_id, c.name AS character_name,
                       m.content, m.importance, m.timestamp, m.source_type, m.is_reflected,
                       1 - (m.embedding <=> $1::halfvec) AS sim_score
                FROM memory_episodes m
                JOIN characters c ON c.id = m.character_id
                WHERE m.materialized = TRUE AND m.is_duplicate = FALSE
                  AND m.embedding IS NOT NULL
                ORDER BY m.embedding <=> $1::halfvec
                LIMIT $2
            )
            SELECT id, character_id, character_name, content, importance,
                   timestamp, source_type, is_reflected, sim_score,
                   {_HYBRID_SCORE_SQL} AS final_score
            FROM candidates
            ORDER BY final_score DESC
            LIMIT $3
        """
        vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
        result = await dbapi_conn.fetch(
            query_sql,
            vec_str,
            top_k * settings.retrieval_candidate_multiplier,
            top_k,
        )
        rows = [dict(row) for row in result]
        logger.info("memory_search_hybrid_global", top_k=top_k, returned=len(rows))
        return rows
