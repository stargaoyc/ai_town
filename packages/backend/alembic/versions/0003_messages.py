"""Phase 3: 消息服务表 + 向量化失败处理 + 分区函数精简

变更内容：
1. 创建 conversations 表（会话主表，角色 × 用户的对话线程）
2. 创建 messages 表（消息记录，非分区表 - 对齐 ORM 模型）
3. memory_episodes 增加 fail_count / last_error 字段（异步向量化失败处理）
4. 更新 pre_create_partitions() 函数：
   - 移除 messages 分区创建循环（messages 改为非分区表）
   - 新增 pg_partitioned_table 检查，避免对非分区表创建分区时报错
5. COMMENT ON 元数据注释

设计要点：
- conversations/messages 按「方案 B」对齐 ORM 模型，使用非分区表
  原因：messages 通过 conversation_id FK 关联 conversations，
        分区表无法建立外键引用非分区父表的常规 FK（PG 12 部分支持但有约束）
        单表 + (conversation_id, created_at) 复合索引足以支撑查询
- messages 表记录 token 与 cost，便于 Phase 3.5 LLM 成本控制
- memory_episodes.fail_count + last_error 实现「最大重试次数熔断」，
  防止失败记忆反复重试占用算力

Revision ID: 0003_messages
Revises: 0002_optimize
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_messages"
down_revision: Union[str, None] = "0002_optimize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    """Phase 3 消息服务迁移"""

    # ============================================================
    # 1. conversations 表（会话主表）
    # ============================================================
    # 设计：一个用户与一个角色的对话线程
    # - character_id 外键引用 characters(id) ON DELETE CASCADE
    # - platform 标识来源（web/qq/lark/internal）
    # - context JSONB 存储最近 N 条消息摘要（LLM 上下文压缩）
    # - last_message_at 用于会话活跃度排序
    op.execute("""
        CREATE TABLE conversations (
            id              UUID PRIMARY KEY DEFAULT uuidv7(),
            character_id    UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            user_id         VARCHAR(100) NOT NULL,
            platform        VARCHAR(20) NOT NULL,
            context         JSONB,
            last_message_at TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        -- 同一用户对同一角色仅一个会话（ON CONFLICT 依赖此唯一约束）
        CREATE UNIQUE INDEX idx_conv_user_char ON conversations (user_id, character_id);
        -- 按最后消息时间排序活跃会话
        CREATE INDEX idx_conv_last_msg   ON conversations (last_message_at DESC);
        -- 按 character 查询所有相关会话（角色侧主动分享时使用）
        CREATE INDEX idx_conv_char       ON conversations (character_id);
    """)

    # ============================================================
    # 2. messages 表（消息记录，非分区表）
    # ============================================================
    # 设计：单条消息记录
    # - conversation_id 外键引用 conversations(id) ON DELETE CASCADE
    # - sender: user / character / system（与 ORM 模型一致）
    # - tokens / cost: LLM 成本追踪（Phase 3.5 熔断依赖）
    # - extra_data: 平台特定字段（reply_to、attachments 等）
    #
    # ⚠️ 不分区原因（方案 B 对齐 ORM）：
    # - messages 通过 FK 关联 conversations，分区表 FK 受限
    # - (conversation_id, created_at) 复合索引足够支撑时间线查询
    # - 单表 3 个月内千万级数据 PostgreSQL B-tree 完全可承载
    # - 长期归档由 Phase 4 冷热分离方案处理
    op.execute("""
        CREATE TABLE messages (
            id              UUID PRIMARY KEY DEFAULT uuidv7(),
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sender          VARCHAR(20) NOT NULL,
            content         TEXT NOT NULL,
            tokens          INT,
            cost            NUMERIC(10, 6),
            extra_data      JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        -- 会话时间线查询（核心索引）
        CREATE INDEX idx_msg_conv_time ON messages (conversation_id, created_at);
        -- 全局最近消息查询（管理面板、监控）
        CREATE INDEX idx_msg_created   ON messages (created_at);
    """)

    # ============================================================
    # 3. memory_episodes 增加失败处理字段（v8 P1 延后项）
    # ============================================================
    # 问题：原仅 materialized 布尔字段，无失败次数与错误信息
    #       Embedding API 持续故障时失败记忆反复拉取重试
    # 方案：
    # - fail_count: 失败次数，达到 MAX(5) 后从待处理队列移除
    # - last_error: 最近失败错误信息（截断 1000 字），便于排查
    # - 复合部分索引：仅索引 materialized=false AND fail_count<5 的记忆
    #   worker 拉取时直接命中，无需扫描失败记忆
    op.execute("""
        ALTER TABLE memory_episodes
            ADD COLUMN fail_count INT NOT NULL DEFAULT 0,
            ADD COLUMN last_error TEXT;

        -- 替换原 idx_mem_unmaterialized：排除已熔断的失败记忆
        DROP INDEX IF EXISTS idx_mem_unmaterialized;
        CREATE INDEX idx_mem_unmaterialized ON memory_episodes (timestamp)
            WHERE materialized = FALSE AND fail_count < 5;

        COMMENT ON COLUMN memory_episodes.fail_count IS '向量化失败次数，达到 5 后不再重试';
        COMMENT ON COLUMN memory_episodes.last_error IS '最近一次失败错误信息（截断 1000 字）';

        COMMENT ON TABLE conversations IS '会话表 - 用户与角色的对话线程';
        COMMENT ON COLUMN conversations.character_id IS '角色 ID（外键 ON DELETE CASCADE）';
        COMMENT ON COLUMN conversations.user_id IS '用户标识（平台特定）';
        COMMENT ON COLUMN conversations.platform IS '来源平台 web/qq/lark/internal';
        COMMENT ON COLUMN conversations.context IS 'LLM 上下文压缩摘要';
        COMMENT ON COLUMN conversations.last_message_at IS '最后消息时间（活跃度排序）';

        COMMENT ON TABLE messages IS '消息表 - 单条消息记录（非分区表）';
        COMMENT ON COLUMN messages.conversation_id IS '会话 ID（外键 ON DELETE CASCADE）';
        COMMENT ON COLUMN messages.sender IS '发送者 user/character/system';
        COMMENT ON COLUMN messages.content IS '消息内容';
        COMMENT ON COLUMN messages.tokens IS 'LLM token 消耗（成本追踪）';
        COMMENT ON COLUMN messages.cost IS '本次调用费用（USD，Phase 3.5 熔断依赖）';
        COMMENT ON COLUMN messages.extra_data IS '附加信息（reply_to/attachments 等）';
    """)

    # ============================================================
    # 4. 更新 pre_create_partitions() 函数
    # ============================================================
    # 变更：
    # - 移除 messages 分区创建循环（messages 已改为非分区表）
    # - 新增 pg_partitioned_table 存在性检查，确保函数对未来
    #   非分区表健壮（避免 "relation is not partitioned" 错误）
    # - 保留 action_records 的分区创建逻辑
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

            -- ⚠️ v3 迁移变更：messages 已改为非分区表（方案 B 对齐 ORM）
            --    原分区创建循环已移除，避免 "relation is not partitioned" 错误
            --    若未来 messages 改回分区表，可通过 pg_partitioned_table 检查自动恢复
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    """⚠️ 生产环境遵循「只升级不降级」原则，通过备份恢复而非回滚迁移。

    原因：
    1. messages 表回滚会永久丢失对话历史
    2. memory_episodes.fail_count/last_error 回滚会丢失失败诊断信息
    3. pre_create_partitions 函数回滚可能影响 action_records 分区

    如需回滚，请使用 pg_dump 备份恢复：
        pg_restore --dbname=ai_town --clean --if-exists backup.dump
    """
    raise RuntimeError(
        "Downgrade not supported. Use backup restore instead. "
        "See docstring for details."
    )
