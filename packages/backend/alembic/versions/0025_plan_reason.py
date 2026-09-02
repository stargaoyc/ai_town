"""plans 增加 reason 制定理由列（R9 计划闭环）

行动有 reason（action_records.reason），计划此前只有 description——
「为什么制定这个计划」的动机无独立字段。LLM 决策 createPlanChanges 与
daily_plan prompt 增加 reason 输出，auto_complete 匹配时计划 reason
与行动 reason 可做语义对照，决策时 LLM 也能看到计划的动机背景。

Revision ID: 0025_plan_reason
Revises: 0024_plan_extend_count
Create Date: 2026-09-02
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0025_plan_reason"
down_revision: str | None = "0024_plan_extend_count"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS reason TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS reason")
