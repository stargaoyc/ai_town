"""PersonMemory 两层改造：append-only 事实条目表

- person_memory_entries：角色对用户的单条事实（对话中抽取），只追加不修改
- compacted 标记：已被合并进 person_memories.content 主档的条目置位，
  主档压缩后原始条目保留可追溯（软归档），检索默认只取未压缩的近期条目

Revision ID: 0011_person_memory_entries
Revises: 0010_reflection_tier_archive
Create Date: 2026-08-24
"""

from alembic import op

revision = "0011_person_memory_entries"
down_revision = "0010_reflection_tier_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS person_memory_entries (
            id UUID PRIMARY KEY,
            character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            user_id VARCHAR(100) NOT NULL,
            platform VARCHAR(20) NOT NULL DEFAULT 'web',
            content TEXT NOT NULL,
            compacted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pmem_entries_lookup "
        "ON person_memory_entries (character_id, user_id, compacted, created_at DESC);"
    )
    op.execute("COMMENT ON TABLE person_memory_entries IS 'Person Memory 事实条目层：只追加，定期压缩进主档';")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS person_memory_entries;")
