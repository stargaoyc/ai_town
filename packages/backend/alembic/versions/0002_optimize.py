"""数据库性能与完整性优化（v4）

基于四轮生产环境审查反馈的系统性改进。

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

Revision ID: 0002_optimize
Revises: 0001_init
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

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
    """

    # ============================================================
    # 改进 1+2+5+6: memory_episodes 重建为 HASH 分区（16 分区）
    # ============================================================
    # 问题：全局 HNSW + WHERE character_id = :cid 导致召回率崩塌
    # 方案：按 character_id HASH 分区（16 分区，HASH 分区数固定，扩容需全表重分布）
    #       查询时分区裁剪直接命中单分区，HNSW 只搜索该角色数据
    #
    # 同时：
    # - 增加 materialized 标志区分原始日志与向量化记忆
    # - ef_construction 64→128 提升图精度
    # - HNSW 索引在父表创建，自动传播到所有子分区（含未来新增）
    # - character_id 外键引用 characters(id) ON DELETE CASCADE
    #   ⚠️ v3 曾误认为「分区表不能加外键」而移除，实际 PostgreSQL 11+
    #      支持分区表引用非分区表，与分区类型（RANGE/LIST/HASH）无关。
    #      同设计的 action_records（RANGE 分区）也保留了 character_id 外键。

    op.execute("""
        -- 1. 重命名旧表
        ALTER TABLE memory_episodes RENAME TO memory_episodes_old;

        -- 2. 创建新的 HASH 分区表（16 分区）
        CREATE TABLE memory_episodes (
            id                 UUID NOT NULL DEFAULT uuidv7(),
            character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                                                                      -- 分区键（外键引用 characters.id）
            content            TEXT NOT NULL,
            embedding          vector(1536),                        -- nullable: materialized=false 时为 NULL
            importance         INT  NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
            timestamp          TIMESTAMPTZ NOT NULL DEFAULT now(),
            action_id          TEXT,
            location           TEXT,
            related_characters UUID[] NOT NULL DEFAULT '{}',
            is_reflected       BOOLEAN NOT NULL DEFAULT FALSE,
            materialized       BOOLEAN NOT NULL DEFAULT FALSE,      -- embedding 是否已生成
            source_type        TEXT NOT NULL DEFAULT 'action'
                               CHECK (source_type IN ('action','conversation','reflection','event')),
            PRIMARY KEY (id, character_id)                          -- 分区表主键必须含分区键
        ) PARTITION BY HASH (character_id);

        -- 3. 创建 16 个 HASH 分区（MODULUS 16）
        CREATE TABLE memory_episodes_p00 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 0);
        CREATE TABLE memory_episodes_p01 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 1);
        CREATE TABLE memory_episodes_p02 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 2);
        CREATE TABLE memory_episodes_p03 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 3);
        CREATE TABLE memory_episodes_p04 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 4);
        CREATE TABLE memory_episodes_p05 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 5);
        CREATE TABLE memory_episodes_p06 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 6);
        CREATE TABLE memory_episodes_p07 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 7);
        CREATE TABLE memory_episodes_p08 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 8);
        CREATE TABLE memory_episodes_p09 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 9);
        CREATE TABLE memory_episodes_p10 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 10);
        CREATE TABLE memory_episodes_p11 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 11);
        CREATE TABLE memory_episodes_p12 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 12);
        CREATE TABLE memory_episodes_p13 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 13);
        CREATE TABLE memory_episodes_p14 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 14);
        CREATE TABLE memory_episodes_p15 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 15);

        -- 4. ⚠️ HNSW 索引在父表创建，PostgreSQL 自动传播到所有子分区
        --    （包括未来新增的分区，无需手动补建）
        --    ef_construction=128 提升图构建精度（原 64 偏低）
        CREATE INDEX idx_mem_embedding_hnsw ON memory_episodes
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 128);

        -- 5. 辅助索引（同样在父表创建，自动传播）
        CREATE INDEX idx_mem_char_time ON memory_episodes (character_id, timestamp DESC);
        CREATE INDEX idx_mem_char_imp  ON memory_episodes (character_id, importance DESC);
        CREATE INDEX idx_mem_related   ON memory_episodes USING gin (related_characters);
        CREATE INDEX idx_mem_unreflected ON memory_episodes (character_id) WHERE is_reflected = FALSE;
        -- materialized=false 的部分索引：用于异步 embedding worker 批量拉取
        CREATE INDEX idx_mem_unmaterialized ON memory_episodes (timestamp) WHERE materialized = FALSE;

        -- 6. 迁移旧数据（⚠️ 大表锁警告，见函数 docstring）
        INSERT INTO memory_episodes (
            id, character_id, content, embedding, importance, timestamp,
            action_id, location, related_characters, is_reflected,
            materialized, source_type
        )
        SELECT
            id, character_id, content,
            CASE WHEN embedding IS NOT NULL THEN embedding::vector ELSE NULL END,
            importance, timestamp, action_id, location, related_characters,
            is_reflected,
            CASE WHEN embedding IS NOT NULL THEN TRUE ELSE FALSE END,
            source_type
        FROM memory_episodes_old;

        -- 7. 删除旧表
        DROP TABLE memory_episodes_old;
    """)

    # ============================================================
    # 改进 4: 删除 DEFAULT 分区（不创建死代码触发器）
    # ============================================================
    # 问题：DEFAULT 分区兜底 → 慢查询定时炸弹
    # 方案：删除 DEFAULT 分区即可。
    #
    # ⚠️ v2 曾创建 check_partition_exists() BEFORE INSERT 触发器，但这是死代码：
    #    PostgreSQL 声明式分区的分区路由发生在 BEFORE INSERT 触发器之前。
    #    若分区不存在，PG 直接抛 "no partition of relation found" 错误，
    #    根本不会执行到该触发器。
    #    v3 已删除该触发器，PG 原生报错已足够清晰。
    #
    # 分区预创建由 pre_create_partitions() 函数处理（见本脚本末尾）。

    op.execute("""
        -- ⚠️ 删除 DEFAULT 分区前先检查是否有数据，避免静默丢失
        -- 如果 DEFAULT 分区中有数据，说明有记录未能路由到正确分区，
        -- 需要先排查并迁移数据，再删除 DEFAULT 分区。

        DO $$
        DECLARE
            default_count INT;
        BEGIN
            -- 检查 action_records_default
            IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'action_records_default') THEN
                EXECUTE 'SELECT count(*) FROM action_records_default' INTO default_count;
                IF default_count > 0 THEN
                    RAISE EXCEPTION
                        'action_records_default contains % rows. '
                        'Migrate data to correct monthly partitions before dropping DEFAULT.',
                        default_count;
                END IF;
            END IF;

            -- 检查 messages_default
            IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'messages_default') THEN
                EXECUTE 'SELECT count(*) FROM messages_default' INTO default_count;
                IF default_count > 0 THEN
                    RAISE EXCEPTION
                        'messages_default contains % rows. '
                        'Migrate data to correct monthly partitions before dropping DEFAULT.',
                        default_count;
                END IF;
            END IF;
        END $$;

        -- 确认无数据后安全删除
        DROP TABLE IF EXISTS action_records_default;
        DROP TABLE IF EXISTS messages_default;
    """)

    # ============================================================
    # 改进 5: 保留 world_snapshots + 新增 world_events 差分表
    # ============================================================
    # 架构：事件溯源 + 定期快照（闭环）
    # - world_events: 每 N Tick 记录差分事件（高频，仅状态变化时写入）
    # - world_snapshots: 每 1000 Tick 存一次完整快照（低频，冷启动用）
    # - 冷启动：加载最新快照 → 回放之后的增量事件 → 恢复状态
    #
    # ⚠️ v2 曾删除 world_snapshots 仅保留 world_events，但这导致冷启动
    #    需从头回放所有事件，启动时间随运行时长线性增长，不可接受。

    op.create_table(
        "world_events",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("tick_id", sa.BigInteger, nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False,
                  comment="time/weather/scene/resource/event"),
        sa.Column("payload", sa.JSONB, nullable=False,
                  comment="变更内容（仅差分）"),
        sa.Column("created_at", sa.TIMESTAMPTZ, server_default=sa.text("now()")),
        # 幂等约束：同一 Tick 同一类型事件唯一，防止重试/重启导致重复写入
        sa.UniqueConstraint("tick_id", "event_type", name="uq_world_events_tick_type"),
    )
    op.create_index("idx_world_events_tick", "world_events", ["tick_id"])
    op.create_index("idx_world_events_type_time", "world_events", ["event_type", "created_at DESC"])

    # ============================================================
    # 改进 7: 彻底删除 personality 列，统一到 traits
    # ============================================================
    # 问题：personality TEXT[] 与 traits JSONB 冗余，易不一致
    # 方案：先迁移数据到 traits.personality，再删除 personality 列
    # ⚠️ 修复：使用 COALESCE 防御 traits 为 NULL 的情况
    op.execute("""
        -- 将 personality 迁移到 traits.personality（⚠️ COALESCE 防御 NULL）
        UPDATE characters
        SET traits = COALESCE(traits, '{}'::jsonb) || jsonb_build_object('personality', to_jsonb(personality))
        WHERE personality IS NOT NULL AND personality <> '{}';

        -- 彻底删除 personality 列
        ALTER TABLE characters DROP COLUMN personality;
    """)

    # ============================================================
    # 改进 11: 补充 updated_at 字段 + 通用自动更新触发器
    # ============================================================
    # 问题：仅 character_states 有 updated_at 触发器，characters/plans 缺失
    # 方案：为 characters/plans 补充 updated_at 字段，创建通用触发器函数

    # 补充 characters.updated_at
    op.add_column(
        "characters",
        sa.Column("updated_at", sa.TIMESTAMPTZ, server_default=sa.text("now()"),
                  comment="更新时间")
    )

    # 补充 plans.updated_at
    op.add_column(
        "plans",
        sa.Column("updated_at", sa.TIMESTAMPTZ, server_default=sa.text("now()"),
                  comment="更新时间")
    )

    # 通用 updated_at 自动更新触发器（替换原 character_states 专用函数）
    op.execute("""
        -- 删除旧的 character_states 专用触发器函数
        DROP TRIGGER IF EXISTS trg_character_states_updated_at ON character_states;
        DROP FUNCTION IF EXISTS update_character_states_updated_at();

        -- 创建通用 updated_at 触发器函数
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        -- 应用到所有带 updated_at 的表
        CREATE TRIGGER trg_characters_updated_at
            BEFORE UPDATE ON characters
            FOR EACH ROW EXECUTE FUNCTION update_updated_at();

        CREATE TRIGGER trg_character_states_updated_at
            BEFORE UPDATE ON character_states
            FOR EACH ROW EXECUTE FUNCTION update_updated_at();

        CREATE TRIGGER trg_plans_updated_at
            BEFORE UPDATE ON plans
            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)

    # ============================================================
    # 改进 6: reflection_sources 中间表 + 复合外键
    # ============================================================
    # 问题：v2 的 reflection_sources.memory_id 无外键（分区表复合主键限制）
    # 方案：增加 memory_character_id，与 memory_id 组成复合外键
    #       引用 memory_episodes(id, character_id) ON DELETE CASCADE
    #       PostgreSQL 12+ 支持分区表作为外键父表
    op.create_table(
        "reflection_sources",
        sa.Column("reflection_id", sa.UUID,
                  sa.ForeignKey("reflections.id", ondelete="CASCADE"),
                  primary_key=True, comment="反思 ID"),
        sa.Column("memory_id", sa.UUID,
                  primary_key=True, comment="记忆 ID"),
        sa.Column("memory_character_id", sa.UUID,
                  primary_key=True, comment="记忆所属角色 ID（分区键，外键组成部分）"),
        sa.Column("created_at", sa.TIMESTAMPTZ, server_default=sa.text("now()"),
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

    # 删除 reflections.related_episodes 废弃字段（已被 reflection_sources 中间表替代）
    # 保留废弃字段会导致双写不一致风险：应用层可能误用旧字段
    op.execute("""
        ALTER TABLE reflections DROP COLUMN IF EXISTS related_episodes;
    """)

    # ============================================================
    # 改进 8: character_states 乐观锁 + fillfactor + autovacuum
    # ============================================================
    op.add_column(
        "character_states",
        sa.Column("version", sa.Integer, nullable=False, server_default="1",
                  comment="乐观锁版本号")
    )

    # 高频更新表优化：fillfactor=85 预留页面空闲空间，提升 HOT 更新比例
    # autovacuum 调优：更早触发清理，减少死元组堆积
    #
    # ⚠️ 不在迁移中执行 VACUUM FULL：该命令会获取 ACCESS EXCLUSIVE 锁，
    #    阻塞所有读写。fillfactor 设置仅对新写入的页面生效，
    #    已有数据需在维护窗口手动执行 pg_repack 或 VACUUM FULL。
    op.execute("""
        -- fillfactor=85：预留 15% 空间供 HOT 更新（减少索引膨胀）
        ALTER TABLE character_states SET (fillfactor = 85);

        -- autovacuum 调优：高频更新表需要更频繁清理
        ALTER TABLE character_states SET (
            autovacuum_vacuum_scale_factor = 0.05,
            autovacuum_analyze_scale_factor = 0.02
        );

        -- 注意：fillfactor 仅对新写入的页面生效。
        -- 已有数据需在维护窗口执行：pg_repack -t character_states 或 VACUUM FULL ANALYZE character_states;
        -- ⚠️ VACUUM FULL 会获取 ACCESS EXCLUSIVE 锁，切勿在迁移中自动执行。
    """)

    # ============================================================
    # 改进 9: 覆盖索引（移除 content，避免索引膨胀）
    # ============================================================
    # 问题：INCLUDE (content) 会导致索引体积膨胀（content 可能 2000 字）
    # 方案：仅包含轻量字段，content 走主键回表
    #
    # 注意：不使用 BRIN 索引。按月范围分区已通过分区裁剪限制扫描范围，
    #       BRIN 在单月千万级以内数据无收益，反而增加写入维护开销。
    op.execute("""
        -- messages.user_id 覆盖索引（仅轻量字段）
        CREATE INDEX idx_msg_user_time_cover
        ON messages (user_id, created_at DESC)
        INCLUDE (role, platform);

        -- messages.conversation_id 覆盖索引（仅轻量字段）
        CREATE INDEX idx_msg_conv_time_cover
        ON messages (conversation_id, created_at)
        INCLUDE (role, character_id, platform);
    """)

    # ============================================================
    # 改进 13: COMMENT ON 元数据注释
    # ============================================================
    op.execute("""
        -- characters 表注释
        COMMENT ON TABLE characters IS '角色档案表 - 存储角色静态属性（由角色卡 YAML 导入）';
        COMMENT ON COLUMN characters.id IS '角色 ID（UUID v7，时间有序）';
        COMMENT ON COLUMN characters.name IS '角色名';
        COMMENT ON COLUMN characters.traits IS '特征字典（含 personality/hobby/schedule/mbti 等）';
        COMMENT ON COLUMN characters.is_active IS '是否参与世界运行';
        COMMENT ON COLUMN characters.created_at IS '创建时间';
        COMMENT ON COLUMN characters.updated_at IS '更新时间（触发器自动维护）';

        -- character_states 表注释
        COMMENT ON TABLE character_states IS '角色实时状态表 - PG 镜像（Redis 为主要读写源）';
        COMMENT ON COLUMN character_states.character_id IS '角色 ID（主键 + 外键）';
        COMMENT ON COLUMN character_states.stamina IS '体力 0-100，影响可执行 Action';
        COMMENT ON COLUMN character_states.satiety IS '饱腹度 0-100，低于阈值触发饥饿';
        COMMENT ON COLUMN character_states.mood IS '情绪（happy/calm/sad/anxious 等）';
        COMMENT ON COLUMN character_states.money IS '金钱，影响购物类 Action';
        COMMENT ON COLUMN character_states.current_action IS '当前动作 JSON: {action_id, params, end_time}';
        COMMENT ON COLUMN character_states.version IS '乐观锁版本号（防止并发覆盖）';
        COMMENT ON COLUMN character_states.updated_at IS '更新时间（触发器自动维护）';

        -- memory_episodes 表注释
        COMMENT ON TABLE memory_episodes IS '记忆片段表 - HASH 分区（16 分区）+ 父表 HNSW 索引';
        COMMENT ON COLUMN memory_episodes.character_id IS '所属角色（分区键，外键引用 characters.id ON DELETE CASCADE）';
        COMMENT ON COLUMN memory_episodes.embedding IS '向量嵌入（materialized=false 时为 NULL）';
        COMMENT ON COLUMN memory_episodes.importance IS '重要性 1-10，影响检索排序权重';
        COMMENT ON COLUMN memory_episodes.is_reflected IS '是否已被反思消化';
        COMMENT ON COLUMN memory_episodes.materialized IS 'embedding 是否已生成（异步 worker 处理）';
        COMMENT ON COLUMN memory_episodes.source_type IS '来源：action/conversation/reflection/event';

        -- world_events 表注释
        COMMENT ON TABLE world_events IS '世界变更事件表 - 差分记录（事件溯源），UNIQUE(tick_id, event_type) 保证幂等';
        COMMENT ON COLUMN world_events.tick_id IS 'Tick 序号';
        COMMENT ON COLUMN world_events.event_type IS '事件类型：time/weather/scene/resource/event';
        COMMENT ON COLUMN world_events.payload IS '变更内容（仅差分，非全量）';

        -- world_snapshots 表注释
        COMMENT ON TABLE world_snapshots IS '世界快照表 - 冷启动恢复用（每 1000 Tick 存一次）';
        COMMENT ON COLUMN world_snapshots.tick_id IS '快照对应的 Tick 序号';
        COMMENT ON COLUMN world_snapshots.world_time IS '虚拟世界时间';
        COMMENT ON COLUMN world_snapshots.weather IS '天气状态';
        COMMENT ON COLUMN world_snapshots.locations IS '所有场景状态 JSON';
        COMMENT ON COLUMN world_snapshots.resources IS '资源状态 JSON';
        COMMENT ON COLUMN world_snapshots.active_events IS '活跃事件列表 JSON';

        -- reflection_sources 表注释
        COMMENT ON TABLE reflection_sources IS '反思来源中间表 - 反思与记忆的多对多关联';
        COMMENT ON COLUMN reflection_sources.memory_id IS '记忆 ID（复合外键引用 memory_episodes）';
        COMMENT ON COLUMN reflection_sources.memory_character_id IS '记忆所属角色 ID（复合外键组成部分）';

        -- plans 表注释
        COMMENT ON TABLE plans IS '计划表 - 角色的长期/短期规划';
        COMMENT ON COLUMN plans.type IS '计划类型：long_term/short_term';
        COMMENT ON COLUMN plans.status IS '状态：active/completed/abandoned';
        COMMENT ON COLUMN plans.priority IS '优先级 1-5，影响 LLM 决策权重';
        COMMENT ON COLUMN plans.progress IS '进度 0-100';
    """)

    # ============================================================
    # 改进 14: 分区自动预创建函数
    # ============================================================
    # 问题：按月分区表忘记预创建下月分区 → 月初写入全部报错
    # 方案：提供 PL/pgSQL 函数，应用启动时调用，预创建未来 N 个月的分区
    op.execute("""
        CREATE OR REPLACE FUNCTION pre_create_partitions(months_ahead INT DEFAULT 3)
        RETURNS VOID AS $$
        DECLARE
            i INT;
            target_month DATE;
            partition_name TEXT;
            start_date DATE;
            end_date DATE;
        BEGIN
            -- action_records 按月分区
            FOR i IN 0..months_ahead LOOP
                target_month := date_trunc('month', CURRENT_DATE + (i || ' months')::interval)::date;
                start_date := target_month;
                end_date := target_month + INTERVAL '1 month';
                partition_name := 'action_records_' || to_char(target_month, 'YYYY_MM');

                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
                    EXECUTE format(
                        'CREATE TABLE %I PARTITION OF action_records FOR VALUES FROM (%L) TO (%L)',
                        partition_name, start_date, end_date
                    );
                    RAISE NOTICE 'Created partition: %', partition_name;
                END IF;
            END LOOP;

            -- messages 按月分区（若表存在分区结构）
            FOR i IN 0..months_ahead LOOP
                target_month := date_trunc('month', CURRENT_DATE + (i || ' months')::interval)::date;
                start_date := target_month;
                end_date := target_month + INTERVAL '1 month';
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
                            -- messages 表不存在，跳过
                            RAISE NOTICE 'Table messages does not exist, skipping partition %', partition_name;
                        WHEN duplicate_table THEN
                            -- 分区已存在，跳过
                            RAISE NOTICE 'Partition already exists: %', partition_name;
                        -- 其他异常（权限不足、磁盘满等）直接抛出，不吞掉
                    END;
                END IF;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql;

        -- 执行一次：预创建未来 3 个月的分区
        SELECT pre_create_partitions(3);
    """)


def downgrade() -> None:
    """⚠️ 生产环境遵循「只升级不降级」原则，通过备份恢复而非回滚迁移。

    原因：
    1. personality 列回滚会永久丢失数据（无法从 traits 反向提取）
    2. memory_episodes 分区回滚会丢失分区结构、索引参数
    3. world_events/world_snapshots 回滚后业务逻辑直接异常
    4. 数据迁移是不可逆的物理操作

    如需回滚，请使用 pg_dump 备份恢复：
        pg_restore --dbname=ai_town --clean --if-exists backup.dump
    """
    raise RuntimeError(
        "Downgrade not supported. Use backup restore instead. "
        "See docstring for details."
    )
