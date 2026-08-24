"""传闻传播服务 - 群体动力学的核心机制

好友的高重要性经历以「第二手记忆」扩散到角色：
- 内容取自源记忆原文（模板拼接，非 LLM 编造，保证事实不漂移）
- importance 减半：传闻保真度随传播递减
- 每好友每窗口最多传播 1 条：复用 source_type + related_characters 列去重，
  不引入新表（少加概念）；窗口语义 = 「同一好友的连续八卦合并为一条印象」
- 传闻经既有 search_hybrid 管线自然回流后续决策，无需额外注入路径
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.config import settings
from src.db.models import Character, MemoryEpisode, Relation
from src.memory.episode_service import EpisodeService

logger = get_logger()

# 传闻正文截断：第二手记忆只需保留事件主干
_GOSSIP_CONTENT_MAX_CHARS = 120


class GossipService:
    """从好友的高重要性记忆中生成角色的第二手记忆"""

    def __init__(self, session: AsyncSession, episode_service: EpisodeService):
        self.session = session
        self.episode_service = episode_service

    async def propagate_from_friends(self, character_id: UUID) -> int:
        """拉取好友近窗内的显著经历，为角色创建传闻记忆

        Returns:
            实际创建的传闻条数（0 = 本 Tick 无可传播内容）
        """
        cfg = settings
        if not cfg.gossip_enabled:
            return 0

        friends = await self._friend_ids(character_id, min_strength=cfg.gossip_relation_min)
        if not friends:
            return 0

        cutoff = datetime.now(UTC) - timedelta(hours=cfg.gossip_window_hours)
        sources = await self._notable_episodes(friends, cutoff)
        if not sources:
            return 0

        propagated = 0
        for source in sources:
            if propagated >= cfg.gossip_max_per_tick:
                break
            friend_id = source.character_id
            # 去重键 = (好友, 窗口)：已有该好友的传闻则本窗口不再重复
            if await self._already_heard(character_id, friend_id, cutoff):
                continue

            created = await self._create_second_hand(
                character_id=character_id,
                source=source,
                importance=max(2, source.importance // 2),
            )
            if created is not None:
                propagated += 1

        if propagated:
            logger.info("gossip_propagated", character_id=str(character_id), count=propagated)
        return propagated

    async def _friend_ids(self, character_id: UUID, min_strength: int) -> list[UUID]:
        """关系强度达标的好友列表（传闻沿既有社交关系流动）"""
        stmt = select(Relation.target_id).where(
            Relation.character_id == character_id,
            Relation.strength >= min_strength,
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def _notable_episodes(self, friend_ids: list[UUID], cutoff: datetime) -> list[MemoryEpisode]:
        """好友近窗内达到重要性门槛的经历，按重要性降序"""
        stmt = (
            select(MemoryEpisode)
            .join(Character, Character.id == MemoryEpisode.character_id)
            .where(
                MemoryEpisode.character_id.in_(friend_ids),
                MemoryEpisode.timestamp >= cutoff,
                MemoryEpisode.importance >= settings.gossip_importance_threshold,
                # 传闻不再二次传播：防止 A 听说的八卦被 B 当亲身经历继续扩散失真
                MemoryEpisode.source_type == "action",
                Character.is_active.is_(True),
            )
            .order_by(MemoryEpisode.importance.desc(), MemoryEpisode.timestamp.desc())
            .limit(10)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _already_heard(self, character_id: UUID, friend_id: UUID, cutoff: datetime) -> bool:
        stmt = (
            select(func.count())
            .select_from(MemoryEpisode)
            .where(
                MemoryEpisode.character_id == character_id,
                MemoryEpisode.source_type == "gossip",
                MemoryEpisode.timestamp >= cutoff,
                # PostgreSQL UUID[] 包含判断：related_characters @> ARRAY[friend_id]
                MemoryEpisode.related_characters.contains([friend_id]),
            )
        )
        return bool(await self.session.scalar(stmt))

    async def _create_second_hand(
        self, character_id: UUID, source: MemoryEpisode, importance: int
    ) -> MemoryEpisode | None:
        friend_name = await self.session.scalar(select(Character.name).where(Character.id == source.character_id))
        if not friend_name:
            return None
        content = f"听{friend_name}说：{source.content[:_GOSSIP_CONTENT_MAX_CHARS]}"
        return await self.episode_service.create_episode(
            character_id,
            content,
            importance=importance,
            location=None,
            related_characters=[source.character_id],
            source_type="gossip",
        )
