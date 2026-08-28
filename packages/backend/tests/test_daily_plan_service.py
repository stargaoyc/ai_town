"""每日计划生成器单元测试（round-7 F1b）

覆盖：
- 活跃角色生成当日计划（LLM 结构化输出 → plans 表）
- 当日幂等（已存在 daily 计划则跳过）
- LLM 失败时跳过不中断
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pytest import MonkeyPatch

from src.llm.client import LLMClient
from src.llm.prompts import PromptTemplates
from src.memory import daily_plan_service as dps_module
from src.memory.daily_plan_service import DailyPlanService


class StubPrompts:
    def render(self, name: str, **kwargs: Any) -> str:
        return name


class StubLLM:
    def __init__(self, plans: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self._plans = plans or [{"title": "下午去咖啡店打工", "description": "攒钱买新画具"}]
        self.fail = fail

    async def structured_output(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("llm down")
        return {"plans": self._plans}


class StubPlanRepo:
    def __init__(self) -> None:
        self.plans: list[dict[str, Any]] = []
        self.created: list[tuple[UUID, dict[str, Any]]] = []

    async def get_active_plans(self, character_id: UUID) -> list[Any]:
        return [SimplePlan(p["type"], p["title"]) for p in self.plans if p["character_id"] == character_id]

    async def has_daily_plan_on(self, character_id: UUID, plan_date: Any) -> bool:
        return any(
            p["character_id"] == character_id and p.get("plan_date") == plan_date
            for p in self.plans
            if p["type"] == "daily"
        )

    async def create_plan(self, character_id: UUID, **fields: Any) -> Any:
        self.plans.append({"character_id": character_id, **fields})
        self.created.append((character_id, fields))
        return cast(Any, None)


class SimplePlan:
    def __init__(self, plan_type: str, title: str) -> None:
        self.type = plan_type
        self.title = title


def _char(character_id: UUID) -> Any:
    return cast(
        Any,
        type(
            "Char",
            (),
            {
                "id": character_id,
                "name": "小艾",
                "traits": {"personality": ["温柔", "勤奋"]},
                "backstory": "喜欢画画的学生",
            },
        ),
    )


class StubCharRepo:
    def __init__(self, chars: list[Any]) -> None:
        self.chars = chars

    async def get_active_characters(self) -> list[Any]:
        return self.chars


@pytest.fixture
def plan_repo() -> StubPlanRepo:
    return StubPlanRepo()


def _make_service(
    chars: list[Any],
    plan_repo: StubPlanRepo,
    llm: StubLLM,
    monkeypatch: MonkeyPatch,
) -> DailyPlanService:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(dps_module, "CharacterRepository", lambda session: StubCharRepo(chars))
    monkeypatch.setattr(dps_module, "PlanRepository", lambda session: plan_repo)

    return DailyPlanService(
        session_factory=_Session,
        llm=cast(LLMClient, llm),
        prompts=cast(PromptTemplates, StubPrompts()),
    )


class TestDailyPlanService:
    async def test_generates_plans_for_active_characters(
        self, monkeypatch: MonkeyPatch, plan_repo: StubPlanRepo
    ) -> None:
        cid = uuid4()
        service = _make_service([_char(cid)], plan_repo, StubLLM(), monkeypatch)

        created = await service.generate_for_all_characters(datetime(2026, 8, 27, 7, 0, tzinfo=UTC))

        assert created == 1
        assert len(plan_repo.created) == 1
        _cid, fields = plan_repo.created[0]
        assert _cid == cid
        assert fields["type"] == "daily"
        assert "咖啡店" in fields["title"]

    async def test_idempotent_within_same_world_day(self, monkeypatch: MonkeyPatch, plan_repo: StubPlanRepo) -> None:
        cid = uuid4()
        # 0022：幂等键为 plan_date 精确日期（非标题字符串），标题不再带日期前缀
        plan_repo.plans.append(
            {
                "character_id": cid,
                "type": "daily",
                "title": "已有计划",
                "plan_date": datetime(2026, 8, 27, tzinfo=UTC).date(),
            }
        )
        service = _make_service([_char(cid)], plan_repo, StubLLM(), monkeypatch)

        created = await service.generate_for_all_characters(datetime(2026, 8, 27, 7, 0, tzinfo=UTC))

        assert created == 0
        assert plan_repo.created == []

    async def test_generates_plan_with_plan_date(self, monkeypatch: MonkeyPatch, plan_repo: StubPlanRepo) -> None:
        cid = uuid4()
        service = _make_service([_char(cid)], plan_repo, StubLLM(), monkeypatch)

        await service.generate_for_all_characters(datetime(2026, 8, 27, 7, 0, tzinfo=UTC))

        _cid, fields = plan_repo.created[0]
        assert fields["plan_date"] == datetime(2026, 8, 27, tzinfo=UTC).date()
        # 标题不再拼日期前缀（plan_date 已承载幂等键）
        assert not fields["title"].startswith("[2026-08-27]")

    async def test_llm_failure_skips_character(self, monkeypatch: MonkeyPatch, plan_repo: StubPlanRepo) -> None:
        cid = uuid4()
        service = _make_service([_char(cid)], plan_repo, StubLLM(fail=True), monkeypatch)

        created = await service.generate_for_all_characters(datetime(2026, 8, 27, 7, 0, tzinfo=UTC))

        assert created == 0
        assert plan_repo.created == []
