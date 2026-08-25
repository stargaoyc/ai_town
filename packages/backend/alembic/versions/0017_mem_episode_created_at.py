"""memory_episodes 新增 created_at：归档保留期按创建时间计龄

Round-5 M2：归档行（source_type='archive'）的 timestamp 继承自原事件
（episodes[-1].timestamp），而 run_cognition_retention_cycle 此前按 timestamp
删除超期归档——从 >365 天旧积压压缩出的归档在创建后一个保留周期内即被删除，
摘要从未被消费。不变量：归档保留期按创建时间计龄，不继承原事件时间戳。

变更内容：
1. ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()
   （PG11+ 元数据级变更，存量行瞬间填默认值）
2. 回填存量归档行 created_at = timestamp：保持其既有「等效年龄」，
   避免历史归档因迁移集体续命

Revision ID: 0017_mem_episode_created_at
Revises: 0016_missing_doc_indexes
Create Date: 2026-08-26
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_mem_episode_created_at"
down_revision = "0016_missing_doc_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_episodes",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="入库时间（归档保留期计龄基准）",
        ),
    )
    op.execute("UPDATE memory_episodes SET created_at = timestamp WHERE source_type = 'archive'")


def downgrade() -> None:
    op.drop_column("memory_episodes", "created_at")
