"""改写式记忆去重：memory_episodes.is_duplicate 标记列

EmbeddingWorker 向量化时与同角色近窗口记忆做余弦比对，
相似度 >= 阈值判定为改写式重复：置 is_duplicate=TRUE 且不落向量
（materialized=TRUE 防止 worker 重复拉取，检索/反思查询均排除）。

Revision ID: 0012_memory_dedup_flag
Revises: 0011_person_memory_entries
Create Date: 2026-08-24
"""

from alembic import op

revision = "0012_memory_dedup_flag"
down_revision = "0011_person_memory_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    op.execute("COMMENT ON COLUMN memory_episodes.is_duplicate IS '改写式重复标记（向量化时余弦比对判定）';")


def downgrade() -> None:
    op.execute("ALTER TABLE memory_episodes DROP COLUMN IF EXISTS is_duplicate;")
