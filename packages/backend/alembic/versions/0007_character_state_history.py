"""新增 character_state_history 分区表

变更内容：
1. 创建 character_state_history 表（按月分区，记录角色状态历史快照）
2. 创建索引 idx_csh_char_time（character_id, recorded_at）
3. 更新 pre_create_partitions() 函数，新增 character_state_history 分区创建逻辑

原因：原 /api/v1/characters/{id}/state-history 端点查询 character_states 表，
该表仅存储当前状态（1 行/角色），导致前端状态趋势图只有一个点。
新增历史快照表后，每次角色状态更新都会写入一条快照，支持完整趋势曲线。

注意：降级脚本仅 raise RuntimeError，遵循"upgrade only"原则。
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_character_state_history"
down_revision = "0006_world_event_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 character_state_history 主表（按月分区）
    op.execute("""
        CREATE TABLE IF NOT EXISTS character_state_history (
            id UUID NOT NULL DEFAULT uuidv7(),
            character_id UUID NOT NULL,
            location VARCHAR(50),
            stamina INTEGER NOT NULL,
            satiety INTEGER NOT NULL,
            mood VARCHAR(20),
            money INTEGER NOT NULL,
            phone_battery INTEGER NOT NULL,
            social_energy INTEGER NOT NULL,
            action_id VARCHAR(100),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT csh_character_fk FOREIGN KEY (character_id)
                REFERENCES characters(id) ON DELETE CASCADE
        ) PARTITION BY RANGE (recorded_at)
    """)

    # 2. 创建默认分区
    op.execute("""
        CREATE TABLE IF NOT EXISTS character_state_history_default
        PARTITION OF character_state_history DEFAULT
    """)

    # 3. 创建索引（在主表上创建，会传播到所有分区）
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_csh_char_time
        ON character_state_history (character_id, recorded_at)
    """)

    # 4. 添加列注释
    op.execute("COMMENT ON TABLE character_state_history IS '角色状态历史快照表（按月分区），每次状态更新写入一条快照'")
    op.execute("COMMENT ON COLUMN character_state_history.id IS '记录 ID（UUID v7，时间有序）'")
    op.execute("COMMENT ON COLUMN character_state_history.character_id IS '角色 ID'")
    op.execute("COMMENT ON COLUMN character_state_history.location IS '当前场景 ID'")
    op.execute("COMMENT ON COLUMN character_state_history.stamina IS '体力 0-100'")
    op.execute("COMMENT ON COLUMN character_state_history.satiety IS '饱腹度 0-100'")
    op.execute("COMMENT ON COLUMN character_state_history.mood IS '情绪'")
    op.execute("COMMENT ON COLUMN character_state_history.money IS '金钱'")
    op.execute("COMMENT ON COLUMN character_state_history.phone_battery IS '手机电量 0-100'")
    op.execute("COMMENT ON COLUMN character_state_history.social_energy IS '社交能量 0-100'")
    op.execute("COMMENT ON COLUMN character_state_history.action_id IS '触发状态变更的 Action ID'")
    op.execute("COMMENT ON COLUMN character_state_history.recorded_at IS '记录时间'")

    # 5. 更新 pre_create_partitions() 函数，新增 character_state_history 分区创建
    op.execute("""
        CREATE OR REPLACE FUNCTION pre_create_partitions(months_ahead INT DEFAULT 3)
        RETURNS VOID AS $$
        DECLARE
            i INT;
            target_month DATE;
            partition_name TEXT;
            start_date TIMESTAMPTZ;
            end_date TIMESTAMPTZ;
        BEGIN
            -- action_records 按月分区
            FOR i IN 0..months_ahead LOOP
                target_month := date_trunc('month', CURRENT_TIMESTAMP + (i || ' months')::interval)::date;
                start_date := target_month::timestamptz;
                end_date := (target_month + INTERVAL '1 month')::timestamptz;
                partition_name := 'action_records_' || to_char(target_month, 'YYYY_MM');

                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
                    BEGIN
                        EXECUTE format(
                            'CREATE TABLE %I PARTITION OF action_records FOR VALUES FROM (%L) TO (%L)',
                            partition_name, start_date, end_date
                        );
                        RAISE NOTICE 'Created partition: %', partition_name;
                    EXCEPTION
                        WHEN undefined_table THEN
                            RAISE NOTICE 'Table action_records does not exist, skipping partition %', partition_name;
                        WHEN duplicate_table THEN
                            RAISE NOTICE 'Partition already exists: %', partition_name;
                    END;
                END IF;
            END LOOP;

            -- character_state_history 按月分区
            FOR i IN 0..months_ahead LOOP
                target_month := date_trunc('month', CURRENT_TIMESTAMP + (i || ' months')::interval)::date;
                start_date := target_month::timestamptz;
                end_date := (target_month + INTERVAL '1 month')::timestamptz;
                partition_name := 'character_state_history_' || to_char(target_month, 'YYYY_MM');

                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
                    BEGIN
                        EXECUTE format(
                            'CREATE TABLE %I PARTITION OF character_state_history FOR VALUES FROM (%L) TO (%L)',
                            partition_name, start_date, end_date
                        );
                        RAISE NOTICE 'Created partition: %', partition_name;
                    EXCEPTION
                        WHEN undefined_table THEN
                            RAISE NOTICE 'Table character_state_history does not exist, skipping partition %', partition_name;
                        WHEN duplicate_table THEN
                            RAISE NOTICE 'Partition already exists: %', partition_name;
                    END;
                END IF;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # 6. 预创建当前月份分区
    op.execute("SELECT pre_create_partitions(3);")


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Follow upgrade-only principle.")
