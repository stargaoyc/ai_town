"""person_memory_entries 增加 embedding 语义向量列（审查 记忆-05）

此前该表用字符二元组重叠召回相关条目（person_memory_service.py），
无语义、对改写表达召回差——用户记忆恰恰是最需要准确召回的场景。
本迁移加 embedding halfvec 列 + HNSW 索引（与 memory_episodes 同型），
写入时由 person_memory_service 即时向量化。

Revision ID: 0023_pmem_embedding
Revises: 0022_plan_date
Create Date: 2026-08-28
"""
from collections.abc import Sequence

from alembic import op
from src.config import settings

revision: str = "0023_pmem_embedding"
down_revision: str | None = "0022_plan_date"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    dim = settings.embedding_dim
    op.execute(f"ALTER TABLE person_memory_entries ADD COLUMN IF NOT EXISTS embedding halfvec({dim})")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pmem_entries_embedding "
        "ON person_memory_entries USING hnsw (embedding halfvec_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pmem_entries_embedding")
    op.execute("ALTER TABLE person_memory_entries DROP COLUMN IF EXISTS embedding")