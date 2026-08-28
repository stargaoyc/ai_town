#!/bin/sh
# P1-20：启动前执行数据库迁移（RUN_MIGRATIONS=1 默认）。
# OPS-02：多副本部署时多个实例并发 alembic upgrade 会产生 DDL/DML 竞态；
# 迁移统一经 scripts/migrate_with_lock.py 以 PG advisory lock 协调——
# 同一时刻仅一个实例执行迁移，其余阻塞等待，消除人工约定 RUN_MIGRATIONS=0。
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[entrypoint] running alembic upgrade head (advisory-lock coordinated)..."
    python -m scripts.migrate_with_lock
else
    echo "[entrypoint] RUN_MIGRATIONS=0, skipping migrations"
fi

exec "$@"
