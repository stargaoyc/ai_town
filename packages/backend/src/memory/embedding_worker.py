"""异步 Embedding Worker

后台批量处理 materialized=false 的记忆，生成 embedding 向量。
解决"每个 Tick 调用 LLM API 生成 embedding 阻塞主循环"的问题。

运行方式：
    uv run python -m src.memory.embedding_worker

或集成到 FastAPI 后台任务（lifespan 中启动）。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import AsyncGenerator, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.memory_repo import MemoryRepository
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

# 类型别名：异步会话工厂
SessionFactory = Callable[[], AsyncGenerator[AsyncSession, None]]


class EmbeddingWorker:
    """异步 Embedding 生成 Worker

    职责：
    1. 定期拉取 materialized=false 的记忆（FOR UPDATE SKIP LOCKED）
    2. 批量调用 LLM embedding API
    3. 更新记忆的 embedding 字段并标记 materialized=true

    并发安全：
    - 使用 SKIP LOCKED 跳过被其他 worker 锁定的行
    - 支持多 worker 实例并行处理
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        llm_client: LLMClient,
        batch_size: int = 20,
        poll_interval: float = 5.0,
    ):
        """
        Args:
            session_factory: 异步会话工厂（db.session 的 context manager）
            llm_client: LLM 客户端（用于 embedding）
            batch_size: 每批拉取数量
            poll_interval: 轮询间隔（秒）
        """
        self.session_factory = session_factory
        self.llm_client = llm_client
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self._running = False

    async def run(self) -> None:
        """启动 worker 主循环"""
        self._running = True
        logger.info(
            "embedding_worker_started",
            batch_size=self.batch_size,
            poll_interval=self.poll_interval,
        )

        while self._running:
            try:
                processed = await self._process_batch()
                if processed == 0:
                    # 无待处理记忆，等待
                    await asyncio.sleep(self.poll_interval)
            except Exception as e:
                logger.error("embedding_worker_error", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        """停止 worker"""
        self._running = False
        logger.info("embedding_worker_stopped")

    async def _process_batch(self) -> int:
        """处理一批未向量化的记忆

        Returns:
            本批处理的记忆数量
        """
        async with self.session_factory() as session:
            repo = MemoryRepository(session)
            episodes = await repo.fetch_unmaterialized(limit=self.batch_size)

            if not episodes:
                return 0

            logger.info(
                "embedding_batch_start",
                count=len(episodes),
            )

            # 批量生成 embedding
            success_count = 0
            failed_count = 0
            circuit_break_count = 0
            for episode in episodes:
                try:
                    embedding = await self.llm_client.embed(episode.content)
                    await repo.update_embedding(
                        episode_id=episode.id,
                        character_id=episode.character_id,
                        embedding=embedding,
                    )
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    # v3: 标记失败并累加 fail_count，达 5 次后自动熔断
                    await repo.mark_embedding_failed(
                        episode_id=episode.id,
                        character_id=episode.character_id,
                        error=str(e),
                    )
                    # 检测熔断（fail_count 达到 5 表示刚刚跨过阈值）
                    if episode.fail_count + 1 >= 5:
                        circuit_break_count += 1
                    logger.error(
                        "embedding_failed",
                        episode_id=str(episode.id),
                        character_id=str(episode.character_id),
                        error=str(e),
                        fail_count=episode.fail_count + 1,
                        circuit_broken=episode.fail_count + 1 >= 5,
                    )

            await session.commit()

            logger.info(
                "embedding_batch_done",
                count=len(episodes),
                success=success_count,
                failed=failed_count,
                circuit_broken=circuit_break_count,
            )
            return len(episodes)


# === 独立运行入口 ===

async def main():
    """独立运行 embedding worker"""
    from src.db.session import db
    from src.llm.client import LLMClient

    llm = LLMClient()
    worker = EmbeddingWorker(
        session_factory=db.session,
        llm_client=llm,
        batch_size=20,
        poll_interval=5.0,
    )

    try:
        await worker.run()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
