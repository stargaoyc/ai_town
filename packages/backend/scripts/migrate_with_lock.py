"""带 PostgreSQL advisory lock 的数据库迁移执行器（OPS-02 修复）

多副本部署时 RUN_MIGRATIONS=1 的多个实例会并发执行 alembic upgrade——
迁移脚本非幂等（DDL/DML 冲突）。本脚本用 PG advisory lock 协调：
同一时刻只有一个实例持有锁并执行迁移，其余阻塞等待，避免竞态。

用法（替换 entrypoint 中的裸 alembic 调用）：
    python -m scripts.migrate_with_lock

依赖 DATABASE_URL（asyncpg 协议）。advisory lock 由独立连接持有，
alembic 在持锁期间作为子进程执行。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import asyncpg

# 固定锁键：全实例共享同一键才有互斥效果
_LOCK_KEY = 0x414C54  # "ALT" ASCII，迁移锁命名空间


async def _run_migrations() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("[migrate] DATABASE_URL 未设置", file=sys.stderr)
        return 1

    conn: asyncpg.Connection | None = None
    try:
        # 1. 建立独立连接并获取 advisory lock（会话级，阻塞等待）。
        # asyncpg 不接受 SQLAlchemy 的 +asyncpg 驱动后缀，需剥离
        lock_url = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(lock_url)
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        print("[migrate] advisory lock acquired, running alembic upgrade head...")

        # 2. 持锁期间执行 alembic（子进程，继承当前环境）
        proc = subprocess.run(
            ["alembic", "upgrade", "head"],
            env=os.environ.copy(),
        )
        return proc.returncode
    except Exception as e:  # noqa: BLE001 - 迁移失败需完整暴露给 entrypoint
        print(f"[migrate] 迁移失败: {e}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                # 3. 释放锁（会话级锁随连接关闭自动释放，显式释放更清晰）
                await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
            except Exception:  # noqa: BLE001 - 释放失败不影响已完成的迁移
                pass
            await conn.close()


def main() -> None:
    raise SystemExit(asyncio.run(_run_migrations()))


if __name__ == "__main__":
    main()