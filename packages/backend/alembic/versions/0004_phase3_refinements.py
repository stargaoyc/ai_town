"""Phase 3 精炼：会话唯一键扩展 + updated_at 触发器 + 枚举 CHECK + 向量化退避

变更内容（v9 审查 P1/P2 有效问题处理）：
1. conversations 唯一键扩展：(user_id, character_id) → (user_id, platform, character_id)
   原因：P1 #4，未来跨平台用户体系打通时避免会话串扰
2. conversations 增加 updated_at 字段 + 通用触发器
   原因：P2 #3，会话上下文更新无时间轨迹
3. conversations.platform 与 messages.sender 增加 CHECK 约束
   原因：P2 #1，枚举字段防止脏数据
4. memory_episodes 增加 next_retry_at 字段
   原因：P2 #5，向量化失败指数退避，避免短期快速重试加剧服务压力
5. 更新 idx_mem_unmaterialized 部分索引：考虑 next_retry_at
6. COMMENT ON FUNCTION pre_create_partitions（P1 #2 文档化）

v10 修复（PG18 兼容性）：
7. ⚠️ PostgreSQL 18 对 prepared statement 限制更严格，不允许单条 prepared statement
    包含多条 SQL 命令。将所有多语句 op.execute() 拆分为单语句调用。

Revision ID: 0004_phase3_refinements
Revises: 0003_messages
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_phase3_refinements"
down_revision: Union[str, None] = "0003_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    """Phase 3 精炼迁移"""

    # ============================================================
    # 1. conversations 唯一键扩展：加入 platform 维度
    # ============================================================
    # 原 UNIQUE(user_id, character_id) → UNIQUE(user_id, platform, character_id)
    # 向前兼容：当前不同平台 user_id 不重复，扩展后无冲突
    # 向后兼容：未来跨平台用户体系打通时，同一用户在不同平台可有独立会话
    # v10: PG18 兼容性 - 拆分为单语句执行
    op.execute("DROP INDEX IF EXISTS idx_conv_user_char;")
    op.execute("CREATE UNIQUE INDEX idx_conv_user_platform_char ON conversations (user_id, platform, character_id);")

    # ============================================================
    # 2. conversations 增加 updated_at 字段 + 触发器
    # ============================================================
    # 与 characters / character_states / plans 对齐，使用通用 update_updated_at()
    # v10: PG18 兼容性 - 拆分为单语句执行
    op.execute("ALTER TABLE conversations ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();")
    op.execute("""
        CREATE TRIGGER trg_conversations_updated_at
            BEFORE UPDATE ON conversations
            FOR EACH ROW EXECUTE FUNCTION update_updated_at()
    """)
    op.execute("COMMENT ON COLUMN conversations.updated_at IS '更新时间（触发器自动维护）';")

    # ============================================================
    # 3. 枚举字段 CHECK 约束
    # ============================================================
    # 防止应用层 bug 写入非法值，污染数据
    # v10: PG18 兼容性 - 拆分为单语句执行
    op.execute("ALTER TABLE conversations ADD CONSTRAINT ck_conv_platform CHECK (platform IN ('web', 'qq', 'lark', 'internal'));")
    op.execute("ALTER TABLE messages ADD CONSTRAINT ck_msg_sender CHECK (sender IN ('user', 'character', 'system'));")

    # ============================================================
    # 4. memory_episodes 增加 next_retry_at 字段（向量化指数退避）
    # ============================================================
    # 失败后 worker 不立即重试，按指数退避延后：
    #   retry 1 → 60s 后
    #   retry 2 → 180s 后
    #   retry 3 → 600s 后
    #   retry 4 → 1800s 后
    #   retry 5 → 熔断（不再重试）
    # v10: PG18 兼容性 - 拆分为单语句执行
    op.execute("ALTER TABLE memory_episodes ADD COLUMN next_retry_at TIMESTAMPTZ;")
    op.execute("COMMENT ON COLUMN memory_episodes.next_retry_at IS '下次可重试时间（指数退避），NULL 表示可立即重试或已成功';")

    # 更新部分索引：排除未到重试时间的记忆
    op.execute("DROP INDEX IF EXISTS idx_mem_unmaterialized;")
    op.execute("CREATE INDEX idx_mem_unmaterialized ON memory_episodes (next_retry_at NULLS FIRST) WHERE materialized = FALSE AND fail_count < 5;")

    # ============================================================
    # 5. pre_create_partitions 函数文档化
    # ============================================================
    op.execute("""
        COMMENT ON FUNCTION pre_create_partitions IS
            '预创建 action_records 未来 N 个月分区。'
            '应用启动时执行一次 + APScheduler 每月 25 号 03:00 自动执行。'
            'messages 已改为非分区表（0003 迁移），不在本函数处理范围。';
    """)


def downgrade() -> None:
    """⚠️ 生产环境遵循「只升级不降级」原则，通过备份恢复而非回滚迁移。"""
    raise RuntimeError(
        "Downgrade not supported. Use backup restore instead."
    )
