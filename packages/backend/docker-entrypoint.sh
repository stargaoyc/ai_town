#!/bin/sh
# P1-20：容器入口——迁移与进程启动解耦
# RUN_MIGRATIONS=1（默认）：先 alembic upgrade head 再启动；
# RUN_MIGRATIONS=0：跳过迁移直接启动（多副本附加实例 / 独立 Init Job 模式）
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[entrypoint] running alembic upgrade head..."
    alembic upgrade head
else
    echo "[entrypoint] RUN_MIGRATIONS=0, skipping migrations"
fi

exec "$@"
