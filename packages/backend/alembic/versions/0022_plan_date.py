"""plans 增加 plan_date 列，daily 计划幂等键从标题字符串改为精确日期

此前 daily 计划幂等用「day_key in plan.title」字符串匹配（daily_plan_service.py），
LLM 生成含日期串的标题会被误判为今日已规划；且 get_active_plans 只返回 active，
计划过期后同日重跑会重复生成。

本迁移：
1. 新增 plans.plan_date DATE（可空——非 daily 计划无日期语义）
2. 部分唯一索引：仅 daily 类型按 (character_id, plan_date) 唯一，
   数据库层兜底防同日重复（幂等判定与数据约束一致）
3. 回填：从既有 daily 标题的 [YYYY-MM-DD] 前缀解析并填充

Revision ID: 0022_plan_date
Revises: 0021_embedding_dim_sync
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_plan_date"
down_revision: str | None = "0021_embedding_dim_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS plan_date DATE")

    # 回填既有 daily 计划标题中的 [YYYY-MM-DD] 前缀
    op.execute(
        "UPDATE plans SET plan_date = substring(title from '^\\[([0-9]{4}-[0-9]{2}-[0-9]{2})\\]')::date "
        "WHERE type = 'daily' AND plan_date IS NULL AND title ~ '^\\[[0-9]{4}-[0-9]{2}-[0-9]{2}\\]'"
    )

    # 去重：标题匹配幂等失效导致同日重复生成，保留每个 (character_id, plan_date)
    # 最新一条（created_at 最大），删除其余——否则部分唯一索引创建失败。
    # 用窗口函数 row_number 而非 DELETE USING 自连接（后者对同表自连接
    # 的可见性/配对顺序不可靠，实测保留的行不确定）。
    op.execute(
        "WITH ranked AS ("
        "  SELECT id, row_number() OVER ("
        "    PARTITION BY character_id, plan_date ORDER BY created_at DESC, id DESC"
        "  ) AS rn FROM plans WHERE type = 'daily' AND plan_date IS NOT NULL"
        ") DELETE FROM plans WHERE id IN (SELECT id FROM ranked WHERE rn > 1)"
    )

    # daily 计划按 (character_id, plan_date) 部分唯一（防同日重复生成）
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_plans_daily_char_date "
        "ON plans (character_id, plan_date) WHERE type = 'daily' AND plan_date IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_plans_daily_char_date")
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS plan_date")
