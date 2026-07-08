"""角色关系图谱

管理角色间的关系，包括首次互动初始化、关系更新、关系查询。
基于有向图模型：A→B 和 B→A 是两条独立记录。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.relation import Relation
from src.db.repositories.relation_repo import RelationRepository

logger = logging.getLogger(__name__)


@dataclass
class RelationSnapshot:
    """关系快照（单向：character 对 target 的认知）"""

    character_id: UUID
    target_id: UUID
    relationship_type: str
    strength: int          # 关系强度 0-100
    last_interaction_at: datetime | None
    notes: str | None


class RelationGraph:
    """角色关系图谱

    用法：
        graph = RelationGraph(session, redis)
        # 首次互动初始化（双向）
        await graph.ensure_relation(char_a, char_b)
        # 互动后更新（双向同步）
        await graph.update_on_interaction(char_a, char_b, strength_delta=+5)
        # 查询 A 对 B 的关系
        snapshot = await graph.get_relation(char_a, char_b)

    职责：
    1. 首次互动时创建双向关系记录（默认 stranger）
    2. 互动后更新关系强度，自动升级关系类型
    3. Redis 缓存关系快照，PG 持久化
    """

    # Redis key: rel:{character_id}:{target_id} -> Hash
    RELATION_KEY = "rel:{character_id}:{target_id}"

    # 关系升级阈值（基于 strength）
    UPGRADE_THRESHOLDS: list[tuple[int, str]] = [
        (90, "best_friend"),
        (70, "close_friend"),
        (40, "friend"),
        (20, "acquaintance"),
        (0, "stranger"),
    ]

    def __init__(self, session: AsyncSession, redis: Redis):
        self.session = session
        self.redis = redis
        self.repo = RelationRepository(session)

    async def ensure_relation(
        self, char_a: UUID, char_b: UUID
    ) -> tuple[Relation, Relation]:
        """确保两个角色间存在双向关系记录

        如果不存在则创建默认关系（stranger, strength=20）。

        Args:
            char_a: 角色 A ID
            char_b: 角色 B ID

        Returns:
            (A→B 关系, B→A 关系)
        """
        # 创建双向关系
        rel_ab = await self.repo.get_or_create(char_a, char_b)
        rel_ba = await self.repo.get_or_create(char_b, char_a)

        # 缓存到 Redis
        await self._cache_relation(rel_ab)
        await self._cache_relation(rel_ba)

        logger.debug("关系已确保: %s <-> %s", char_a, char_b)
        return rel_ab, rel_ba

    async def update_on_interaction(
        self,
        char_a: UUID,
        char_b: UUID,
        strength_delta: int = 0,
        notes: str | None = None,
    ) -> tuple[RelationSnapshot, RelationSnapshot]:
        """互动后更新双向关系

        Args:
            char_a: 角色 A
            char_b: 角色 B
            strength_delta: 关系强度变化（双向同步）
            notes: 对对方的认知笔记更新

        Returns:
            (A→B 快照, B→A 快照)
        """
        # 确保关系存在
        rel_ab, rel_ba = await self.ensure_relation(char_a, char_b)

        now = datetime.now(timezone.utc)

        # 更新 A→B
        new_strength_ab = self._clamp(rel_ab.strength + strength_delta, 0, 100)
        new_type_ab = self._determine_type(new_strength_ab)
        await self.repo.update_relation(
            char_a, char_b,
            strength=new_strength_ab,
            relationship_type=new_type_ab,
            last_interaction_at=now,
            notes=notes,
        )

        # 更新 B→A
        new_strength_ba = self._clamp(rel_ba.strength + strength_delta, 0, 100)
        new_type_ba = self._determine_type(new_strength_ba)
        await self.repo.update_relation(
            char_b, char_a,
            strength=new_strength_ba,
            relationship_type=new_type_ba,
            last_interaction_at=now,
            notes=notes,
        )

        # 更新 Redis 缓存
        rel_ab.strength = new_strength_ab
        rel_ab.relationship_type = new_type_ab
        rel_ab.last_interaction_at = now
        rel_ba.strength = new_strength_ba
        rel_ba.relationship_type = new_type_ba
        rel_ba.last_interaction_at = now
        await self._cache_relation(rel_ab)
        await self._cache_relation(rel_ba)

        # 记录升级
        if new_type_ab != rel_ab.relationship_type:
            logger.info(
                "关系升级: %s -> %s: %s",
                char_a, char_b, new_type_ab,
            )

        return (
            self._to_snapshot(rel_ab),
            self._to_snapshot(rel_ba),
        )

    async def get_relation(
        self, character_id: UUID, target_id: UUID
    ) -> RelationSnapshot | None:
        """查询单向关系快照（优先 Redis）"""
        # 先查 Redis
        key = self.RELATION_KEY.format(
            character_id=character_id, target_id=target_id
        )
        cached = await self.redis.hgetall(key)
        if cached:
            return RelationSnapshot(
                character_id=character_id,
                target_id=target_id,
                relationship_type=cached.get("relationship_type", "stranger"),
                strength=int(cached.get("strength", 20)),
                last_interaction_at=None,
                notes=cached.get("notes"),
            )

        # Redis 未命中，查 PG
        rel = await self.repo.get_or_create(character_id, target_id)
        await self._cache_relation(rel)
        return self._to_snapshot(rel)

    async def get_all_relations(
        self, character_id: UUID
    ) -> list[RelationSnapshot]:
        """获取角色的所有出向关系"""
        relations = await self.repo.get_relations(character_id)
        return [self._to_snapshot(r) for r in relations]

    def _determine_type(self, strength: int) -> str:
        """根据强度判定关系类型"""
        for threshold, rel_type in self.UPGRADE_THRESHOLDS:
            if strength >= threshold:
                return rel_type
        return "stranger"

    async def _cache_relation(self, relation: Relation) -> None:
        """缓存关系到 Redis"""
        key = self.RELATION_KEY.format(
            character_id=relation.character_id,
            target_id=relation.target_id,
        )
        await self.redis.hset(key, mapping={
            "relationship_type": relation.relationship_type,
            "strength": str(relation.strength),
            "notes": relation.notes or "",
        })

    @staticmethod
    def _to_snapshot(relation: Relation) -> RelationSnapshot:
        """模型转快照"""
        return RelationSnapshot(
            character_id=relation.character_id,
            target_id=relation.target_id,
            relationship_type=relation.relationship_type,
            strength=relation.strength,
            last_interaction_at=relation.last_interaction_at,
            notes=relation.notes,
        )

    @staticmethod
    def _clamp(value: int, min_val: int, max_val: int) -> int:
        """限制值范围"""
        return max(min_val, min(max_val, value))
