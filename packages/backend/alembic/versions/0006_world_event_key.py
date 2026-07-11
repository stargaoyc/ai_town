"""world_events 增加 event_key 字段 + 幂等约束扩展

变更内容：
1. 新增 event_key 列（VARCHAR(100), DEFAULT 'default'）
2. 删除旧 UNIQUE(tick_id, event_type) 约束
3. 创建新 UNIQUE(tick_id, event_type, event_key) 约束
4. created_at 列类型改为 TIMESTAMPTZ（与其他表一致）

原因：支持同一 Tick 同一类型的多条事件（如不同场景各自一条 scene 事件），
event_key 区分不同实体，保证幂等性的同时支持细粒度事件拆分。

注意：降级脚本仅 raise RuntimeError，遵循"upgrade only"原则。
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_world_event_key"
down_revision = "0005_embedding_dim_2048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 新增 event_key 列（默认 'default'）
    op.execute(
        "ALTER TABLE world_events "
        "ADD COLUMN IF NOT EXISTS event_key VARCHAR(100) NOT NULL DEFAULT 'default'"
    )

    # 2. 删除旧唯一约束
    op.execute("ALTER TABLE world_events DROP CONSTRAINT IF EXISTS uq_world_events_tick_type")

    # 3. 创建新唯一约束（含 event_key）
    op.execute(
        "ALTER TABLE world_events "
        "ADD CONSTRAINT uq_world_events_tick_type_key "
        "UNIQUE (tick_id, event_type, event_key)"
    )

    # 4. created_at 改为 TIMESTAMPTZ（与其他表一致）
    op.execute(
        "ALTER TABLE world_events "
        "ALTER COLUMN created_at TYPE TIMESTAMPTZ "
        "USING created_at AT TIME ZONE 'UTC'"
    )

    # 5. 更新列注释
    op.execute("COMMENT ON COLUMN world_events.event_key IS '事件键（区分同 Tick 同类型不同实体，默认 default）'")


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Follow upgrade-only principle.")
