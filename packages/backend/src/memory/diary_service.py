"""日记服务 - 基于记忆生成角色日记

从 memory_episodes 提取一段时间内的记忆，调用 LLM 生成叙事性日记。
日记不替代 Episode 真相源，是角色视角的叙事归档。
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.config import settings
from src.runtime import get_llm

logger = get_logger(__name__)


def _world_real_window_seconds(period: str) -> float:
    """周期（世界天数）换算为真实秒数

    世界时钟每 world_tick_seconds 真实秒推进 world_tick_minutes 虚拟分钟，
    故 1 个世界日耗时 1440 × world_tick_seconds / world_tick_minutes 真实秒
    （默认配置下 = 72 真实分钟）。记忆按真实时间戳存储，查询窗口必须用真实秒。
    """
    world_days = DiaryService.PERIOD_DAYS[period]
    return world_days * 1440 * settings.world_tick_seconds / settings.world_tick_minutes


def _diary_trigger_periods(world_now: datetime) -> list[str]:
    """日记触发矩阵（纯函数）：按世界时间判断本轮需生成的日记种类

    - 日：世界时间 22:00-次日 06:00（一天结束时）
    - 周/月/年：tm_yday 整除 7/30/365 的世界日
    """
    periods: list[str] = []
    if world_now.hour >= 22 or world_now.hour < 6:
        periods.append("day")
    day_of_year = world_now.timetuple().tm_yday
    if day_of_year % 7 == 0:
        periods.append("week")
    if day_of_year % 30 == 0:
        periods.append("month")
    if day_of_year % 365 == 0:
        periods.append("year")
    return periods


def _derive_diary_dates(period: str, world_now: datetime) -> tuple[datetime, datetime | None]:
    """由世界时间派生日记的归属日期与覆盖终点（纯函数）

    diary_date 存虚拟时间：展示与排序语义应跟随世界时钟（世界时钟单调，get_latest 天然有序）；
    幂等键 diary_date::date 也由此派生，保证调度链路一天一报。
    """
    end = world_now - timedelta(days=DiaryService.PERIOD_DAYS[period]) if period != "day" else None
    return world_now, end


class DiaryService:
    """日记生成服务

    从 memory_episodes 提取一段时间内的记忆，调用 LLM 生成叙事性日记。
    日记不替代 Episode 真相源，是角色视角的叙事归档。
    支持四种周期：
    - day: 日报（每日生成）
    - week: 周报（每周生成）
    - month: 月报（每月生成）
    - year: 年报（每年生成）
    """

    # 各周期的「世界天数」（非真实天数）：真实查询窗口由 _world_real_window_seconds
    # 按世界时钟倍率换算，避免把 ~20 个世界日误当成一个真实日（round-3 review H1）
    PERIOD_DAYS = {
        "day": 1,
        "week": 7,
        "month": 30,
        "year": 365,
    }

    def __init__(self, session_factory: Any, llm_client: Any = None, prompts: Any = None):
        """
        Args:
            session_factory: 异步会话工厂（async context manager），
                             如 db.session 或 db.session_factory
            llm_client: LLM 客户端实例（可选，默认从 runtime 获取）
            prompts: Prompt 模板管理器（可选，默认从 runtime 获取）
        """
        self.session_factory = session_factory
        self._llm = llm_client
        self._prompts = prompts

    async def generate_diary(
        self,
        character_id: UUID,
        character_name: str,
        period: str = "day",
        *,
        world_now: datetime | None = None,
        window_start: datetime | None = None,
    ) -> dict[str, Any] | None:
        """为角色生成指定周期的日记

        Args:
            character_id: 角色 ID
            character_name: 角色名
            period: day/week/month/year
            world_now: 世界引擎当前虚拟时间——diary_date 与幂等日期的唯一真相源（H1）。
                       仅手动触发入口（API 未接世界时钟）允许缺省，以真实时间近似
            window_start: 记忆查询窗口起点（真实时间）；缺省时按世界时钟倍率从周期换算

        Returns:
            生成的日记数据，或 None（无记忆/LLM 不可用）
        """
        if period not in self.PERIOD_DAYS:
            logger.warning("diary_invalid_period", period=period)
            return None

        llm = self._llm or get_llm()
        if not llm:
            logger.warning("diary_llm_unavailable", character_id=str(character_id))
            return None

        real_now = datetime.now(UTC)
        effective_world_now = world_now or real_now
        effective_window_start = window_start or (real_now - timedelta(seconds=_world_real_window_seconds(period)))
        _, diary_end_date = _derive_diary_dates(period, effective_world_now)

        # 从数据库获取这段时间的记忆（记忆为真实时间戳，窗口用真实时间边界）
        from src.db.repositories.memory_repo import MemoryRepository

        async with self.session_factory() as session:
            repo = MemoryRepository(session)
            memories = await repo.get_by_character_and_time_range(character_id, effective_window_start, real_now)

        if not memories or len(memories) < 1:
            logger.info(
                "diary_insufficient_memories",
                character_id=str(character_id),
                count=len(memories) if memories else 0,
            )
            return None

        # 构造记忆摘要（最多取 20 条，避免 prompt 过长）
        memory_texts = []
        for m in memories[-20:]:
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            memory_texts.append(f"- {content}")

        memory_summary = "\n".join(memory_texts)

        # 构造 Prompt
        period_cn = {"day": "今天", "week": "这一周", "month": "这个月", "year": "这一年"}[period]
        from src.runtime import get_prompts

        prompts = self._prompts or get_prompts()
        if not prompts:
            logger.warning("diary_prompts_unavailable", character_id=str(character_id))
            return None
        prompt = prompts.render(
            "diary",
            character_name=character_name,
            period_cn=period_cn,
            memory_summary=memory_summary,
        )

        try:
            result = await llm.structured_output(
                prompt,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "mood": {"type": "string"},
                    },
                    "required": ["title", "content", "mood"],
                },
                model="chat",
            )

            diary_data = {
                "character_id": str(character_id),
                "period": period,
                "diary_date": effective_world_now,  # 虚拟时间；datetime 对象，asyncpg 需要
                "diary_end_date": diary_end_date,
                "title": result.get("title", f"{period_cn}的日记"),
                "content": result.get("content", ""),
                "mood": result.get("mood", ""),
            }

            # 保存到数据库
            await self._save_diary(diary_data)
            logger.info(
                "diary_generated",
                character_id=str(character_id),
                period=period,
                title=diary_data["title"],
            )
            return diary_data

        except Exception as e:
            logger.error(
                "diary_generation_failed",
                character_id=str(character_id),
                error=str(e),
                exc_info=True,
            )
            return None

    async def generate_diaries_for_all_characters(
        self,
        period: str,
        *,
        world_now: datetime,
        window_start: datetime,
    ) -> dict[str, Any]:
        """为所有活跃角色批量生成指定周期的日记

        对每个角色先检查当前「世界日」该周期日记是否已存在，已存在则跳过（幂等）。
        单个角色失败不影响其余角色，最终返回汇总计数。

        Args:
            period: day/week/month/year
            world_now: 世界引擎当前虚拟时间（幂等日期与 diary_date 的真相源）
            window_start: 记忆查询窗口起点（真实时间）

        Returns:
            汇总字典：period / total / success / skipped / failed
        """
        if period not in self.PERIOD_DAYS:
            logger.warning("diary_batch_invalid_period", period=period)
            return {"period": period, "total": 0, "success": 0, "skipped": 0, "failed": 0}

        from sqlalchemy import text

        from src.db.repositories import CharacterRepository

        async with self.session_factory() as session:
            repo = CharacterRepository(session)
            characters = await repo.get_active_characters()

        success = 0
        skipped = 0
        failed = 0

        for char in characters:
            try:
                # 幂等检查：当前「世界日」该周期日记已存在则跳过
                async with self.session_factory() as session:
                    exists = await session.execute(
                        text("""
                            SELECT 1 FROM character_diaries
                            WHERE character_id = :cid AND period = :period
                              AND diary_date::date = (:world_date)::date
                            LIMIT 1
                        """),
                        {
                            "cid": str(char.id),
                            "period": period,
                            "world_date": world_now,
                        },
                    )
                    if exists.fetchone() is not None:
                        skipped += 1
                        logger.debug(
                            "diary_batch_character_skipped",
                            character_id=str(char.id),
                            character_name=char.name,
                            period=period,
                        )
                        continue

                diary = await self.generate_diary(
                    character_id=char.id,
                    character_name=char.name,
                    period=period,
                )
                if diary is not None:
                    success += 1
                    logger.info(
                        "diary_batch_character_success",
                        character_id=str(char.id),
                        character_name=char.name,
                        period=period,
                    )
                else:
                    failed += 1
                    logger.warning(
                        "diary_batch_character_failed",
                        character_id=str(char.id),
                        character_name=char.name,
                        period=period,
                    )
            except Exception as e:
                failed += 1
                logger.error(
                    "diary_batch_character_error",
                    character_id=str(char.id),
                    character_name=char.name,
                    period=period,
                    error=str(e),
                    exc_info=True,
                )

        logger.info(
            "diary_batch_complete",
            period=period,
            total=len(characters),
            success=success,
            skipped=skipped,
            failed=failed,
        )
        return {
            "period": period,
            "total": len(characters),
            "success": success,
            "skipped": skipped,
            "failed": failed,
        }

    async def _save_diary(self, data: dict[str, Any]) -> None:
        """保存日记到数据库"""
        from sqlalchemy import text

        async with self.session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO character_diaries
                        (character_id, period, diary_date, diary_end_date, title, content, mood)
                    VALUES
                        (:character_id, :period, :diary_date, :diary_end_date, :title, :content, :mood)
                """),
                {
                    "character_id": data["character_id"],
                    "period": data["period"],
                    "diary_date": data["diary_date"],
                    "diary_end_date": data.get("diary_end_date"),
                    "title": data["title"],
                    "content": data["content"],
                    "mood": data.get("mood", ""),
                },
            )
            await session.commit()

    async def get_diaries(
        self,
        character_id: UUID,
        period: str | None = None,
        limit: int = 20,
    ) -> list[Any]:
        """获取角色的日记列表

        Args:
            character_id: 角色 ID
            period: 周期过滤（可选，day/week/month/year）
            limit: 返回数量上限

        Returns:
            日记记录列表（按日期倒序）
        """
        from sqlalchemy import text

        async with self.session_factory() as session:
            if period:
                result = await session.execute(
                    text("""
                        SELECT * FROM character_diaries
                        WHERE character_id = :cid AND period = :period
                        ORDER BY diary_date DESC LIMIT :limit
                    """),
                    {"cid": str(character_id), "period": period, "limit": limit},
                )
            else:
                result = await session.execute(
                    text("""
                        SELECT * FROM character_diaries
                        WHERE character_id = :cid
                        ORDER BY diary_date DESC LIMIT :limit
                    """),
                    {"cid": str(character_id), "limit": limit},
                )
            # SQLAlchemy 2.0 Row 需通过 ._mapping 转字典
            rows = [dict(row._mapping) for row in result]

        # 序列化 datetime/UUID 为字符串
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif isinstance(v, UUID):
                    r[k] = str(v)
        return rows
