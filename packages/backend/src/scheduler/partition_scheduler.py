"""分区预创建调度器 - 每月定时调用 pre_create_partitions()

背景（v8 P1 延后项 #68）：
原 pre_create_partitions 仅在应用启动时执行一次，若服务连续运行超过 3 个月，
第 4 个月的分区不会自动创建，导致月初写入全量失败。

方案：
- 启动时执行一次（确保当前周期分区存在）
- 每月 25 号 03:00 自动执行（提前 6 天预创建下月分区，留足容错窗口）
- 通过 APScheduler AsyncIOScheduler 与 FastAPI lifespan 集成

容错策略：
- 任务执行失败仅记录日志，不中断调度器
- pre_create_partitions 内部已有 undefined_table/duplicate_table 异常捕获
- 即使某次失败，下个月仍会重试
"""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from structlog import get_logger

from src.db.session import db

logger = get_logger(__name__)


# 每月 25 号 03:00 执行（低峰期，提前 6 天预创建下月分区）
PARTITION_CRON = CronTrigger(day=25, hour=3, minute=0)


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
