"""world_events 补充 created_at 单列索引

Round-3 M4：世界历史保留清理（run_world_retention_cycle）按
`WHERE created_at < cutoff` 删除超期事件，但表上只有
idx_world_events_tick 与 idx_world_events_type_time (event_type, created_at)
复合索引，单列 created_at 过滤无法命中，随数据量增长退化为全表扫描。

新增 idx_world_events_created_at (created_at) 供 DELETE 范围扫描使用。
现有迁移均未使用 CONCURRENTLY（0002/0006 同为普通 create_index），
且 world_events 为差分表、量级有限，沿用普通创建方式。

Revision ID: 0014_world_events_created_idx
Revises: 0013_unify_related_chars
Create Date: 2026-08-25
"""

from alembic import op

# revision 截短为 created_idx：alembic_version.version_num 为 VARCHAR(32)，
# 完整文件名（34 字符）写入会触发 StringDataRightTruncation
revision = "0014_world_events_created_idx"
down_revision = "0013_unify_related_chars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_world_events_created_at", "world_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_world_events_created_at", table_name="world_events")
