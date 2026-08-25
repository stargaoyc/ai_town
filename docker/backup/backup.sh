#!/bin/sh
# PostgreSQL 定时备份（R4-H6 加固）：pg_dump 自定义格式（-Fc，自带压缩、
# 支持 pg_restore --jobs 并行恢复）。Redis 快照由 redis-backup 服务负责
# （redis:8-alpine 自带 redis-cli，避免在本容器依赖 docker CLI/docker.sock）。
# 由 docker-compose 的 db-backup 服务（profile: backup）挂载运行。
#
# 恢复方法：
#   pg_restore --jobs=4 --dbname=<目标库> /backups/ai_town_<ts>.dump
#   Redis: 停止 redis → 用 .rdb 覆盖 /data/dump.rdb → 启动
#   （或用 packages/backend/scripts/restore_drill.sh 自动验证）
#
# 注意：备份仍落在与数据库同主机的 ./data/backups 卷——请将该目录挂载/同步到
# 异机或对象存储，否则宿主机磁盘故障会同时摧毁数据与备份（RPO ≤ BACKUP_INTERVAL_HOURS）。
set -eu

INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-6}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
export PGHOST=postgres
export PGPORT=5432
export PGUSER=ai_town
export PGDATABASE=ai_town
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

dump_once() {
    ts=$(date +%Y%m%d_%H%M%S)
    # 先写 .part 再原子改名，避免半成品被误当作可用备份
    tmp="/backups/ai_town_${ts}.dump.part"
    final="/backups/ai_town_${ts}.dump"
    pg_dump --format=custom > "$tmp"
    mv "$tmp" "$final"
    echo "[backup] written $final"
}

prune_old() {
    find /backups -name 'ai_town_*.dump' -mtime "+${RETENTION_DAYS}" -delete
}

echo "[backup] scheduler started interval=${INTERVAL_HOURS}h retention=${RETENTION_DAYS}d"
while :; do
    if dump_once; then
        prune_old
    fi
    sleep "$((INTERVAL_HOURS * 3600))"
done
