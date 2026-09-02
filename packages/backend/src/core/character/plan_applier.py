"""计划变更应用器（round-7 E2：从 CharacterTickEngine 拆出的计划域）

把「LLM 决策的 planChanges / createPlanChanges 落库」与「计划-行动启发式对账」
从 Tick 引擎中独立出来，职责单一、可独立测试。character_id 由调用方传入，
更新一律经 PlanRepository 的 character_id 作用域方法，防跨角色篡改。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.actions import DecisionResult
from src.config import settings
from src.db.models import Plan
from src.db.repositories import PlanRepository

logger = get_logger(__name__)

_PLAN_TYPE_WHITELIST = frozenset({"long_term", "short_term", "daily"})
_PLAN_CREATE_MAX_PER_DECISION = 3

# 事件型计划关键词：一次性事件（看/去/见/参加/出发等），deadline 过了即不可补救
# （画展不会改期）。任务型计划（准备/整理/练习）可顺延。R9 闭环补救判定依据。
_EVENT_PLAN_KEYWORDS = frozenset(
    {
        "看",
        "去",
        "见",
        "参加",
        "出发",
        "前往",
        "参观",
        "见面",
        "赴",
        "启程",
        "抵达",
        "观看",
        "出席",
    }
)

# 任务型计划关键词：持续投入直到完成，deadline 过了可顺延（准备/整理/练习…）。
# 判定优先级高于事件关键词——「准备明天看画的草稿」含"看"但本质是任务，
# 若只按事件关键词匹配会被误判为事件型直接过期（R9 审查发现的误伤）。
_TASK_PLAN_KEYWORDS = frozenset(
    {
        "准备",
        "整理",
        "练习",
        "复习",
        "完成",
        "制作",
        "购买",
        "学习",
        "预习",
        "研究",
        "训练",
        "写",
        "做",
        "收拾",
        "采购",
        "补充",
        "修复",
        "维护",
        "充电",
        "备考",
        "设计",
        "编写",
        "策划",
        "安排",
        "背诵",
        "打扫",
        "烹饪",
    }
)


def _is_event_plan(title: str) -> bool:
    """判断计划是否为事件型（一次性事件，deadline 过后不可补救）

    事件型计划的语义是「在特定时间点发生一次」——错过即失效（如看画展、
    车站见面）。任务型计划是「持续投入直到完成」——可顺延（如整理行李）。

    判定顺序：先命中任务型关键词 → 任务型（可顺延）；再命中事件关键词 →
    事件型。任务型关键词优先，避免「准备明天看画的草稿」这类含事件动词
    的任务被误判为事件型而直接过期。
    """
    if any(kw in title for kw in _TASK_PLAN_KEYWORDS):
        return False
    return any(kw in title for kw in _EVENT_PLAN_KEYWORDS)


class PlanChangeApplier:
    """计划变更应用器：planChanges / createPlanChanges 落库 + 自动对账 + 补救审查"""

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
                    parsed_deadline = datetime.fromisoformat(raw_deadline.strip())
                    # LLM 输出的 deadline 通常不带时区（与 world_time 同为 +08 墙钟语义），
                    # 补 +08:00 使 Python 层与 DB 读出的 aware datetime 可安全比较
                    if parsed_deadline.tzinfo is None:
                        parsed_deadline = parsed_deadline.replace(tzinfo=timezone(timedelta(hours=8)))
                    deadline = parsed_deadline
                except ValueError:
                    logger.warning("plan_create_invalid_deadline", deadline=raw_deadline)
            description = change.get("description")
            # 0025：制定理由（与 action_records.reason 同源语义，供 auto_complete 对照）
            reason = change.get("reason")
            normalized.append(
                {
                    "title": title.strip()[:200],
                    "description": description.strip()[:2000] if isinstance(description, str) else None,
                    "reason": reason.strip()[:2000] if isinstance(reason, str) and reason.strip() else None,
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
        world_time: datetime | None = None,
    ) -> int:
        """启发式计划-行动对账（P1-13）

        对每个 active 计划计算标题与「决策理由+动作名」的二元组重叠，
        达到阈值即推进 delta 百分比。仅当 LLM 本轮未显式汇报该计划的
        进度时生效；推进上限 99——完成语义必须由 LLM 显式 complete 宣告。

        Args:
            plan_repo: 计划仓储
            character_id: 角色 ID
            decision: 本次决策
            world_time: 当前世界时间（可选）。提供时过滤 deadline 已过的计划，
                避免推进过期计划进度（与 auto_complete 的过滤语义一致）。
        """
        # evidence 只用自然语言 reason：action id 是工程标识符（如 move），
        # 参与 bigram 匹配会稀释中文语义重叠
        evidence = (decision.reason or "").strip()
        if not evidence:
            return 0
        explicitly_touched = {
            str(change.get("planId"))
            for change in decision.plan_changes
            if isinstance(change, dict) and change.get("progress") is not None
        }
        advanced = 0
        for plan in await plan_repo.get_active_plans(character_id, world_time=world_time):
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
        world_time: datetime | None = None,
    ) -> int:
        """将 LLM 决策的 createPlanChanges 落库为角色新计划（层级体系 B3）

        R9 防膨胀：创建前与现有 active 计划做相似去重——标题 bigram 重叠超阈值
        **且 deadline 落在同一时间窗内** 才视为重复跳过。仅标题相似但 deadline
        不同（改期 / 不同日期的同类安排，如「明天看画展」在世界时间推进后
        重新生成）是合法新计划，不误伤。传 world_time 时，已过期的计划不参与
        去重（将被 remedy 清理，不在同一时间窗内构成重复）。

        Returns:
            实际创建的计划数
        """
        created = 0
        existing = await plan_repo.get_active_plans(character_id, world_time=world_time)
        for fields in PlanChangeApplier.normalize_creates(changes):
            if PlanChangeApplier._is_duplicate(fields, existing):
                logger.info(
                    "plan_create_skipped_duplicate",
                    character_id=str(character_id),
                    title=fields.get("title"),
                )
                continue
            await plan_repo.create_plan(character_id, **fields)
            created += 1
        if created:
            logger.info("plans_created_from_decision", character_id=str(character_id), count=created)
        return created

    @staticmethod
    def _is_duplicate(fields: dict[str, Any], existing: Sequence[Plan]) -> bool:
        """判断新计划是否与现有 active 计划重复（标题相似 + deadline 同时间窗）

        标题 bigram 重叠 < 阈值 → 不同主题，不重复；
        标题相似但 deadline 差超过窗口 → 改期/不同日期的安排，不重复；
        都无 deadline 且标题相似 → 视为重复（长期模糊目标，防 LLM 反复建）。
        """
        title = str(fields.get("title") or "")
        if not title:
            return False
        deadline = fields.get("deadline")
        window = timedelta(hours=settings.plan_create_dedup_window_hours)
        for plan in existing:
            overlap = PlanChangeApplier._char_bigram_overlap(title, plan.title)
            if overlap < settings.plan_create_dedup_overlap:
                continue
            # 标题相似：再比较 deadline 时间窗
            if deadline is None and plan.deadline is None:
                return True
            if deadline is not None and plan.deadline is not None:
                gap = abs(deadline - plan.deadline)
                if gap <= window:
                    return True
            # 一个有 deadline 一个没有 → 不视为重复（时间语义不同）
        return False

    @staticmethod
    async def auto_complete(
        plan_repo: PlanRepository,
        character_id: UUID,
        decision: DecisionResult,
        location: str | None,
        world_time: datetime,
    ) -> int:
        """Action 执行后自动完成匹配计划（R9 闭环：行动→计划反馈）

        LLM 决策可能忘记显式 complete；此处用「行动证据」启发式兜底：
        计划标题与「reason+action+location」的字符二元组重叠达到阈值即视为
        该行动完成了此计划 → 置 completed（progress=100）。
        计划自带 reason（0025）时，行动 reason 与计划 reason 的标题级证据
        一并纳入匹配（双方各自取 bigram 最高重叠）。

        仅对 short_term / daily 生效；long_term 不会因单次行动被自动完成
        （长期目标需 LLM 显式判定）。

        Args:
            plan_repo: 计划仓储
            character_id: 角色 ID
            decision: 本次决策（含 reason/action）
            location: 行动发生地点
            world_time: 当前世界时间（过滤已过期计划）

        Returns:
            完成的计划数
        """
        if not settings.plan_auto_complete_enabled:
            return 0
        # evidence 只用自然语言（reason + location）：action id 是工程标识符
        # （如 move/chat_with），参与 bigram 匹配会稀释中文语义重叠
        evidence = f"{decision.reason or ''} {location or ''}".strip()
        if not evidence:
            return 0
        completed = 0
        for plan in await plan_repo.get_active_plans(character_id, world_time=world_time):
            if plan.type == "long_term":
                continue
            # 仅事件型计划可被单次行动完成（看画展/见面等一次即达成）；
            # 任务型计划（准备/整理/练习）需多次行动，单次行动只应推进进度
            # （auto_progress），直接置 completed=100 会错误终结未完成任务。
            if not _is_event_plan(plan.title):
                continue
            overlap = PlanChangeApplier._char_bigram_overlap(plan.title, evidence)
            # 计划 reason 与行动 reason 的互补证据（0025：双文本取最高重叠，
            # 标题短时匹配不足但理由语义对齐的情形可被 reason 兜住）
            if plan.reason:
                reason_overlap = PlanChangeApplier._char_bigram_overlap(plan.reason, evidence)
                overlap = max(overlap, reason_overlap)
            if overlap >= settings.plan_auto_complete_overlap:
                await plan_repo.update_plan_scoped(plan.id, character_id, status="completed", progress=100)
                completed += 1
                logger.info(
                    "plan_auto_completed",
                    character_id=str(character_id),
                    plan_id=str(plan.id),
                    overlap=overlap,
                    threshold=settings.plan_auto_complete_overlap,
                )
        if completed:
            logger.info("plans_auto_completed_total", character_id=str(character_id), count=completed)
        return completed

    @staticmethod
    async def remedy_short_term_plans(
        plan_repo: PlanRepository,
        character_id: UUID,
        world_now: datetime,
    ) -> int:
        """短期计划补救审查（R9 闭环：可补救→顺延，不可补救→过期，防膨胀）

        deadline 已过的 active short_term 计划按可补救性区分处理：
        - 事件型计划（看画展/车站见面等一次性事件）→ 不可补救 → expired
        - 任务型计划（整理/准备/练习等）→ 可补救 → deadline 顺延 extend_hours
          （同一条记录 UPDATE，不新增行——数量不增长）
        - 顺延达 max_extends 次 → 强制 expired（防无限顺延）
        - 该角色 active short_term 总数超上限 → 最旧计划强制 expired（硬防膨胀）

        Returns:
            处理条数（过期 + 顺延合计）
        """
        if not settings.plan_remedy_enabled:
            return 0
        plans = await plan_repo.get_active_short_term(character_id)
        handled = 0
        # 跟踪超限阶段已处理的计划 ID，避免主循环重复处理
        limit_handled_ids: set[UUID] = set()

        # 硬防膨胀：超上限时优先过期已过期计划，避免误伤未到期计划
        over_limit = len(plans) - settings.plan_max_active_short_term
        if over_limit > 0:
            expired_count = 0
            # 第一步：过期已过期的（deadline < world_now）
            for plan in plans:
                if expired_count >= over_limit:
                    break
                if plan.deadline is not None and plan.deadline < world_now:
                    await plan_repo.mark_expired(plan.id)
                    handled += 1
                    expired_count += 1
                    limit_handled_ids.add(plan.id)
                    logger.info(
                        "plan_expired_over_limit",
                        character_id=str(character_id),
                        plan_id=str(plan.id),
                        title=plan.title,
                        reason="overdue",
                    )
            # 第二步：仍超限时淘汰最旧未过期（FIFO）
            if expired_count < over_limit:
                for plan in plans:
                    if expired_count >= over_limit:
                        break
                    if plan.deadline is None or plan.deadline >= world_now:
                        await plan_repo.mark_expired(plan.id)
                        handled += 1
                        expired_count += 1
                        limit_handled_ids.add(plan.id)
                        logger.info(
                            "plan_expired_over_limit",
                            character_id=str(character_id),
                            plan_id=str(plan.id),
                            title=plan.title,
                            reason="fifo",
                        )

        for plan in plans:
            if plan.id in limit_handled_ids:
                continue  # 超限阶段已处理，跳过
            if plan.deadline is None or plan.deadline >= world_now:
                continue  # 未到期或无限期，保留
            if plan.extend_count >= settings.plan_remedy_max_extends:
                # 顺延已到上限：任务拖了太久仍未完成，强制过期收敛
                await plan_repo.mark_expired(plan.id)
                handled += 1
                logger.info(
                    "plan_expired_max_extends",
                    character_id=str(character_id),
                    plan_id=str(plan.id),
                    title=plan.title,
                    extend_count=plan.extend_count,
                )
                continue
            if _is_event_plan(plan.title):
                # 事件型：错过即失效（画展不会改期）
                await plan_repo.mark_expired(plan.id)
                handled += 1
                logger.info(
                    "plan_expired_event",
                    character_id=str(character_id),
                    plan_id=str(plan.id),
                    title=plan.title,
                )
                continue
            # 任务型：顺延 deadline（同一记录，不新增行）
            new_deadline = world_now + timedelta(hours=settings.plan_remedy_extend_hours)
            await plan_repo.extend_deadline(plan.id, new_deadline)
            handled += 1
            logger.info(
                "plan_remedied_extend",
                character_id=str(character_id),
                plan_id=str(plan.id),
                title=plan.title,
                new_deadline=new_deadline.isoformat(),
                extend_count=plan.extend_count + 1,
            )

        if handled:
            logger.info("plans_remedy_total", character_id=str(character_id), handled=handled)
        return handled
