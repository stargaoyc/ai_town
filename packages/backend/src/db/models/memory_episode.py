"""记忆片段模型 - 含 pgvector 向量字段

存储角色的所有经历片段，是记忆系统的核心数据。
向量字段用于语义检索，importance + timestamp 用于混合排序。

⚠️ 性能优化（0002_optimize 迁移）：
- 表已改为按 character_id HASH 分区（16 分区，HASH 分区数固定，扩容需全表重分布）
- HNSW 索引在父表创建，PostgreSQL 自动传播到所有子分区（含未来新增）
- 查询 WHERE character_id = :cid 时分区裁剪，避免全局扫描
- materialized 标志区分原始日志与向量化记忆
- embedding 异步批量生成，不阻塞 Tick 循环
- character_id 外键引用 characters(id) ON DELETE CASCADE
  PostgreSQL 11+ 支持分区表引用非分区表，角色删除时记忆自动级联清理

⚠️ autovacuum 调优（0018 迁移，R5-M9）：本表是保留周期的删除热点，
各 HASH 子分区 vacuum/analyze scale factor 收紧为 0.05/0.02（父表不承载
reloption）；不设 fillfactor——追加写为主，无原地 UPDATE 热点。
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from src.config import settings
from src.db.base import Base


class MemoryEpisode(Base):
    """记忆片段表 - HASH 分区（16 分区）+ 父表 HNSW 索引

    设计要点：
    - 复合主键 (id, character_id)：分区表要求分区键在主键中
    - character_id: 外键引用 characters(id) ON DELETE CASCADE
    - embedding: nullable，materialized=false 时为 NULL（异步生成）
    - materialized: 是否已生成 embedding（worker 批量处理）
    - importance: 重要性评分（1-10），影响检索排序权重
    - is_reflected: 是否已被反思消化，部分索引优化未反思查询
    - source_type: 来源类型（action/conversation/reflection）

    检索策略（混合排序）：
        recency = exp(-距今天数/30)
        final_score = (sim_score * 0.6 + importance * 0.05) * (0.25 + 0.75 * recency)
    详见 architecture.md §5.7
    """

    __tablename__ = "memory_episodes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, comment="记忆 ID")
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True,
        comment="所属角色（分区键，外键引用 characters.id）",
    )
    content: Mapped[str] = mapped_column(Text, comment="记忆内容（自然语言）")
    embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(settings.embedding_dim),
        nullable=True,
        comment="向量嵌入（materialized=false 时为 NULL）",
    )
    importance: Mapped[int] = mapped_column(Integer, default=5, comment="重要性 1-10")
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="now()", comment="发生时间")
    # round-5 M2：归档行继承原事件 timestamp（仅展示/排序语义），保留期必须按
    # created_at 计龄——按事件时间计龄会让旧积压压缩出的归档生来即到期
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="入库时间（归档保留期计龄基准）"
    )
    action_id: Mapped[str | None] = mapped_column(String(100), comment="关联 Action")
    location: Mapped[str | None] = mapped_column(String(50), comment="发生场景")
    related_characters: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), default=list, comment="相关角色 ID 列表")
    is_reflected: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已被反思消化")
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="改写式重复标记（向量化时余弦比对判定，检索/反思排除）"
    )
    materialized: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="embedding 是否已生成（异步 worker 处理）"
    )
    # v3 迁移新增：向量化失败处理（最大重试 5 次后熔断）
    fail_count: Mapped[int] = mapped_column(Integer, default=0, comment="向量化失败次数，达到 5 后不再重试")
    last_error: Mapped[str | None] = mapped_column(Text, comment="最近一次失败错误信息（截断 1000 字）")
    # v4 迁移新增：下次可重试时间（指数退避），NULL 表示可立即重试或已成功
    next_retry_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), comment="下次可重试时间（指数退避），NULL 表示可立即重试"
    )
    source_type: Mapped[str] = mapped_column(String(20), default="action", comment="来源类型")

    __table_args__ = (
        # 角色记忆时间线查询
        Index("idx_mem_char_time", "character_id", "timestamp"),
        # 角色重要性排序
        Index("idx_mem_char_imp", "character_id", "importance"),
        # Round-3 M5：对齐 DDL 0002 的 GIN 索引（按相关角色查记忆），缺失会造成 ORM 元数据漂移
        Index("idx_mem_related", "related_characters", postgresql_using="gin"),
        # 部分索引：仅索引未反思的记忆，加速反思触发检查
        Index(
            "idx_mem_unreflected",
            "character_id",
            postgresql_where="is_reflected = FALSE",
        ),
        # 部分索引：未向量化的记忆，供 embedding worker 批量拉取
        # v4: 排除熔断记忆 + 按 next_retry_at 排序（指数退避）
        # round-6: 对齐 0002 DDL 以 timestamp 建列（fetch_unmaterialized 实际 ORDER BY timestamp）
        Index(
            "idx_mem_unmaterialized",
            "timestamp",
            postgresql_where="materialized = FALSE AND fail_count < 5",
        ),
        # 部分索引：保留周期查询（fetch_retention_candidates 跨角色过滤 importance<=6 的记忆）
        Index(
            "idx_mem_retention",
            "importance",
            "timestamp",
            postgresql_where="importance <= 6",
        ),
        # HNSW 向量索引（0002 迁移用原生 SQL 建在父表，自动传播到 16 个 HASH 子分区）
        # 声明在此处供 autogenerate 比对：缺声明会让 alembic 认为索引多余而生成 DROP，
        # 且 Base.metadata 与物理库不一致时无法用 metadata 校验结构。
        # 算子类是 halfvec_cosine_ops——与迁移保持一致，不能用默认的向量 L2 算子。
        Index(
            "idx_mem_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 128},
        ),
    )
