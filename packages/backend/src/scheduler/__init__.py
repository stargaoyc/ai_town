"""定时调度模块 - 基于 APScheduler AsyncIOScheduler

职责：
1. 月度分区预创建（替代仅启动时执行 pre_create_partitions 的脆弱机制）
2. Phase 3.5+ 的其他定时任务（LLM 成本日预算重置、metrics flush 等）

设计要点：
- 使用 AsyncIOScheduler 与 FastAPI lifespan 集成
- CronTrigger 表达式：每月 25 号 03:00 执行（低峰期）
- 任务失败不中断调度器，仅记录日志
- 单实例运行（与 World Engine 共享 leader 锁机制）
"""
from src.scheduler.partition_scheduler import PartitionScheduler, create_scheduler

__all__ = [
    "PartitionScheduler",
    "create_scheduler",
]
