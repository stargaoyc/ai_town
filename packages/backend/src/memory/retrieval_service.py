"""记忆检索服务 - 向量检索 + 混合排序

使用 MemoryRepository.search_hybrid() 实现语义 + 重要性 + 时间衰减排序
"""

from typing import Any
from uuid import UUID

from structlog import get_logger

from src.db.repositories import MemoryRepository
from src.llm import LLMClient

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
        # 生成查询向量
        query_vec = await self.llm.embed(query)

        # 混合检索
        results = await self.repo.search_hybrid(character_id, query_vec, top_k)

        logger.debug(
            "memory_search_completed",
            character_id=str(character_id),
            query=query,
            count=len(results),
        )
        return results
