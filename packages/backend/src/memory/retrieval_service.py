"""记忆检索服务 - 向量检索 + 混合排序

使用 MemoryRepository.search_hybrid() 实现语义 + 重要性 + 时间衰减排序
"""

import time
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.db.repositories import MemoryRepository
from src.llm import LLMClient
from src.observability.metrics import MEMORY_RETRIEVE_LATENCY

logger = get_logger(__name__)


class RetrievalService:
    """记忆检索服务"""

    def __init__(self, llm: LLMClient, repo: MemoryRepository):
        self.llm = llm
        self.repo = repo

    async def search(
        self,
        character_id: UUID,
        query: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """检索相关记忆

        流程：
        1. 将 query 转为向量
        2. 调用 MemoryRepository.search_hybrid()
        3. 返回排序后的记忆列表

        Args:
            character_id: 角色 ID
            query: 查询文本（如"最近在咖啡店做了什么"）
            top_k: 返回数量

        Returns:
            记忆列表（dict: id, content, final_score）
        """
        start_perf = time.perf_counter()
        query_vec = await self.llm.embed(query)
        results = await self.search_with_vec(character_id, query_vec, top_k)
        MEMORY_RETRIEVE_LATENCY.observe(time.perf_counter() - start_perf)
        return results

    async def search_with_vec(
        self,
        character_id: UUID,
        query_vec: list[float],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """以现成查询向量执行混合检索

        供调用方在同一 Tick 内复用一次 embed 结果检索多类认知产物
        （记忆 + 反思），避免重复向量化开销。
        """
        results = await self.repo.search_hybrid(character_id, query_vec, top_k)

        logger.debug(
            "memory_search_completed",
            character_id=str(character_id),
            count=len(results),
        )
        return results
