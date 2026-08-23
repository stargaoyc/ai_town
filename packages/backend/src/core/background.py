"""后台任务注册表（P-2：fire-and-forget 任务泄漏治理）

asyncio 事件循环只持有任务的弱引用：裸 `asyncio.create_task(...)` 的任务
可能在执行中被 GC 静默回收，异常也无人消费。lifespan 在 yield 之前抛异常时，
已创建的后台任务同样无人取消。

本模块提供进程级注册表：
- spawn()：创建即注册 + 异常回调记日志，杜绝静默丢失
- shutdown()：统一取消并等待所有存活任务，供 lifespan finally 调用
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)


class BackgroundTaskRegistry:
    """进程级后台任务注册表"""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        """创建后台任务并注册

        Args:
            coro: 待执行的协程
            name: 任务名（日志与调试用）

        Returns:
            已注册的 Task
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        """任务结束回调：注销 + 消费异常（防止 exception was never retrieved）"""
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "background_task_failed",
                task_name=task.get_name(),
                error=str(exc),
                exc_info=exc,
            )

    async def shutdown(self, timeout: float = 5.0) -> None:
        """取消所有存活任务并等待退出

        Args:
            timeout: 等待取消生效的总时长（秒），超时后放弃等待
        """
        live = [t for t in self._tasks if not t.done()]
        for task in live:
            task.cancel()
        if live:
            await asyncio.wait(live, timeout=timeout)
        self._tasks.clear()


# 进程级单例（lifespan 初始化，业务模块直接 import 使用）
_registry = BackgroundTaskRegistry()


def spawn_background(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
    """模块级便捷函数：在进程注册表中创建后台任务"""
    return _registry.spawn(coro, name)


async def shutdown_background_tasks(timeout: float = 5.0) -> None:
    """模块级便捷函数：lifespan shutdown 时统一取消"""
    await _registry.shutdown(timeout)
