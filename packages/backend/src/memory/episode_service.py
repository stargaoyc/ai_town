"""记忆片段服务 - 负责记忆的生成与沉淀

流程：
1. Character Tick 执行 Action 后，生成记忆片段
2. 调用 LLM embed() 生成向量
3. 写入 MemoryEpisode（含 embedding + importance）

重要性评分支持两种模式：
- 规则评分（默认）：基于 action 类型 + 情绪关键词
- LLM 评分（可选）：环境变量 MEMORY_LLM_SCORING_ENABLED=true 启用，
  LLM 在生成记忆的同时进行打分，更精准但增加 LLM 调用成本。
"""

import re
from datetime import UTC, datetime
from uuid import UUID

from structlog import get_logger

from src.config import settings
from src.db.models import MemoryEpisode
from src.db.repositories import MemoryRepository
from src.llm import LLMClient
from src.llm.prompts import PromptTemplates

logger = get_logger(__name__)


class EpisodeService:
    """记忆片段服务"""

    def __init__(
        self,
        llm: LLMClient,
        repo: MemoryRepository,
        prompts: PromptTemplates | None = None,
    ):
        self.llm = llm
        self.repo = repo
        self._prompts = prompts

    async def score_importance_with_llm(
        self,
        character_name: str,
        content: str,
        action_id: str | None,
        reason: str | None,
        mood: str | None,
        location: str | None,
        fallback_importance: int,
    ) -> int:
        """使用 LLM 对记忆重要性进行评分（1-10）

        评分维度：
        - 情感强度：涉及强烈情绪（开心/生气/惊讶）的事件更重要
        - 关系影响：涉及他人互动的事件更重要
        - 稀缺性：罕见事件（冒险/达成目标）比日常行为（吃饭/休息）更重要
        - 后续影响：可能改变角色未来行为的事件更重要

        Args:
            character_name: 角色名
            content: 记忆内容
            action_id: Action ID
            reason: 决策理由
            mood: 当前情绪
            location: 当前位置
            fallback_importance: 调用方规则计算的评分，LLM 评分任一环节失败时原样返回

        Returns:
            重要性评分 1-10，失败时返回 fallback_importance（调用方规则分）
        """
        from src.runtime import get_prompts

        prompts = self._prompts or get_prompts()
        if not prompts:
            logger.warning("memory_score_prompts_unavailable", fallback=fallback_importance)
            return fallback_importance
        prompt = prompts.render(
            "memory_score",
            character_name=character_name,
            location=location or "未知",
            action_id=action_id or "未知",
            reason=reason or "无",
            mood=mood or "平静",
            content=content,
        )

        try:
            response = await self.llm.chat(prompt)
            # 提取数字（容错：LLM 可能返回 "7" 或 "7分" 或 "重要性：7"）
            match = re.search(r"\b(\d+)\b", response.strip())
            if match:
                score = int(match.group(1))
                return max(1, min(10, score))
            logger.warning(
                "llm_importance_parse_failed",
                response=response[:100],
                fallback=fallback_importance,
            )
            return fallback_importance
        except Exception as e:
            logger.warning(
                "llm_importance_scoring_failed",
                error=str(e),
                fallback=fallback_importance,
            )
            return fallback_importance

    async def create_episode(
        self,
        character_id: UUID,
        content: str,
        action_id: str | None = None,
        location: str | None = None,
        importance: int = 5,
        character_name: str | None = None,
        reason: str | None = None,
        mood: str | None = None,
        related_characters: list[UUID] | None = None,
        source_type: str = "action",
    ) -> MemoryEpisode | None:
        """创建记忆片段

        ⚠️ embedding 由 EmbeddingWorker 异步生成，此处不阻塞 Tick 循环。
        新记忆 materialized=false, embedding=NULL，worker 批量拉取后调 LLM 生成。

        重要性评分：
        - 若 MEMORY_LLM_SCORING_ENABLED=true 且提供 character_name，
          调用 LLM 评分（更精准），失败时回退到传入的 importance
        - 否则使用调用方计算的规则评分 importance

        写入去重：内容归一化（折叠空白）后与近 24 小时记忆比对，
        命中则跳过写入返回 None——抑制重复行为产生的近似重复记忆膨胀。

        Args:
            character_id: 角色 ID
            content: 记忆内容（自然语言描述）
            action_id: 关联 Action ID
            location: 发生场景
            importance: 规则评分（1-10），LLM 评分启用时作为回退值
            character_name: 角色名（LLM 评分所需）
            reason: 决策理由（LLM 评分所需）
            mood: 当前情绪（LLM 评分所需）
            related_characters: 共同经历/消息来源的角色 ID 列表
                （群体动力学：同场景在场者或传闻来源好友）
            source_type: 来源类型（action=自身行为 / gossip=传闻第二手记忆）

        Returns:
            MemoryEpisode 实体；重复内容返回 None
        """
        normalized_content = " ".join(content.split())
        if await self.repo.exists_recent_duplicate(character_id, normalized_content):
            logger.info(
                "memory_duplicate_skipped",
                character_id=str(character_id),
                content_preview=normalized_content[:80],
            )
            return None

        final_importance = importance

        # LLM 评分（可选，环境变量控制）
        if settings.memory_llm_scoring_enabled and character_name:
            final_importance = await self.score_importance_with_llm(
                character_name=character_name,
                content=content,
                action_id=action_id,
                reason=reason,
                mood=mood,
                location=location,
                fallback_importance=importance,
            )
            logger.info(
                "memory_importance_llm_scored",
                character_id=str(character_id),
                rule_importance=importance,
                llm_importance=final_importance,
            )

        episode = MemoryEpisode(
            character_id=character_id,
            content=content,
            embedding=None,  # 异步 worker 生成
            materialized=False,  # 标记为未向量化
            importance=final_importance,
            timestamp=datetime.now(UTC),
            action_id=action_id,
            location=location,
            related_characters=related_characters or [],
            source_type=source_type,
        )

        saved = await self.repo.add(episode)
        logger.info(
            "memory_episode_created",
            character_id=str(character_id),
            importance=final_importance,
            scoring_method="llm" if settings.memory_llm_scoring_enabled and character_name else "rule",
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
