"""related_characters 类型统一：action_records JSONB -> UUID[]（Schema 债 #14）

memory_episodes.related_characters 为 UUID[]，action_records 为 JSONB——
两表同语义字段类型割裂导致查询模式不统一。统一为 UUID[]：
- 类型安全（DB 层拒绝非法元素）
- 支持数组包含查询（@>）与 GIN 索引扩展

采用「加新列 -> 回填 -> 删旧列 -> 改名」的版本安全模式，
避免对分区表直接 ALTER COLUMN TYPE 的版本兼容性问题。

Revision ID: 0013_unify_related_chars
Revises: 0012_memory_dedup_flag
Create Date: 2026-08-24
"""

from alembic import op

revision = "0013_unify_related_chars"
down_revision = "0012_memory_dedup_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE action_records ADD COLUMN IF NOT EXISTS related_characters_new UUID[];"
    )
    # JSONB 数组元素为字符串形式的 UUID；空数组/NULL 均安全回填为空数组
    op.execute("""
        UPDATE action_records
        SET related_characters_new = COALESCE(
            ARRAY(SELECT jsonb_array_elements_text(related_characters)::uuid),
            ARRAY[]::uuid[]
        )
        WHERE related_characters IS NOT NULL;
    """)
    op.execute("UPDATE action_records SET related_characters_new = ARRAY[]::uuid[] WHERE related_characters IS NULL;")
    op.execute("ALTER TABLE action_records DROP COLUMN related_characters;")
    op.execute("ALTER TABLE action_records RENAME COLUMN related_characters_new TO related_characters;")
    op.execute(
        "COMMENT ON COLUMN action_records.related_characters IS "
        "'相关角色 ID 列表（与 memory_episodes.related_characters 同型：UUID[]）';"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE action_records ADD COLUMN IF NOT EXISTS related_characters_old JSONB;"
    )
    op.execute("""
        UPDATE action_records
        SET related_characters_old = to_jsonb(related_characters)
        WHERE related_characters IS NOT NULL;
    """)
    op.execute("ALTER TABLE action_records DROP COLUMN related_characters;")
    op.execute("ALTER TABLE action_records RENAME COLUMN related_characters_old TO related_characters;")
