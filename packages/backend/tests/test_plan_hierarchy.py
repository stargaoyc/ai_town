"""Plan 层级体系测试 - LLM 新建计划归一化 + daily 滚动过期（B2/B3）"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.config import settings
from src.core.character.plan_applier import PlanChangeApplier, _is_event_plan
from src.db.models import Plan


class TestNormalizePlanCreates:
    def test_valid_create_normalized(self) -> None:
        out = PlanChangeApplier.normalize_creates(
            [{"title": "去图书馆还书", "type": "daily", "priority": 4, "deadline": "2026-08-25T18:00:00"}]
        )
        assert len(out) == 1
        assert out[0]["title"] == "去图书馆还书"
        assert out[0]["type"] == "daily"
        assert out[0]["priority"] == 4
        assert out[0]["deadline"] is not None

    def test_defaults_and_clamps(self) -> None:
        out = PlanChangeApplier.normalize_creates(
            [{"title": "无类型计划", "priority": 99}, {"title": "负优先级", "priority": -3}]
        )
        assert [p["priority"] for p in out] == [5, 1]
        assert all(p["type"] == "short_term" for p in out)
        assert all(p["deadline"] is None for p in out)

    def test_invalid_type_and_empty_title_skipped(self) -> None:
        out = PlanChangeApplier.normalize_creates(
            [
                {"title": "坏类型", "type": "weekly"},
                {"title": "   "},
                {"description": "没有标题"},
                {"title": "合法计划", "type": "daily"},
            ]
        )
        assert len(out) == 1
        assert out[0]["title"] == "合法计划"

    def test_capped_at_three_per_decision(self) -> None:
        creates = [{"title": f"计划{i}"} for i in range(6)]
        assert len(PlanChangeApplier.normalize_creates(creates)) == 3

    def test_bad_deadline_becomes_none(self) -> None:
        out = PlanChangeApplier.normalize_creates([{"title": "x", "deadline": "下周三"}])
        assert out[0]["deadline"] is None

    def test_reason_passed_through(self) -> None:
        """0025：reason 字段应完整传递"""
        out = PlanChangeApplier.normalize_creates([{"title": "去看画展", "reason": "结衣奈邀请我一起去看画展"}])
        assert out[0]["reason"] == "结衣奈邀请我一起去看画展"

    def test_reason_empty_skipped(self) -> None:
        """空 reason 应被跳过（None）"""
        out = PlanChangeApplier.normalize_creates([{"title": "x", "reason": "   "}])
        assert out[0]["reason"] is None


class TestIsDuplicate:
    def test_duplicate_title_same_deadline_window(self) -> None:
        """标题相似且 deadline 在同一时间窗内 → 判重"""
        now = datetime.now(UTC)
        existing = [MagicMock(title="明天九点车站见去结衣奈家看画展", deadline=now, spec=Plan)]
        assert PlanChangeApplier._is_duplicate(
            {"title": "明天九点车站见去结衣奈家看画展", "deadline": now + timedelta(hours=6)},
            existing,
        )

    def test_duplicate_title_different_deadline_not_duplicate(self) -> None:
        """标题相似但 deadline 差超过时间窗 → 不判重（改期/不同日期的安排）"""
        now = datetime.now(UTC)
        existing = [MagicMock(title="明天九点车站见去结衣奈家看画展", deadline=now, spec=Plan)]
        assert not PlanChangeApplier._is_duplicate(
            {"title": "明天九点车站见去结衣奈家看画展", "deadline": now + timedelta(days=3)},
            existing,
        )

    def test_different_titles_not_duplicate(self) -> None:
        """不同标题 → 不判重"""
        existing = [MagicMock(title="看画展", deadline=datetime.now(UTC), spec=Plan)]
        assert not PlanChangeApplier._is_duplicate(
            {"title": "去图书馆", "deadline": datetime.now(UTC)},
            existing,
        )


class TestAutoComplete:
    @pytest.mark.asyncio
    async def test_action_matches_plan_completes(self) -> None:
        """行动证据与计划标题重叠达阈值 → 自动完成"""
        plan_id = uuid4()
        plan = MagicMock(
            id=plan_id,
            title="去车站见结衣奈看画展",
            type="short_term",
            reason=None,
            deadline=datetime.now(UTC),
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_plans = AsyncMock(return_value=[plan])

        from src.actions import DecisionResult

        decision = DecisionResult(action="move", reason="去车站见结衣奈看画展")

        now = datetime.now(UTC)
        completed = await PlanChangeApplier.auto_complete(plan_repo, uuid4(), decision, "station", now)

        assert completed == 1
        plan_repo.update_plan_scoped.assert_awaited_once()
        call_args = plan_repo.update_plan_scoped.await_args.args
        assert call_args[0] == plan_id
        call_kwargs = plan_repo.update_plan_scoped.await_args.kwargs
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["progress"] == 100

    @pytest.mark.asyncio
    async def test_action_not_matching_skips(self) -> None:
        """行动证据与计划不匹配 → 不完成"""
        plan = MagicMock(
            title="去看画展",
            type="short_term",
            reason=None,
            deadline=datetime.now(UTC),
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_plans = AsyncMock(return_value=[plan])

        from src.actions import DecisionResult

        decision = DecisionResult(action="sleep", reason="累了想睡觉")

        now = datetime.now(UTC)
        completed = await PlanChangeApplier.auto_complete(plan_repo, uuid4(), decision, "home", now)

        assert completed == 0
        plan_repo.update_plan_scoped.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_plan_not_auto_completed(self) -> None:
        """任务型计划（准备/整理/练习）即使行动匹配也不 auto_complete——
        单次行动只应推进进度（auto_progress），直接完成会错误终结未完成任务"""
        plan = MagicMock(
            title="准备画展草稿和甜品",
            type="short_term",
            reason=None,
            deadline=datetime.now(UTC),
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_plans = AsyncMock(return_value=[plan])

        from src.actions import DecisionResult

        decision = DecisionResult(action="work", reason="准备画展草稿和甜品")

        now = datetime.now(UTC)
        completed = await PlanChangeApplier.auto_complete(plan_repo, uuid4(), decision, "home", now)

        assert completed == 0
        plan_repo.update_plan_scoped.assert_not_awaited()


class TestIsEventPlan:
    def test_event_plan_identifies_event_keywords(self) -> None:
        """含事件关键词的计划被识别为事件型"""
        assert _is_event_plan("明天去车站见结衣奈看画展")
        assert _is_event_plan("参加祭典活动")
        assert _is_event_plan("出发去京都旅行")

    def test_task_plan_not_event(self) -> None:
        """不含事件关键词的计划不被识别为事件型"""
        assert not _is_event_plan("整理明日出行行李")
        assert not _is_event_plan("准备画展草稿和甜品")
        assert not _is_event_plan("练习钢琴曲")


class TestRemedyShortTermPlans:
    @pytest.mark.asyncio
    async def test_event_plan_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """事件型计划 deadline 已过 → 过期（不可补救）"""
        monkeypatch.setattr(settings, "plan_remedy_enabled", True)
        monkeypatch.setattr(settings, "plan_remedy_max_extends", 2)

        plan = MagicMock(
            id=uuid4(),
            title="明天九点车站见去结衣奈家看画展",
            type="short_term",
            deadline=datetime.now(UTC) - timedelta(days=1),
            extend_count=0,
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_short_term = AsyncMock(return_value=[plan])

        handled = await PlanChangeApplier.remedy_short_term_plans(plan_repo, uuid4(), datetime.now(UTC))

        assert handled == 1
        plan_repo.mark_expired.assert_awaited_once()
        plan_repo.extend_deadline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_plan_extended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """任务型计划 deadline 已过 → 顺延（不新增行）"""
        monkeypatch.setattr(settings, "plan_remedy_enabled", True)
        monkeypatch.setattr(settings, "plan_remedy_max_extends", 2)
        monkeypatch.setattr(settings, "plan_remedy_extend_hours", 24)

        plan = MagicMock(
            id=uuid4(),
            title="整理明日出行行李",
            type="short_term",
            deadline=datetime.now(UTC) - timedelta(hours=6),
            extend_count=0,
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_short_term = AsyncMock(return_value=[plan])

        handled = await PlanChangeApplier.remedy_short_term_plans(plan_repo, uuid4(), datetime.now(UTC))

        assert handled == 1
        plan_repo.extend_deadline.assert_awaited_once()
        plan_repo.mark_expired.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_max_extends_force_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """顺延已达上限 → 强制过期（防无限顺延）"""
        monkeypatch.setattr(settings, "plan_remedy_enabled", True)
        monkeypatch.setattr(settings, "plan_remedy_max_extends", 2)

        plan = MagicMock(
            id=uuid4(),
            title="整理行李",
            type="short_term",
            deadline=datetime.now(UTC) - timedelta(hours=6),
            extend_count=2,
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_short_term = AsyncMock(return_value=[plan])

        handled = await PlanChangeApplier.remedy_short_term_plans(plan_repo, uuid4(), datetime.now(UTC))

        assert handled == 1
        plan_repo.mark_expired.assert_awaited_once()
        plan_repo.extend_deadline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_over_limit_prefers_overdue_then_fifo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """超限时优先过期已过期计划，避免误伤未到期（边界问题A）"""
        monkeypatch.setattr(settings, "plan_remedy_enabled", True)
        monkeypatch.setattr(settings, "plan_max_active_short_term", 2)

        now = datetime.now(UTC)
        overdue = MagicMock(
            id=uuid4(),
            title="过期任务",
            type="short_term",
            deadline=now - timedelta(hours=2),
            extend_count=0,
            spec=Plan,
        )
        v1 = MagicMock(
            id=uuid4(),
            title="准备草稿",
            type="short_term",
            deadline=now + timedelta(days=3),
            extend_count=0,
            spec=Plan,
        )
        v2 = MagicMock(
            id=uuid4(),
            title="练习钢琴",
            type="short_term",
            deadline=now + timedelta(days=4),
            extend_count=0,
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_short_term = AsyncMock(return_value=[overdue, v1, v2])

        handled = await PlanChangeApplier.remedy_short_term_plans(plan_repo, uuid4(), now)

        assert handled == 1
        expired_ids = [c.args[0] for c in plan_repo.mark_expired.await_args_list]
        assert overdue.id in expired_ids
        assert v1.id not in expired_ids
        assert v2.id not in expired_ids
        plan_repo.extend_deadline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_over_limit_fifo_after_overdue_exhausted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """已过期计划不足超限额度时，淘汰最旧未过期（FIFO）"""
        monkeypatch.setattr(settings, "plan_remedy_enabled", True)
        monkeypatch.setattr(settings, "plan_max_active_short_term", 1)

        now = datetime.now(UTC)
        old = MagicMock(
            id=uuid4(),
            title="整理行李",
            type="short_term",
            deadline=now + timedelta(days=1),
            extend_count=0,
            spec=Plan,
        )
        mid = MagicMock(
            id=uuid4(),
            title="准备草稿",
            type="short_term",
            deadline=now + timedelta(days=2),
            extend_count=0,
            spec=Plan,
        )
        new = MagicMock(
            id=uuid4(),
            title="练习钢琴",
            type="short_term",
            deadline=now + timedelta(days=3),
            extend_count=0,
            spec=Plan,
        )
        plan_repo = AsyncMock()
        plan_repo.get_active_short_term = AsyncMock(return_value=[old, mid, new])

        handled = await PlanChangeApplier.remedy_short_term_plans(plan_repo, uuid4(), now)

        assert handled == 2
        expired_ids = [c.args[0] for c in plan_repo.mark_expired.await_args_list]
        assert old.id in expired_ids and mid.id in expired_ids
        assert new.id not in expired_ids
