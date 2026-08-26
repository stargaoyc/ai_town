"""索引治理与扩展清理（round-6 复审）

修复项：
- R6-L8b: 移除 pg_trgm 扩展（0001_init 安装后全库零使用，见 config.py / memory_repo.py 注释证实中文无效，死扩展）
- R6-L8c: 重建 idx_mem_unmaterialized —— 0002_optimize 以 timestamp 建列，ORM 元数据却声明 next_retry_at，
  以 fetch_unmaterialized 实际查询为准（ORDER BY timestamp + materialized/fail_count 过滤）
- R6-L9: 新增 idx_mem_retention —— fetch_retention_candidates 的跨角色保留周期查询
  （importance<=6 部分索引），此前无匹配索引走顺序扫描

Revision ID: 0020_memory_index_governance
Revises: 0019_cognition_fixes
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_memory_index_governance"
down_revision: str | None = "0019_cognition_fixes"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ============================================================
    # R6-L8b: 移除 pg_trgm 扩展
    # 检查：全库已无任何 gin_trgm_ops 索引或 similarity() 调用，
    # memory_repo.py 与 config.py 注释已证实 pg_trgm 对中文无效。
    # ============================================================
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")

    # ============================================================
    # R6-L8c: 重建 idx_mem_unmaterialized
    # 旧索引（0002_optimize）: (timestamp) WHERE materialized = FALSE
    # ORM 元数据误声明 next_retry_at —— 实际查询 ORDER BY timestamp
    # 新索引: (timestamp) WHERE materialized = FALSE AND fail_count < 5
    # 与 ORM 元数据对齐，且涵盖 fetch_unmaterialized 的恒定过滤条件
    # ============================================================
    op.execute("SET statement_timeout = '10min';")
    op.execute("SET lock_timeout = '60s';")

    op.execute("DROP INDEX IF EXISTS idx_mem_unmaterialized;")
    op.execute("""
        CREATE INDEX idx_mem_unmaterialized ON memory_episodes (timestamp)
        WHERE materialized = FALSE AND fail_count < 5
    """)

    # ============================================================
    # R6-L9: 新增 idx_mem_retention
    # fetch_retention_candidates 查询:
    #   WHERE (
    #     (importance <= 3 AND timestamp < low_cutoff)
    #     OR (importance >= 4 AND importance <= 6 AND timestamp < mid_cutoff)
    #   ) AND source_type != 'archive'
    #   ORDER BY timestamp ASC LIMIT 300
    # 部分索引 WHERE importance <= 6 覆盖两个 OR 分支的 importance 范围，
    # (importance, timestamp) 组合让 planner 对每个分支做范围扫描
    # ============================================================
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mem_retention ON memory_episodes (importance, timestamp)
        WHERE importance <= 6
    """)


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Use backup restore instead. See docstring for details.")
