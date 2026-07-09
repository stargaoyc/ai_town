"""数据库性能与完整性优化（v6）

基于六轮生产环境审查反馈的系统性改进。

致命问题修复：
1. personality 迁移使用 COALESCE 防御 NULL（原代码 NULL || jsonb = NULL）
2. HNSW 索引在父表创建（自动传播所有子分区，避免运维噩梦）
3. 删除死代码 check_partition_exists 触发器（PG 分区路由在 BEFORE INSERT 之前执行）
4. memory_episodes.character_id 补充外键 REFERENCES characters(id) ON DELETE CASCADE
   （v3 曾误认为「分区表不能加外键」，实际 PG 11+ 支持）

架构级修复：
5. 删除 DEFAULT 分区（删除前检查数据，避免静默丢失）
6. 保留 world_snapshots 表 + 新增 world_events 差分表（事件溯源 + 快照闭环）
7. world_events 增加 UNIQUE(tick_id, event_type) 幂等约束
8. HASH 分区改为 16 个（HASH 分区数固定，扩容需全表重分布）
9. 彻底删除 personality 列（不保留废弃列）
10. reflection_sources 增加复合外键引用 memory_episodes(id, character_id)

性能优化：
11. 覆盖索引移除 content 字段（避免索引膨胀）
12. character_states fillfactor=85 + autovacuum 调优（不自动执行 VACUUM FULL）
13. 通用 updated_at 触发器覆盖所有表（characters/character_states/plans）
14. characters/plans 补充 updated_at 字段

工程化改进：
15. COMMENT ON TABLE/COLUMN 元数据注释
16. pre_create_partitions() 分区自动预创建函数（收紧异常捕获范围）
17. downgrade 简化为 raise exception（只升级不降级原则）

v5 修复：
18. 删除 reflections.related_episodes 废弃字段（已被 reflection_sources 替代）

v6 修复（P0 阻塞性 + P1 健壮性）：
19. ⚠️ P0: 移除 messages 表覆盖索引创建（0001_init 未建 messages 表，
    直接 CREATE INDEX 会触发 "relation messages does not exist" 错误，
    导致整个迁移中断。messages 表+索引+分区统一推迟到 Phase 3）
20. P1: 添加 statement_timeout=10min + lock_timeout=60s 显式超时保护
    （防止 memory_episodes 大表 INSERT...SELECT 卡死，超时后事务回滚）
21. P1: pre_create_partitions() 为 action_records 增加 undefined_table 异常捕获
    （与 messages 逻辑一致，提升鲁棒性）

v10 修复（PG18 兼容性）：
22. ⚠️ PostgreSQL 18 对 prepared statement 限制更严格，不允许单条 prepared statement
    包含多条 SQL 命令。将所有多语句 op.execute() 拆分为单语句调用。

Revision ID: 0002_optimize
Revises: 0001_init
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, JSONB

# revision identifiers, used by Alembic.
revision: str = "0002_optimize"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    """
    ⚠️ 生产环境执行警告：

    1. memory_episodes 表重建涉及全表数据迁移，会持有 ACCESS EXCLUSIVE 锁。
       - 数据量 < 100 万行：可直接执行（预计 < 1 分钟）
       - 数据量 > 100 万行：必须使用 pg_repack 或蓝绿迁移
       - 执行前必须备份：pg_dump --table=memory_episodes

    2. 执行前确认无活跃的 Tick 循环（暂停 World Engine）。

    3. 建议在低峰期维护窗口执行，并设置锁超时：
       SET lock_wait_timeout = '30s';

    4. ⚠️ v6 新增：显式超时保护（防止大表 INSERT...SELECT 卡死）
       - statement_timeout: 防止单条 SQL 执行过久（10 分钟）
       - lock_timeout: 防止锁等待无限期（60 秒）
       超时后事务回滚，旧表名自动恢复（Alembic 事务保护）。
    """

    # ============================================================
    # v6: 显式超时保护（防止 memory_episodes 重建卡死）
    # ============================================================
    op.execute("SET statement_timeout = '10min';")
    op.execute("SET lock_timeout = '60s';")

    # ============================================================
    # 改进 1+2+5+6: memory_episodes 重建为 HASH 分区（16 分区）
    # ============================================================
    # v10: PG18 兼容性 - 拆分为单语句执行

    # 1. 重命名旧表
    op.execute("ALTER TABLE memory_episodes RENAME TO memory_episodes_old;")

    # 2. 创建新的 HASH 分区表（16 分区）
    op.execute("""
        CREATE TABLE memory_episodes (
            id                 UUID NOT NULL DEFAULT uuidv7(),
            character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            content            TEXT NOT NULL,
            embedding          vector(1536),
            importance         INT  NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
            timestamp          TIMESTAMPTZ NOT NULL DEFAULT now(),
            action_id          TEXT,
            location           TEXT,
            related_characters UUID[] NOT NULL DEFAULT '{}',
            is_reflected       BOOLEAN NOT NULL DEFAULT FALSE,
            materialized       BOOLEAN NOT NULL DEFAULT FALSE,
            source_type        TEXT NOT NULL DEFAULT 'action'
                               CHECK (source_type IN ('action','conversation','reflection','event')),
            PRIMARY KEY (id, character_id)
        ) PARTITION BY HASH (character_id)
    """)

    # 3. 创建 16 个 HASH 分区
    for i in range(16):
        op.execute(
            f"CREATE TABLE memory_episodes_p{i:02d} PARTITION OF memory_episodes "
            f"FOR VALUES WITH (MODULUS 16, REMAINDER {i});"
        )

    # 4. HNSW 索引在父表创建
    # v10: 先删除 0001_init 创建的旧索引（索引名全局唯一）
    op.execute("DROP INDEX IF EXISTS idx_mem_embedding_hnsw;")
    op.execute("""
        CREATE INDEX idx_mem_embedding_hnsw ON memory_episodes
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 128)
    """)

    # 5. 辅助索引
    # v10: 先删除 0001_init 创建的旧索引（索引名全局唯一）
    op.execute("DROP INDEX IF EXISTS idx_mem_char_time;")
    op.execute("DROP INDEX IF EXISTS idx_mem_unreflected;")
    op.execute("CREATE INDEX idx_mem_char_time ON memory_episodes (character_id, timestamp DESC);")
    op.execute("CREATE INDEX idx_mem_char_imp ON memory_episodes (character_id, importance DESC);")
    op.execute("CREATE INDEX idx_mem_related ON memory_episodes USING gin (related_characters);")
    op.execute("CREATE INDEX idx_mem_unreflected ON memory_episodes (character_id) WHERE is_reflected = FALSE;")
    op.execute("CREATE INDEX idx_mem_unmaterialized ON memory_episodes (timestamp) WHERE materialized = FALSE;")

    # 6. 迁移旧数据（v8 P0 修复：JSONB → UUID[] 显式转换）
    op.execute("""
        INSERT INTO memory_episodes (
            id, character_id, content, embedding, importance, timestamp,
            action_id, location, related_characters, is_reflected,
            materialized, source_type
        )
        SELECT
            id, character_id, content,
            CASE WHEN embedding IS NOT NULL THEN embedding::vector ELSE NULL END,
            importance, timestamp, action_id, location,
            CASE
                WHEN related_characters IS NOT NULL AND jsonb_typeof(related_characters) = 'array'
                THEN ARRAY(SELECT jsonb_array_elements_text(related_characters))::uuid[]
                ELSE '{}'::uuid[]
            END,
            is_reflected,
            CASE WHEN embedding IS NOT NULL THEN TRUE ELSE FALSE END,
            source_type
        FROM memory_episodes_old
    """)

    # 7. 删除旧表
    op.execute("DROP TABLE memory_episodes_old;")

    # ============================================================
    # 改进 4: 删除 DEFAULT 分区
    # ============================================================
    # v10: PG18 兼容性 - DO 块和 DROP 分开执行

    op.execute("""
        DO $$
        DECLARE
            default_count INT;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'action_records_default') THEN
                EXECUTE 'SELECT count(*) FROM action_records_default' INTO default_count;
                IF default_count > 0 THEN
                    RAISE EXCEPTION
                        'action_records_default contains % rows. Migrate data before dropping DEFAULT.',
                        default_count;
                END IF;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'messages_default') THEN
                EXECUTE 'SELECT count(*) FROM messages_default' INTO default_count;
                IF default_count > 0 THEN
                    RAISE EXCEPTION
                        'messages_default contains % rows. Migrate data before dropping DEFAULT.',
                        default_count;
                END IF;
            END IF;
        END $$;
    """)

    op.execute("DROP TABLE IF EXISTS action_records_default;")
    op.execute("DROP TABLE IF EXISTS messages_default;")

    # ============================================================
    # 改进 5: 保留 world_snapshots + 新增 world_events 差分表
    # ============================================================
    op.create_table(
        "world_events",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("tick_id", sa.BigInteger, nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False,
                  comment="time/weather/scene/resource/event"),
        sa.Column("payload", JSONB, nullable=False,
                  comment="变更内容（仅差分）"),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tick_id", "event_type", name="uq_world_events_tick_type"),
    )
    op.create_index("idx_world_events_tick", "world_events", ["tick_id"])
    op.execute("CREATE INDEX idx_world_events_type_time ON world_events (event_type, created_at DESC);")

    # ============================================================
    # 改进 7: 彻底删除 personality 列，统一到 traits
    # ============================================================
    op.execute("""
        UPDATE characters
        SET traits = COALESCE(traits, '{}'::jsonb) || jsonb_build_object('personality', to_jsonb(personality))
        WHERE personality IS NOT NULL AND personality <> '{}'
    """)

    op.execute("ALTER TABLE characters DROP COLUMN personality;")

    # ============================================================
    # 改进 11: 补充 updated_at 字段 + 通用自动更新触发器
    # ============================================================
    op.add_column(
        "characters",
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  comment="更新时间")
    )

    op.add_column(
        "plans",
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  comment="更新时间")
    )

    # 通用 updated_at 触发器
    op.execute("DROP TRIGGER IF EXISTS trg_character_states_updated_at ON character_states;")
    op.execute("DROP FUNCTION IF EXISTS update_character_states_updated_at();")

    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER trg_characters_updated_at
            BEFORE UPDATE ON characters
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    op.execute("""
        CREATE TRIGGER trg_character_states_updated_at
            BEFORE UPDATE ON character_states
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    op.execute("""
        CREATE TRIGGER trg_plans_updated_at
            BEFORE UPDATE ON plans
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)

    # ============================================================
    # 改进 6: reflection_sources 中间表 + 复合外键
    # ============================================================
    op.create_table(
        "reflection_sources",
        sa.Column("reflection_id", sa.UUID,
                  sa.ForeignKey("reflections.id", ondelete="CASCADE"),
                  primary_key=True, comment="反思 ID"),
        sa.Column("memory_id", sa.UUID,
                  primary_key=True, comment="记忆 ID"),
        sa.Column("memory_character_id", sa.UUID,
                  primary_key=True, comment="记忆所属角色 ID（分区键，外键组成部分）"),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"),
                  comment="创建时间"),
        sa.ForeignKeyConstraint(
            ["memory_id", "memory_character_id"],
            ["memory_episodes.id", "memory_episodes.character_id"],
            ondelete="CASCADE",
            name="fk_reflection_sources_memory",
        ),
    )
    op.create_index("idx_refl_sources_memory", "reflection_sources",
                    ["memory_id", "memory_character_id"])

    op.execute("ALTER TABLE reflections DROP COLUMN IF EXISTS related_episodes;")

    # ============================================================
    # 改进 8: character_states 乐观锁 + fillfactor + autovacuum
    # ============================================================
    op.add_column(
        "character_states",
        sa.Column("version", sa.Integer, nullable=False, server_default="1",
                  comment="乐观锁版本号")
    )

    op.execute("ALTER TABLE character_states SET (fillfactor = 85);")
    op.execute("""
        ALTER TABLE character_states SET (
            autovacuum_vacuum_scale_factor = 0.05,
            autovacuum_analyze_scale_factor = 0.02
        )
    """)

    # ============================================================
    # 改进 13: COMMENT ON 元数据注释
    # ============================================================
    # v10: PG18 兼容性 - 每个 COMMENT ON 单独执行

    # characters 表注释
    op.execute("COMMENT ON TABLE characters IS '角色档案表 - 存储角色静态属性（由角色卡 YAML 导入）';")
    op.execute("COMMENT ON COLUMN characters.id IS '角色 ID（UUID v7，时间有序）';")
    op.execute("COMMENT ON COLUMN characters.name IS '角色名';")
    op.execute("COMMENT ON COLUMN characters.traits IS '特征字典（含 personality/hobby/schedule/mbti 等）';")
    op.execute("COMMENT ON COLUMN characters.is_active IS '是否参与世界运行';")
    op.execute("COMMENT ON COLUMN characters.created_at IS '创建时间';")
    op.execute("COMMENT ON COLUMN characters.updated_at IS '更新时间（触发器自动维护）';")

    # character_states 表注释
    op.execute("COMMENT ON TABLE character_states IS '角色实时状态表 - PG 镜像（Redis 为主要读写源）';")
    op.execute("COMMENT ON COLUMN character_states.character_id IS '角色 ID（主键 + 外键）';")
    op.execute("COMMENT ON COLUMN character_states.stamina IS '体力 0-100，影响可执行 Action';")
    op.execute("COMMENT ON COLUMN character_states.satiety IS '饱腹度 0-100，低于阈值触发饥饿';")
    op.execute("COMMENT ON COLUMN character_states.mood IS '情绪（happy/calm/sad/anxious 等）';")
    op.execute("COMMENT ON COLUMN character_states.money IS '金钱，影响购物类 Action';")
    op.execute("COMMENT ON COLUMN character_states.current_action IS '当前动作 JSON: {action_id, params, end_time}';")
    op.execute("COMMENT ON COLUMN character_states.version IS '乐观锁版本号（防止并发覆盖）';")
    op.execute("COMMENT ON COLUMN character_states.updated_at IS '更新时间（触发器自动维护）';")

    # memory_episodes 表注释
    op.execute("COMMENT ON TABLE memory_episodes IS '记忆片段表 - HASH 分区（16 分区）+ 父表 HNSW 索引';")
    op.execute("COMMENT ON COLUMN memory_episodes.character_id IS '所属角色（分区键，外键引用 characters.id ON DELETE CASCADE）';")
    op.execute("COMMENT ON COLUMN memory_episodes.embedding IS '向量嵌入（materialized=false 时为 NULL）';")
    op.execute("COMMENT ON COLUMN memory_episodes.importance IS '重要性 1-10，影响检索排序权重';")
    op.execute("COMMENT ON COLUMN memory_episodes.is_reflected IS '是否已被反思消化';")
    op.execute("COMMENT ON COLUMN memory_episodes.materialized IS 'embedding 是否已生成（异步 worker 处理）';")
    op.execute("COMMENT ON COLUMN memory_episodes.source_type IS '来源：action/conversation/reflection/event';")

    # world_events 表注释
    op.execute("COMMENT ON TABLE world_events IS '世界变更事件表 - 差分记录（事件溯源），UNIQUE(tick_id, event_type) 保证幂等';")
    op.execute("COMMENT ON COLUMN world_events.tick_id IS 'Tick 序号';")
    op.execute("COMMENT ON COLUMN world_events.event_type IS '事件类型：time/weather/scene/resource/event';")
    op.execute("COMMENT ON COLUMN world_events.payload IS '变更内容（仅差分，非全量）';")

    # world_snapshots 表注释
    op.execute("COMMENT ON TABLE world_snapshots IS '世界快照表 - 冷启动恢复用（每 1000 Tick 存一次）';")
    op.execute("COMMENT ON COLUMN world_snapshots.tick_id IS '快照对应的 Tick 序号';")
    op.execute("COMMENT ON COLUMN world_snapshots.world_time IS '虚拟世界时间';")
    op.execute("COMMENT ON COLUMN world_snapshots.weather IS '天气状态';")
    op.execute("COMMENT ON COLUMN world_snapshots.locations IS '所有场景状态 JSON';")
    op.execute("COMMENT ON COLUMN world_snapshots.resources IS '资源状态 JSON';")
    op.execute("COMMENT ON COLUMN world_snapshots.active_events IS '活跃事件列表 JSON';")

    # reflection_sources 表注释
    op.execute("COMMENT ON TABLE reflection_sources IS '反思来源中间表 - 反思与记忆的多对多关联';")
    op.execute("COMMENT ON COLUMN reflection_sources.memory_id IS '记忆 ID（复合外键引用 memory_episodes）';")
    op.execute("COMMENT ON COLUMN reflection_sources.memory_character_id IS '记忆所属角色 ID（复合外键组成部分）';")

    # plans 表注释
    op.execute("COMMENT ON TABLE plans IS '计划表 - 角色的长期/短期规划';")
    op.execute("COMMENT ON COLUMN plans.type IS '计划类型：long_term/short_term';")
    op.execute("COMMENT ON COLUMN plans.status IS '状态：active/completed/abandoned';")
    op.execute("COMMENT ON COLUMN plans.priority IS '优先级 1-5，影响 LLM 决策权重';")
    op.execute("COMMENT ON COLUMN plans.progress IS '进度 0-100';")

    # ============================================================
    # 改进 14: 分区自动预创建函数
    # ============================================================
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

            FOR i IN 0..months_ahead LOOP
                target_month := date_trunc('month', CURRENT_TIMESTAMP + (i || ' months')::interval)::date;
                start_date := target_month::timestamptz;
                end_date := (target_month + INTERVAL '1 month')::timestamptz;
                partition_name := 'messages_' || to_char(target_month, 'YYYY_MM');

                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
                    BEGIN
                        EXECUTE format(
                            'CREATE TABLE %I PARTITION OF messages FOR VALUES FROM (%L) TO (%L)',
                            partition_name, start_date, end_date
                        );
                        RAISE NOTICE 'Created partition: %', partition_name;
                    EXCEPTION
                        WHEN undefined_table THEN
                            RAISE NOTICE 'Table messages does not exist, skipping partition %', partition_name;
                        WHEN duplicate_table THEN
                            RAISE NOTICE 'Partition already exists: %', partition_name;
                    END;
                END IF;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("SELECT pre_create_partitions(3);")


def downgrade() -> None:
    """⚠️ 生产环境遵循「只升级不降级」原则，通过备份恢复而非回滚迁移。"""
    raise RuntimeError(
        "Downgrade not supported. Use backup restore instead. "
        "See docstring for details."
    )