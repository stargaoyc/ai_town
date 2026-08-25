"""补建文档声称存在但从未创建的索引

data-model.md 声称 plans 有 idx_plans_char_status、reflections 有
idx_refl_char_time，但迁移链中从未创建（R4-M1 文档↔schema 漂移）。
plans 的 get_active_plans 每 Tick 每角色执行一次，是最热的缺失索引；
reflections 的 get_by_character 按 tier/created_at 排序同样无索引可用。

变更内容：
1. CREATE INDEX idx_plans_char_status ON plans (character_id, status)
2. CREATE INDEX idx_refl_char_time ON reflections (character_id, created_at DESC)

Revision ID: 0016_missing_doc_indexes
Revises: 0015_reflection_embedding
Create Date: 2026-08-25
"""

from alembic import op

revision = "0016_missing_doc_indexes"
down_revision = "0015_reflection_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_plans_char_status ON plans (character_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_refl_char_time ON reflections (character_id, created_at DESC)")


def downgrade() -> None:
    op.drop_index("idx_refl_char_time", table_name="reflections")
    op.drop_index("idx_plans_char_status", table_name="plans")
