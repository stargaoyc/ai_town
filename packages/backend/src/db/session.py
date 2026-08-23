# src/db/session.py
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings


class DB:
    def __init__(self) -> None:
        self.engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            echo=settings.db_echo,
        )
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self._register_query_metrics()

    def _register_query_metrics(self) -> None:
        """将 SQL 执行耗时写入 Prometheus 直方图 DB_QUERY_DURATION

        指标此前定义后从未 observe（审查 §八-P3）；挂在 sync_engine 的
        cursor 事件上，asyncpg 驱动在 greenlet 内同样触发。
        连接级 info 字典保存起止时间戳，天然协程安全。
        """
        from src.observability.metrics import DB_QUERY_DURATION

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def before_cursor(conn: Any, *args: Any) -> None:  # noqa: ANN401
            conn.info.setdefault("_query_start_time", []).append(time.perf_counter())

        @event.listens_for(self.engine.sync_engine, "after_cursor_execute")
        def after_cursor(conn: Any, *args: Any) -> None:  # noqa: ANN401
            start_times = conn.info.get("_query_start_time")
            if not start_times:
                return
            duration = time.perf_counter() - start_times.pop()
            DB_QUERY_DURATION.observe(duration)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise


db = DB()
