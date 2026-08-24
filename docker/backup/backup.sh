#!/bin/sh
# 数据库定时备份：pg_dump | gzip -> /backups，按保留天数自动清理
# 由 docker-compose 的 db-backup 服务（profile: backup）挂载运行
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
    tmp="/backups/ai_town_${ts}.sql.gz.part"
    final="/backups/ai_town_${ts}.sql.gz"
    pg_dump | gzip > "$tmp"
    mv "$tmp" "$final"
    echo "[backup] written $final"
}

prune_old() {
    find /backups -name 'ai_town_*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete
}

echo "[backup] scheduler started interval=${INTERVAL_HOURS}h retention=${RETENTION_DAYS}d"
while :; do
    if dump_once; then
        prune_old
    fi
    sleep "$((INTERVAL_HOURS * 3600))"
done
