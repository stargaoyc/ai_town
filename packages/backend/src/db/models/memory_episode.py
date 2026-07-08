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
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

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
        final_score = sim_score * 0.6 + importance * 0.05 + time_decay
    详见 architecture.md §5.7
    """
    __tablename__ = "memory_episodes"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="记忆 ID"
    )
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        primary_key=True, comment="所属角色（分区键，外键引用 characters.id）"
    )
    content: Mapped[str] = mapped_column(Text, comment="记忆内容（自然语言）")
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim), nullable=True,
        comment="向量嵌入（materialized=false 时为 NULL）"
    )
    importance: Mapped[int] = mapped_column(
        Integer, default=5, comment="重要性 1-10"
    )
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="发生时间"
    )
    action_id: Mapped[str | None] = mapped_column(String(100), comment="关联 Action")
    location: Mapped[str | None] = mapped_column(String(50), comment="发生场景")
    related_characters: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid), default=list, comment="相关角色 ID 列表"
    )
    is_reflected: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已被反思消化"
    )
    materialized: Mapped[bool] = mapped_column(
        Boolean, default=False,
        comment="embedding 是否已生成（异步 worker 处理）"
    )
    source_type: Mapped[str] = mapped_column(
        String(20), default="action", comment="来源类型"
    )

    __table_args__ = (
        # 角色记忆时间线查询
        Index("idx_mem_char_time", "character_id", "timestamp"),
        # 角色重要性排序
        Index("idx_mem_char_imp", "character_id", "importance"),
        # 部分索引：仅索引未反思的记忆，加速反思触发检查
        Index(
            "idx_mem_unreflected",
            "character_id",
            postgresql_where="is_reflected = FALSE",
        ),
        # 部分索引：未向量化的记忆，供 embedding worker 批量拉取
        Index(
            "idx_mem_unmaterialized",
            "timestamp",
            postgresql_where="materialized = FALSE",
        ),
    )
