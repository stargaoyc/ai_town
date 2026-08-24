"""认知深化：reflections 分层（tier）+ memory_episodes 归档枚举

- reflections.tier：1=批次主题反思，2=跨期元反思（对反思的反思）
- memory_episodes.source_type 扩展 'archive'：低价值老记忆压缩归档行，
  豁免 retention 删除（归档行本身已是压缩形态，量级极小）

Revision ID: 0010_reflection_tier_archive
Revises: 0009_gossip_source_type
Create Date: 2026-08-24
"""

from alembic import op

revision = "0010_reflection_tier_archive"
down_revision = "0009_gossip_source_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE reflections ADD COLUMN IF NOT EXISTS tier INTEGER NOT NULL DEFAULT 1;"
    )
    op.execute(
        "COMMENT ON COLUMN reflections.tier IS '反思层级：1=批次主题反思，2=跨期元反思';"
    )
    op.execute("ALTER TABLE memory_episodes DROP CONSTRAINT memory_episodes_source_type_check;")
    op.execute(
        "ALTER TABLE memory_episodes ADD CONSTRAINT memory_episodes_source_type_check "
        "CHECK (source_type IN ('action','conversation','reflection','event','gossip','archive'));"
    )
    op.execute(
        "COMMENT ON COLUMN memory_episodes.source_type IS "
        "'来源类型：action/conversation/reflection/event/gossip/archive（压缩归档）';"
    )


def downgrade() -> None:
    # tier 列回退前无需清理（默认 1 与旧行为一致），直接删除列
    op.execute("ALTER TABLE reflections DROP COLUMN IF EXISTS tier;")
    op.execute("DELETE FROM memory_episodes WHERE source_type = 'archive';")
    op.execute("ALTER TABLE memory_episodes DROP CONSTRAINT memory_episodes_source_type_check;")
    op.execute(
        "ALTER TABLE memory_episodes ADD CONSTRAINT memory_episodes_source_type_check "
        "CHECK (source_type IN ('action','conversation','reflection','event','gossip'));"
    )
