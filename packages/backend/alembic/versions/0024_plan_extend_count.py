"""plans 增加 extend_count 顺延计数列（R9 计划闭环防膨胀）

短期计划 deadline 已过时先尝试补救（顺延 deadline），而非一刀切过期。
extend_count 记录顺延次数，达到上限（plan_remedy_max_extends）后强制过期——
防止任务型计划被无限顺延、永不收敛（对应「计划不会越来越多」的硬约束）。

Revision ID: 0024_plan_extend_count
Revises: 0023_pmem_embedding
Create Date: 2026-09-02
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0024_plan_extend_count"
down_revision: str | None = "0023_pmem_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS extend_count INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS extend_count")
