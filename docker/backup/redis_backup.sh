#!/bin/sh
# Redis 定时快照（R4-H6）：redis-cli --rdb 触发一致性 RDB 落到本容器挂载的
# 备份卷——Redis 是角色实时状态的真相源（ADR-0001），此前完全不在备份范围内。
# 使用 redis:8-alpine 镜像运行（自带 redis-cli），无需 docker CLI/docker.sock。
#
# 恢复方法：停止 redis → 用 .rdb 覆盖 ./data/redis 中的 dump.rdb → 启动 redis。
set -eu

INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-6}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
export REDIS_PASSWORD="${REDIS_PASSWORD:?REDIS_PASSWORD is required}"

snapshot_once() {
    ts=$(date +%Y%m%d_%H%M%S)
    tmp="/backups/redis_${ts}.rdb.part"
    final="/backups/redis_${ts}.rdb"
    # --rdb 让服务端落一份一致性 RDB 到指定路径（经 bind mount 直达宿主备份卷）
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" --no-auth-warning \
        -a "$REDIS_PASSWORD" --rdb "$tmp" >/dev/null
    mv "$tmp" "$final"
    echo "[redis-backup] written $final"
}

prune_old() {
    find /backups -name 'redis_*.rdb' -mtime "+${RETENTION_DAYS}" -delete
}

echo "[redis-backup] scheduler started interval=${INTERVAL_HOURS}h retention=${RETENTION_DAYS}d"
while :; do
    if snapshot_once; then
        prune_old
    fi
    sleep "$((INTERVAL_HOURS * 3600))"
done
