"""记忆片段服务 - 负责记忆的生成与沉淀

流程：
1. Character Tick 执行 Action 后，生成记忆片段
2. 调用 LLM embed() 生成向量
3. 写入 MemoryEpisode（含 embedding + importance）
"""
from datetime import datetime, timezone
from uuid import UUID

from structlog import get_logger

from src.db.models import MemoryEpisode
from src.db.repositories import MemoryRepository
from src.llm import LLMClient

logger = get_logger(__name__)


class EpisodeService:
    """记忆片段服务"""

    def __init__(self, llm: LLMClient, repo: MemoryRepository):
        self.llm = llm
        self.repo = repo

    async def create_episode(
        self,
        character_id: UUID,
        content: str,
        action_id: str | None = None,
        location: str | None = None,
        importance: int = 5,
    ) -> MemoryEpisode:
        """创建记忆片段

        ⚠️ embedding 由 EmbeddingWorker 异步生成，此处不阻塞 Tick 循环。
        新记忆 materialized=false, embedding=NULL，worker 批量拉取后调 LLM 生成。

        Args:
            character_id: 角色 ID
            content: 记忆内容（自然语言描述）
            action_id: 关联 Action ID
            location: 发生场景
            importance: 重要性评分（1-10）

        Returns:
            MemoryEpisode 实体
        """
        episode = MemoryEpisode(
            character_id=character_id,
            content=content,
            embedding=None,          # 异步 worker 生成
            materialized=False,      # 标记为未向量化
            importance=importance,
            timestamp=datetime.now(timezone.utc),
            action_id=action_id,
            location=location,
        )

        saved = await self.repo.add(episode)
        logger.info(
            "memory_episode_created",
            character_id=str(character_id),
            importance=importance,
        )
        return saved

    async def get_recent(self, character_id: UUID, limit: int = 50) -> list[MemoryEpisode]:
        """获取最近记忆

        Args:
            character_id: 角色 ID
            limit: 返回数量限制

        Returns:
            最近的记忆列表（按时间倒序）
        """
        return await self.repo.recent(character_id, limit)