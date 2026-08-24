"""群体动力学：memory_episodes.source_type 扩展 gossip 枚举

传闻传播（GossipService）以 source_type='gossip' 标记第二手记忆，
原 CHECK 约束仅允许 action/conversation/reflection/event。

Revision ID: 0009_gossip_source_type
Revises: add_char_diaries
Create Date: 2026-08-24
"""

from alembic import op

revision = "0009_gossip_source_type"
down_revision = "add_char_diaries"
branch_labels = None
depends_on = None

_OLD_VALUES = "('action','conversation','reflection','event')"
_NEW_VALUES = "('action','conversation','reflection','event','gossip')"


def upgrade() -> None:
    # 分区表上的 CHECK 由父表统一管理，DROP/ADD 会同步到全部分区
    op.execute("ALTER TABLE memory_episodes DROP CONSTRAINT memory_episodes_source_type_check;")
    op.execute(
        f"ALTER TABLE memory_episodes ADD CONSTRAINT memory_episodes_source_type_check "
        f"CHECK (source_type IN {_NEW_VALUES});"
    )
    op.execute(
        "COMMENT ON COLUMN memory_episodes.source_type IS "
        "'来源类型：action/conversation/reflection/event/gossip（传闻第二手记忆）';"
    )


def downgrade() -> None:
    # 恢复旧约束前先清理存量传闻，避免 CHECK 违规
    op.execute("DELETE FROM memory_episodes WHERE source_type = 'gossip';")
    op.execute("ALTER TABLE memory_episodes DROP CONSTRAINT memory_episodes_source_type_check;")
    op.execute(
        f"ALTER TABLE memory_episodes ADD CONSTRAINT memory_episodes_source_type_check "
        f"CHECK (source_type IN {_OLD_VALUES});"
    )
