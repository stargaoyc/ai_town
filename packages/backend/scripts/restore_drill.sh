#!/bin/sh
# 备份恢复演练（R4-H6）：验证最新（或指定）备份可真实恢复，而非「备了就能恢复」的假设。
#
# 用法：
#   sh scripts/restore_drill.sh                      # 自动取 ./data/backups 最新 .dump + .rdb
#   sh scripts/restore_drill.sh ai_town_xxx.dump [redis_xxx.rdb]
#
# 前置：本机可用 docker；恢复在一次性容器内完成，不触碰运行中的实例。
set -eu

BACKUP_DIR="${BACKUP_DIR:-./data/backups}"
PG_IMAGE="pgvector/pgvector:pg18"
REDIS_IMAGE="redis:8-alpine"

DUMP_FILE="${1:-}"
RDB_FILE="${2:-}"

if [ -z "$DUMP_FILE" ]; then
    DUMP_FILE=$(ls -t "$BACKUP_DIR"/ai_town_*.dump 2>/dev/null | head -n 1 || true)
fi
if [ -z "$RDB_FILE" ]; then
    RDB_FILE=$(ls -t "$BACKUP_DIR"/redis_*.rdb 2>/dev/null | head -n 1 || true)
fi

cleanup() {
    docker rm -f drill-pg-"$$" >/dev/null 2>&1 || true
    docker rm -f drill-redis-"$$" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
    echo "[drill] FAIL: $1"
    exit 1
}

# ---------- PostgreSQL 恢复 ----------
if [ -n "$DUMP_FILE" ] && [ -f "$DUMP_FILE" ]; then
    echo "[drill] restoring $(basename "$DUMP_FILE") into throwaway postgres..."
    docker run -d --name drill-pg-"$$" \
        -e POSTGRES_USER=ai_town -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=ai_town \
        "$PG_IMAGE" >/dev/null
    until docker exec drill-pg-"$$" pg_isready -U ai_town >/dev/null 2>&1; do sleep 1; done
    docker cp "$DUMP_FILE" drill-pg-"$$":/tmp/restore.dump >/dev/null
    if ! docker exec drill-pg-"$$" pg_restore --no-owner --role=ai_town --dbname=ai_town /tmp/restore.dump; then
        fail "pg_restore returned error (see above)"
    fi
    for t in characters memory_episodes messages; do
        cnt=$(docker exec drill-pg-"$$" psql -U ai_town -d ai_town -tAc "SELECT count(*) FROM $t")
        echo "[drill] table $t rows=$cnt"
    done
    echo "[drill] PASS: postgres dump restores cleanly"
else
    echo "[drill] SKIP: no .dump found (path=$DUMP_FILE)"
fi

# ---------- Redis RDB 验证 ----------
if [ -n "$RDB_FILE" ] && [ -f "$RDB_FILE" ]; then
    echo "[drill] verifying $(basename "$RDB_FILE") with throwaway redis..."
    # 把待验 RDB 以单文件 bind mount 挂为 /data/dump.rdb，redis 启动即自动加载
    docker run -d --name drill-redis-"$$" \
        -v "$(cd "$(dirname "$RDB_FILE")" && pwd)/$(basename "$RDB_FILE")":/data/dump.rdb:ro \
        "$REDIS_IMAGE" redis-server --requirepass drill >/dev/null
    sleep 2
    keys=$(docker exec drill-redis-"$$" redis-cli --no-auth-warning -a drill dbsize 2>/dev/null | tr -d '[:space:]')
    if [ -z "$keys" ] || [ "$keys" = "0" ]; then
        fail "restored redis reports empty dataset (dbsize=$keys)"
    fi
    echo "[drill] PASS: redis rdb loads, dbsize=$keys"
else
    echo "[drill] SKIP: no .rdb found (path=$RDB_FILE)"
fi

echo "[drill] done."
