"""计划变更应用器（round-7 E2：从 CharacterTickEngine 拆出的计划域）

把「LLM 决策的 planChanges / createPlanChanges 落库」与「计划-行动启发式对账」
从 Tick 引擎中独立出来，职责单一、可独立测试。character_id 由调用方传入，
更新一律经 PlanRepository 的 character_id 作用域方法，防跨角色篡改。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.actions import DecisionResult
from src.config import settings
from src.db.repositories import PlanRepository

logger = get_logger(__name__)

_PLAN_TYPE_WHITELIST = frozenset({"long_term", "short_term", "daily"})
_PLAN_CREATE_MAX_PER_DECISION = 3


class PlanChangeApplier:
    """计划变更应用器：planChanges / createPlanChanges 落库 + 自动对账"""

    @staticmethod
    async def apply_changes(
        plan_repo: PlanRepository,
        character_id: UUID,
        changes: list[dict[str, Any]],
    ) -> None:
        """将 LLM 决策的 planChanges 应用到 plans 表

        LLM 可携带任意 planId，更新必须以 character_id 约束范围防跨角色篡改；
        单条变更失败仅告警，不回滚整个 Action 事务。
        """
        status_map = {"complete": "completed", "abandon": "abandoned", "update": "active"}
        for change in changes:
            if not isinstance(change, dict):
                continue
            try:
                plan_id = UUID(str(change.get("planId") or ""))
            except (ValueError, TypeError):
                logger.warning("plan_change_invalid_id", plan_id=str(change.get("planId")))
                continue

            action_raw = change.get("action")
            updates: dict[str, Any] = {}
            # 仅在 LLM 显式给出 action 时才变更 status：缺省归为 update 会把
            # 只有 planId 的条目错误地「复活」为 active（单测发现的边界缺陷）
            if action_raw is not None:
                mapped = status_map.get(str(action_raw).lower())
                if mapped is not None:
                    updates["status"] = mapped
            progress = change.get("progress")
            if isinstance(progress, int) and not isinstance(progress, bool):
                updates["progress"] = max(0, min(100, progress))
            # P1-13：放开 title/priority/deadline 修改——此前计划一经创建
            # 只能动 status/progress，目标漂移后只能废弃重建
            new_title = change.get("title")
            if isinstance(new_title, str) and new_title.strip():
                updates["title"] = new_title.strip()[:200]
            new_priority = change.get("priority")
            if isinstance(new_priority, int) and not isinstance(new_priority, bool):
                updates["priority"] = max(1, min(5, new_priority))
            raw_deadline = change.get("deadline")
            if isinstance(raw_deadline, str) and raw_deadline.strip():
                try:
                    updates["deadline"] = datetime.fromisoformat(raw_deadline.strip())
                except ValueError:
                    logger.warning("plan_change_invalid_deadline", deadline=raw_deadline)
            if not updates:
                continue

            applied = await plan_repo.update_plan_scoped(plan_id, character_id, **updates)
            if not applied:
                logger.warning("plan_change_target_not_found", plan_id=str(plan_id), character_id=str(character_id))

    @staticmethod
    def normalize_creates(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """归一化 LLM 新建计划条目：类型白名单/优先级钳制/标题截断/截止日解析

        与 apply_changes 同样的容错哲学：非法条目跳过并告警，不抛异常。
        """
        normalized: list[dict[str, Any]] = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            title = change.get("title")
            if not isinstance(title, str) or not title.strip():
                logger.warning("plan_create_invalid_title", title=str(title))
                continue
            plan_type = str(change.get("type") or "short_term").lower()
            if plan_type not in _PLAN_TYPE_WHITELIST:
                logger.warning("plan_create_invalid_type", type=plan_type)
                continue
            priority = change.get("priority")
            priority_value = (
                max(1, min(5, priority)) if isinstance(priority, int) and not isinstance(priority, bool) else 3
            )
            deadline: datetime | None = None
            raw_deadline = change.get("deadline")
            if isinstance(raw_deadline, str) and raw_deadline.strip():
                try:
                    deadline = datetime.fromisoformat(raw_deadline.strip())
                except ValueError:
                    logger.warning("plan_create_invalid_deadline", deadline=raw_deadline)
            description = change.get("description")
            normalized.append(
                {
                    "title": title.strip()[:200],
                    "description": description.strip()[:2000] if isinstance(description, str) else None,
                    "type": plan_type,
                    "priority": priority_value,
                    "deadline": deadline,
                }
            )
        # 上限作用于「有效」条目——非法条目不占名额
        return normalized[:_PLAN_CREATE_MAX_PER_DECISION]

    @staticmethod
    def _char_bigram_overlap(a: str, b: str) -> float:
        """字符二元组 Jaccard 重叠（0-1）：中文无空格分词的轻量相似度"""
        bigrams_a = {a[i : i + 2] for i in range(len(a) - 1)} if len(a) > 1 else {a}
        bigrams_b = {b[i : i + 2] for i in range(len(b) - 1)} if len(b) > 1 else {b}
        union = bigrams_a | bigrams_b
        if not union:
            return 0.0
        return len(bigrams_a & bigrams_b) / len(union)

    @staticmethod
    async def auto_progress(
        plan_repo: PlanRepository,
        character_id: UUID,
        decision: DecisionResult,
    ) -> int:
        """启发式计划-行动对账（P1-13）

        对每个 active 计划计算标题与「决策理由+动作名」的二元组重叠，
        达到阈值即推进 delta 百分比。仅当 LLM 本轮未显式汇报该计划的
        进度时生效；推进上限 99——完成语义必须由 LLM 显式 complete 宣告。
        """
        evidence = f"{decision.reason or ''}{decision.action}"
        if not evidence.strip():
            return 0
        explicitly_touched = {
            str(change.get("planId"))
            for change in decision.plan_changes
            if isinstance(change, dict) and change.get("progress") is not None
        }
        advanced = 0
        for plan in await plan_repo.get_active_plans(character_id):
            if str(plan.id) in explicitly_touched:
                continue
            overlap = PlanChangeApplier._char_bigram_overlap(plan.title, evidence)
            if overlap < settings.plan_auto_progress_overlap:
                continue
            new_progress = min(99, plan.progress + settings.plan_auto_progress_delta)
            if new_progress <= plan.progress:
                continue
            await plan_repo.update_plan_scoped(plan.id, character_id, progress=new_progress)
            advanced += 1
        if advanced:
            logger.info("plans_auto_progressed", character_id=str(character_id), count=advanced)
        return advanced

    @staticmethod
    async def create_plans(
        plan_repo: PlanRepository,
        character_id: UUID,
        changes: list[dict[str, Any]],
    ) -> int:
        """将 LLM 决策的 createPlanChanges 落库为角色新计划（层级体系 B3）

        Returns:
            实际创建的计划数
        """
        created = 0
        for fields in PlanChangeApplier.normalize_creates(changes):
            await plan_repo.create_plan(character_id, **fields)
            created += 1
        if created:
            logger.info("plans_created_from_decision", character_id=str(character_id), count=created)
        return created
