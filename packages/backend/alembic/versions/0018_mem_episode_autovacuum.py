"""memory_episodes 各 HASH 子分区 autovacuum 调优：抑制高频插入/删除下的索引膨胀

Round-5 M9：memory_episodes 是保留周期的主要删除对象（分级删除、归档行清理、
压缩重写），且为 HASH 分区表无法按时间 drop 分区——死元组与 HNSW/B-tree
索引膨胀只能靠 autovacuum 回收。默认 vacuum_scale_factor=0.2 意味着大分区要
积攒约 20% 死元组才触发，膨胀在两次清理之间持续累积，故参照迁移 0002 对
character_states 的先例收紧到 0.05（analyze 同步到 0.02）。

不设 fillfactor：0002 的 fillfactor=85 为 character_states 乐观锁高频原地
UPDATE 预留 HOT 更新空间；本表追加写为主（Tick 插入 + 周期删除），调低只会
稀释页密度，动机不适用。

为什么逐子分区执行：分区父表不接受任何 reloption（PG 的
partitioned_table_reloptions 只允许空集，对父表 SET autovacuum_* 直接报
unrecognized parameter），autovacuum 以子分区为单位独立运行并读取各自的
reloption。经 pg_inherits 枚举子分区，不硬编码 p00..p15 分区名。
注意：此后若手工新增子分区，需对新分区单独执行同样的 SET。

Revision ID: 0018_mem_episode_autovacuum
Revises: 0017_mem_episode_created_at
Create Date: 2026-08-26
"""

from alembic import op

revision = "0018_mem_episode_autovacuum"
down_revision = "0017_mem_episode_created_at"
branch_labels = None
depends_on = None

_ENUM_PARTITIONS = """
SELECT c.relname FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'memory_episodes'
"""

_UPGRADE_STMT = (
    "ALTER TABLE %I SET ("
    "autovacuum_vacuum_scale_factor = 0.05,"
    " autovacuum_analyze_scale_factor = 0.02)"
)
_DOWNGRADE_STMT = (
    "ALTER TABLE %I RESET (autovacuum_vacuum_scale_factor, autovacuum_analyze_scale_factor)"
)


def _apply_to_partitions(stmt_template: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE part text;
        BEGIN
            FOR part IN {_ENUM_PARTITIONS}
            LOOP
                EXECUTE format('{stmt_template}', part);
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    _apply_to_partitions(_UPGRADE_STMT)


def downgrade() -> None:
    _apply_to_partitions(_DOWNGRADE_STMT)
