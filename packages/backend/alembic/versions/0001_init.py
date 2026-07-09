"""初始化数据库：扩展 + 核心表

Revision ID: 0001_init
Revises:
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_uuidv7;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2. characters 表
    op.create_table(
        "characters",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("age", sa.Integer),
        sa.Column("occupation", sa.String(100)),
        sa.Column("personality", sa.JSONB),
        sa.Column("traits", sa.JSONB),
        sa.Column("backstory", sa.Text),
        sa.Column("avatar_url", sa.String(500)),
        sa.Column("voice_preset", sa.String(100)),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.TIMESTAMPTZ, server_default=sa.text("now()")),
    )

    # 3. character_states 表（PG 镜像）
    op.create_table(
        "character_states",
        sa.Column("character_id", sa.UUID, sa.ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("location", sa.String(50)),
        sa.Column("stamina", sa.Integer, default=80),
        sa.Column("satiety", sa.Integer, default=60),
        sa.Column("mood", sa.String(20)),
        sa.Column("money", sa.Integer, default=500),
        sa.Column("inventory", sa.JSONB),
        sa.Column("current_action", sa.JSONB),
        sa.Column("phone_battery", sa.Integer, default=75),
        sa.Column("social_energy", sa.Integer, default=60),
        sa.Column("updated_at", sa.TIMESTAMPTZ, server_default=sa.text("now()")),
    )

    # 4. action_records 表（按月 RANGE 分区）
    # ⚠️ v7 P0 修复：原 op.create_table() 创建的是普通堆表，无 PARTITION BY 声明，
    #    后续 CREATE TABLE ... PARTITION OF 会报 "relation is not partitioned" 错误。
    #    且原主键仅 id 单列，违反分区表"主键必须包含所有分区键"约束。
    #    改用原生 SQL 创建分区父表，复合主键 (id, timestamp)。
    op.execute("""
        CREATE TABLE action_records (
            id UUID NOT NULL DEFAULT uuidv7(),
            character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            action_id VARCHAR(100),
            action_name VARCHAR(100),
            params JSONB,
            reason TEXT,
            result TEXT,
            duration_minutes INT,
            location VARCHAR(50),
            related_characters JSONB,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, timestamp)              -- 分区表主键必须包含分区键
        ) PARTITION BY RANGE (timestamp);
    """)
    # 分区（按月）
    op.execute("""
        CREATE TABLE action_records_2026_07 PARTITION OF action_records
        FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
    """)
    # 默认分区
    op.execute("""
        CREATE TABLE action_records_default PARTITION OF action_records DEFAULT;
    """)
    op.create_index("idx_action_char_time", "action_records", ["character_id", sa.text("timestamp DESC")])

    # 5. memory_episodes 表（含向量）
    # ⚠️ v7 P0 修复：原 embedding 字段定义为 sa.Text，但 HNSW 索引使用
    #    vector_cosine_ops 操作符类，仅支持 vector 类型，TEXT 类型会导致
    #    索引创建失败。改用原生 SQL 声明 vector(1536) 类型。
    op.execute("""
        CREATE TABLE memory_episodes (
            id UUID NOT NULL DEFAULT uuidv7(),
            character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            content TEXT,
            embedding vector(1536),                 -- pgvector 向量类型（非 TEXT）
            importance INT DEFAULT 5,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
            action_id VARCHAR(100),
            location VARCHAR(50),
            related_characters JSONB,
            is_reflected BOOLEAN DEFAULT FALSE,
            source_type VARCHAR(20) DEFAULT 'action',
            PRIMARY KEY (id)
        );

        CREATE INDEX idx_mem_embedding_hnsw ON memory_episodes
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
    """)
    op.create_index("idx_mem_char_time", "memory_episodes", ["character_id", sa.text("timestamp DESC")])
    op.create_index("idx_mem_unreflected", "memory_episodes", ["character_id"], postgresql_where=sa.text("is_reflected = FALSE"))

    # 6. plans 表
    op.create_table(
        "plans",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("character_id", sa.UUID, sa.ForeignKey("characters.id", ondelete="CASCADE")),
        sa.Column("type", sa.String(20)),
        sa.Column("title", sa.String(200)),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), default="active"),
        sa.Column("priority", sa.Integer, default=3),
        sa.Column("deadline", sa.TIMESTAMPTZ),
        sa.Column("progress", sa.Integer, default=0),
        sa.Column("created_at", sa.TIMESTAMPTZ, server_default=sa.text("now()")),
    )

    # 7. relations 表
    op.create_table(
        "relations",
        sa.Column("character_id", sa.UUID, sa.ForeignKey("characters.id", ondelete="CASCADE")),
        sa.Column("target_id", sa.UUID, sa.ForeignKey("characters.id", ondelete="CASCADE")),
        sa.Column("strength", sa.Integer, default=20),
        sa.Column("relationship_type", sa.String(30)),
        sa.Column("last_interaction_at", sa.TIMESTAMPTZ),
        sa.Column("notes", sa.Text),
        sa.PrimaryKeyConstraint("character_id", "target_id"),
    )

    # 8. reflections 表
    op.create_table(
        "reflections",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("character_id", sa.UUID, sa.ForeignKey("characters.id", ondelete="CASCADE")),
        sa.Column("content", sa.Text),
        sa.Column("related_episodes", sa.JSONB),
        sa.Column("created_at", sa.TIMESTAMPTZ, server_default=sa.text("now()")),
    )

    # 9. world_snapshots 表
    op.create_table(
        "world_snapshots",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("tick_id", sa.BigInteger),
        sa.Column("world_time", sa.TIMESTAMPTZ),
        sa.Column("weather", sa.String(20)),
        sa.Column("locations", sa.JSONB),
        sa.Column("resources", sa.JSONB),
        sa.Column("active_events", sa.JSONB),
        sa.Column("created_at", sa.TIMESTAMPTZ, server_default=sa.text("now()")),
    )
    op.create_index("idx_world_tick", "world_snapshots", ["tick_id"])


def downgrade() -> None:
    op.drop_table("world_snapshots")
    op.drop_table("reflections")
    op.drop_table("relations")
    op.drop_table("plans")
    op.execute("DROP INDEX IF EXISTS idx_mem_unreflected;")
    op.execute("DROP INDEX IF EXISTS idx_mem_char_time;")
    op.execute("DROP INDEX IF EXISTS idx_mem_embedding_hnsw;")
    op.drop_table("memory_episodes")
    op.execute("DROP TABLE IF EXISTS action_records_default;")
    op.execute("DROP TABLE IF EXISTS action_records_2026_07;")
    op.drop_index("idx_action_char_time")
    op.drop_table("action_records")
    op.drop_table("character_states")
    op.drop_table("characters")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
    op.execute("DROP EXTENSION IF EXISTS vector;")
    op.execute("DROP EXTENSION IF EXISTS pg_uuidv7;")