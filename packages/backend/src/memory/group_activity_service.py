"""群活动持久化服务 - 群体动力学 Phase 2

为一次临时聚会的所有参与者写共同经历记忆（related_characters 互指），
并给两两关系小幅加固（+2，上限 100）。记忆内容取自集体叙事原文，
不经过逐人 LLM 转述——事实同源，观感各异的部分留给后续反思。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Relation
from src.memory.episode_service import EpisodeService

logger = get_logger(__name__)

_RELATION_BOOST = 2
_NARRATIVE_IN_MEMORY_MAX_CHARS = 150
# 每对角色的群活动好感加成日限额（R4-M16）：反复同场不再机械刷好感，
# 25h TTL 覆盖跨日边界
_PAIR_BOOST_TTL_SECONDS = 25 * 3600


class GroupActivityService:
    """把一次群活动的叙事落成全体参与者的共同经历与关系加固"""

    def __init__(self, session: AsyncSession, episode_service: EpisodeService):
        self.session = session
        self.episode_service = episode_service

    async def persist(
        self,
        *,
        initiator_id: UUID,
        participants: list[dict[str, Any]],
        location: str,
        narrative: str,
        importance: int = 6,
        redis: Any | None = None,
    ) -> int:
        """为每个参与者写共同经历记忆并两两加固关系

        Args:
            participants: [{"id": "<UUID 字符串>", "name": "<名字>"}, ...]（含发起者）
            narrative: 集体活动叙事（LLM 生成或模板回退）
            redis: 传入时启用每对每日加成限额（R4-M16）

        Returns:
            写入的记忆条数（参与者数；个别被去重跳过时更少）
        """
        written = 0
        for participant in participants:
            pid = UUID(participant["id"])
            others = [p for p in participants if p["id"] != participant["id"]]
            others_ids = [UUID(p["id"]) for p in others]
            others_names = "、".join(p["name"] for p in others)
            content = (
                f"和{others_names}在{location}一起聚会：{narrative[:_NARRATIVE_IN_MEMORY_MAX_CHARS]}"
                if others
                else f"在{location}独自待了一会儿：{narrative[:_NARRATIVE_IN_MEMORY_MAX_CHARS]}"
            )
            episode = await self.episode_service.create_episode(
                pid,
                content,
                action_id="group_activity",
                location=location,
                importance=importance,
                related_characters=others_ids,
            )
            if episode is not None:
                written += 1

        await self._boost_relations(participants, redis=redis)
        # 不在此处 commit：调用方持有会话生命周期
        # （引擎经 db.session 退出自动提交；测试用回滚隔离）
        logger.info(
            "group_activity_persisted",
            initiator=str(initiator_id),
            participants=len(participants),
            memories_written=written,
        )
        return written

    async def _boost_relations(self, participants: list[dict[str, Any]], redis: Any | None = None) -> None:
        """两两关系 +2（上限 100），双向同步；缺失的关系行先按默认值创建

        传入 redis 时启用每对每日一次限额（R4-M16）：反复同场的临时聚会
        不再机械累积好感，关系增长必须由对话质量驱动。
        """
        ids = [p["id"] for p in participants]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = UUID(ids[i]), UUID(ids[j])
                for src, dst in ((a, b), (b, a)):
                    if redis is not None and not await self._acquire_pair_boost_budget(redis, src, dst):
                        logger.debug(
                            "group_activity_pair_boost_capped",
                            src=str(src),
                            dst=str(dst),
                        )
                        continue
                    existing = await self.session.scalar(
                        select(Relation.strength).where(Relation.character_id == src, Relation.target_id == dst)
                    )
                    if existing is None:
                        # 陌生角色因共同活动结识：默认强度 20 起步再叠加本次加成
                        self.session.add(
                            Relation(
                                character_id=src,
                                target_id=dst,
                                strength=20 + _RELATION_BOOST,
                            )
                        )
                    else:
                        stmt = (
                            update(Relation)
                            .where(Relation.character_id == src, Relation.target_id == dst)
                            .values(strength=func.least(100, Relation.strength + _RELATION_BOOST))
                        )
                        await self.session.execute(stmt)

    @staticmethod
    async def _acquire_pair_boost_budget(redis: Any, src: UUID, dst: UUID) -> bool:
        """尝试占用该方向今日的加成名额；当日已加固过则返回 False"""
        day = datetime.now(UTC).strftime("%Y%m%d")
        key = f"groupact:rel:{src}:{dst}:{day}"
        acquired = await redis.set(key, "1", nx=True, ex=_PAIR_BOOST_TTL_SECONDS)
        return bool(acquired)


def parse_group_narrative(raw: str) -> str | None:
    """解析 LLM 集体叙事输出；失败返回 None（调用方使用模板回退）"""
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.split("\n") if not ln.startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    narrative = parsed.get("narrative") if isinstance(parsed, dict) else None
    return narrative.strip() if isinstance(narrative, str) and narrative.strip() else None
