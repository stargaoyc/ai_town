"""分区生命周期调度器 - 每月预创建新分区 + 回收超期旧分区

背景（v8 P1 延后项 #68）：
原 pre_create_partitions 仅在应用启动时执行一次，若服务连续运行超过 3 个月，
第 4 个月的分区不会自动创建，导致月初写入全量失败。

方案：
- 启动时执行一次（确保当前周期分区存在）
- 每月 25 号 03:00 自动执行（提前 6 天预创建下月分区，留足容错窗口）
- 通过 APScheduler AsyncIOScheduler 与 FastAPI lifespan 集成

Round-3 H9 补充回收半区：pre-create 只增不删，无回收会让分区数与存储无限膨胀。
增速量级的两个边界（都不要当成事实使用）：
- 理论上限由 character_tick_seconds 决定：=30 时每角色每天最多 2880 次 Tick，
  20 角色下 action_records 与 character_state_history 各约 57,600 行/天；
- 实际 Tick 周期受 LLM 延迟支配，通常远慢于配置值，真实增速只能实测。
运维应以 Prometheus 指标 ai_town_character_tick_total 的实测斜率推算增速，
不要信任理论值或任何历史估算。每月同刻在预创建之后执行 drop_old_partitions：
- 按 pg_inherits 枚举子分区，解析分区名中的月份（与 pre_create 的
  `{table}_YYYY_MM` 命名约定一致），整月超出 retention 边界的才删除
- 先 DETACH 再 DROP：DETACH 只需父表短时排他锁，DROP 作用于已脱钩子表，
  锁压力小；中途失败时已 DETACH 的子表仍是完整独立表，可人工恢复

容错策略：
- 任务执行失败仅记录日志，不中断调度器
- pre_create_partitions 内部已有 undefined_table/duplicate_table 异常捕获
- 即使某次失败，下个月仍会重试
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.config import settings
from src.db.session import db

logger = get_logger(__name__)


# 每月 25 号 03:00 执行（低峰期，提前 6 天预创建下月分区）
PARTITION_CRON = CronTrigger(day=25, hour=3, minute=0)

# 回收任务排在预创建之后（03:05），保证同一维护窗口内「先建新、后删旧」
PARTITION_RETENTION_CRON = CronTrigger(day=25, hour=3, minute=5)

# 与迁移内 pre_create_partitions() 的命名约定保持一致：{table}_YYYY_MM
_PARTITION_NAME_RE = re.compile(r"^(?P<table>action_records|character_state_history)_(?P<month>\d{4}_\d{2})$")


def _retention_boundary(now: datetime, months: int) -> tuple[str, datetime]:
    """计算 retention 边界：(可保留的最旧月份 YYYY_MM, 更早月份的首日 UTC 时刻)

    年月直接借位换算，避免为一个月减法引入 dateutil 依赖。
    """
    total = now.year * 12 + (now.month - 1) - months
    year, month = total // 12, total % 12 + 1
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}_{month:02d}", datetime(next_year, next_month, 1, tzinfo=UTC)


async def drop_old_partitions(session: AsyncSession) -> tuple[int, int]:
    """删除超出保留期的整月子分区，并清理默认分区中的超期散行

    仅删除「整个月份范围都早于 retention 边界」的分区——跨边界的当月分区
    可能仍含有效数据，必须保留。

    character_state_history 存在 DEFAULT 分区（0007）：早于所有月度分区的
    散行会落入其中，DETACH/DROP 覆盖不到，需在同一周期按 recorded_at 直删。
    普通 DELETE 不建索引即可——DEFAULT 分区只接收越界散行、常态近乎空表，
    当前量级顺序扫描成本可忽略。

    Args:
        session: 数据库会话（提交由调用方的会话上下文负责）

    Returns:
        (删除的分区数, 默认分区删除的行数)
    """
    now = datetime.now(UTC)
    ar_cutoff_month, _ = _retention_boundary(now, settings.action_records_retention_months)
    csh_cutoff_month, csh_cutoff_dt = _retention_boundary(now, settings.state_history_retention_months)
    cutoff_months = {
        "action_records": ar_cutoff_month,
        "character_state_history": csh_cutoff_month,
    }

    dropped = 0
    for parent, cutoff in cutoff_months.items():
        children = await session.execute(
            text(
                "SELECT c.relname FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent "
                "WHERE p.relname = :parent"
            ),
            {"parent": parent},
        )
        stale = sorted(
            name
            for (name,) in children
            if (m := _PARTITION_NAME_RE.match(name)) is not None and m.group("month") < cutoff
        )
        for partition_name in stale:
            await session.execute(text(f"ALTER TABLE {parent} DETACH PARTITION {partition_name}"))
            await session.execute(text(f"DROP TABLE {partition_name}"))
            logger.info(
                "partition_dropped",
                parent=parent,
                partition=partition_name,
                retention_month_boundary=cutoff,
            )
            dropped += 1

    result = cast(
        "CursorResult[Any]",
        await session.execute(
            text("DELETE FROM character_state_history_default WHERE recorded_at < :cutoff"),
            {"cutoff": csh_cutoff_dt},
        ),
    )
    deleted_default_rows = int(result.rowcount or 0)

    if dropped or deleted_default_rows:
        logger.info(
            "partitions_retained_cleanup_done",
            dropped_partitions=dropped,
            deleted_default_rows=deleted_default_rows,
        )
    return dropped, deleted_default_rows


async def run_partition_retention_cycle(session_factory: Any | None = None) -> tuple[int, int]:
    """单次分区回收周期：删除超期分区 + 清理默认分区散行（可测试入口）

    与 loops.run_world_retention_cycle 同风格：会话工厂可注入，
    提交由生产路径 db.session 上下文退出时完成。

    Returns:
        (删除的分区数, 默认分区删除的行数)
    """
    factory = session_factory or db.session
    async with factory() as session:
        return await drop_old_partitions(session)


async def _run_pre_create_partitions() -> None:
    """执行分区预创建（每月 25 号 03:00）

    预创建未来 3 个月的分区，确保月初写入不报错。
    """
    logger.info("scheduled_pre_create_partitions_start")
    try:
        async with db.session() as session:
            await session.execute(text("SELECT pre_create_partitions(3);"))
            await session.commit()
        logger.info("scheduled_pre_create_partitions_done")
    except Exception as e:
        logger.error(
            "scheduled_pre_create_partitions_failed",
            error=str(e),
            exc_info=True,
        )
        # 不抛出，避免 APScheduler 移除任务


async def _run_partition_retention() -> None:
    """执行分区回收（每月 25 号 03:05，紧随预创建）"""
    logger.info("scheduled_partition_retention_start")
    try:
        await run_partition_retention_cycle()
        logger.info("scheduled_partition_retention_done")
    except Exception as e:
        logger.error(
            "scheduled_partition_retention_failed",
            error=str(e),
            exc_info=True,
        )
        # 不抛出，避免 APScheduler 移除任务


def create_scheduler() -> AsyncIOScheduler:
    """创建调度器实例并注册任务

    Returns:
        未启动的 AsyncIOScheduler 实例，由调用方启动/关闭
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    # 月度分区预创建任务
    scheduler.add_job(
        _run_pre_create_partitions,
        trigger=PARTITION_CRON,
        id="pre_create_partitions_monthly",
        name="pre_create_partitions_monthly",
        replace_existing=True,
        # 错过执行窗口不补跑（如服务停机期间），下次到点正常执行
        misfire_grace_time=3600,
        coalesce=True,
    )

    # Round-3 H9：月度分区回收任务（预创建后 5 分钟执行）
    scheduler.add_job(
        _run_partition_retention,
        trigger=PARTITION_RETENTION_CRON,
        id="partition_retention_monthly",
        name="partition_retention_monthly",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    logger.info(
        "scheduler_initialized",
        jobs=[job.id for job in scheduler.get_jobs()],
    )
    return scheduler


class PartitionScheduler:
    """分区调度器封装（便于在 lifespan 中管理）

    用法：
        scheduler = PartitionScheduler()
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self) -> None:
        self._scheduler = create_scheduler()
        self._running = False

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            return
        self._scheduler.start()
        self._running = True
        logger.info(
            "partition_scheduler_started",
            jobs=[job.id for job in self._scheduler.get_jobs()],
        )

    async def stop(self, wait: bool = True) -> None:
        """停止调度器

        Args:
            wait: 是否等待正在执行的任务完成
        """
        if not self._running:
            return
        self._scheduler.shutdown(wait=wait)
        self._running = False
        logger.info("partition_scheduler_stopped")

    @property
    def running(self) -> bool:
        return self._running
