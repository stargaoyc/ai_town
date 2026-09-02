"""每日计划生成器（round-7 F1b：计划被动涌现缺口主动化）

在每天清晨（世界时间）为活跃角色生成当日计划，写入 plans 表（type=daily），
替代此前「计划完全依赖 LLM 决策自发创建」的被动模式。

幂等：以「角色 + 世界日」为键，当日已存在 daily 计划则跳过（与 diary 幂等同哲学）。
LLM 不可用/失败时跳过本日，不阻塞调度循环。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from structlog import get_logger

from src.db.repositories import CharacterRepository, PlanRepository
from src.llm.prompts import PromptTemplates

logger = get_logger(__name__)

_DAILY_PLAN_MAX_PER_CHARACTER = 2


class DailyPlanService:
    """每日计划生成：按角色档案 + 世界时间生成当日待办"""

    def __init__(self, session_factory: Any, llm: Any, prompts: PromptTemplates):
        self.session_factory = session_factory
        self.llm = llm
        self.prompts = prompts

    async def generate_for_all_characters(self, world_now: datetime) -> int:
        """为所有活跃角色生成当日计划（幂等，按世界日）

        Args:
            world_now: 当前世界时间（用于幂等键与 prompt 时段描述）

        Returns:
            实际生成计划数
        """
        day_key = world_now.date()
        created = 0
        async with self.session_factory() as session:
            chars = await CharacterRepository(session).get_active_characters()
            plan_repo = PlanRepository(session)
            for char in chars:
                if await plan_repo.has_daily_plan_on(char.id, day_key):
                    continue
                created += await self._generate_for_character(plan_repo, char, world_now)
        if created:
            logger.info("daily_plans_generated", day=str(day_key), count=created)
        return created

    async def _generate_for_character(self, plan_repo: PlanRepository, char: Any, world_now: datetime) -> int:
        """为单个角色生成当日计划（LLM 结构化输出 → 落库）"""
        personality = ", ".join((char.traits or {}).get("personality", [])) or "无"
        prompt = self.prompts.render(
            "daily_plan",
            character_name=char.name,
            personality=personality,
            backstory=char.backstory or "无",
            world_time=world_now.isoformat(),
        )
        try:
            result = await self.llm.structured_output(
                prompt,
                schema={
                    "type": "object",
                    "properties": {
                        "plans": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "description": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["title"],
                            },
                        }
                    },
                },
            )
        except Exception as e:
            logger.warning("daily_plan_llm_failed", character_id=str(char.id), error=str(e))
            return 0

        created = 0
        plan_date = world_now.date()
        for item in result.get("plans", [])[:_DAILY_PLAN_MAX_PER_CHARACTER]:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            description = item.get("description")
            reason = item.get("reason")
            await plan_repo.create_plan(
                char.id,
                title=title.strip()[:180],
                description=description.strip()[:1000] if isinstance(description, str) else None,
                reason=reason.strip()[:2000] if isinstance(reason, str) and reason.strip() else None,
                type="daily",
                priority=3,
                plan_date=plan_date,
            )
            created += 1
        if created:
            logger.info(
                "daily_plan_created",
                character_id=str(char.id),
                count=created,
                day=str(plan_date),
            )
        return created
