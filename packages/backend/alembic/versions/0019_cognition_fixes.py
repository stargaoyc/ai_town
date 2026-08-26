"""认知机制补强：反思 importance / 元反思溯源 / 记忆全文索引

- reflections.importance：检索配额内加权排序（P2-10）
- reflections.source_reflection_ids：元反思回挂 tier-1 来源（P1-11，
  reflection_sources 复合外键只能引用 memory_episodes，无法承载反思→反思）
- memory_episodes 内容 tsvector GIN 表达式索引（P2-16）：关键词检索
  不再完全依赖向量；'simple' 配置对中文按单字切分，覆盖字面匹配场景
- HNSW 索引不收缩（P1-1）由应用层周期 REINDEX CONCURRENTLY 治理，
  见 scheduler.loops.hnsw_reindex_loop，无需 DDL

Revision ID: 0019_cognition_fixes
Revises: 0018_mem_episode_autovacuum
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0019_cognition_fixes"
down_revision: str | None = "0018_mem_episode_autovacuum"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reflections",
        sa.Column(
            "importance",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
            comment="重要性 1-10（按支撑记忆数/主题数推导）",
        ),
    )
    op.add_column(
        "reflections",
        sa.Column(
            "source_reflection_ids",
            JSONB(),
            nullable=True,
            comment="元反思来源的 tier-1 反思 ID 列表（仅 tier=2 使用）",
        ),
    )

    # 表达式索引不能经 ORM Index 声明（ORM 元数据无该表达式列），原生 SQL 创建；
    # 父表创建自动传播到全部 HASH 子分区（与 HNSW 同机制）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_content_fts ON memory_episodes "
        "USING gin (to_tsvector('simple', content));"
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Use backup restore instead.")
