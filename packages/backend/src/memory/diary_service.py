"""日记服务 - 基于记忆生成角色日记

从 memory_episodes 提取一段时间内的记忆，调用 LLM 生成叙事性日记。
日记不替代 Episode 真相源，是角色视角的叙事归档。
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from structlog import get_logger

from src.runtime import get_llm

logger = get_logger(__name__)


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

    PERIOD_DAYS = {
        "day": 1,
        "week": 7,
        "month": 30,
        "year": 365,
    }

    def __init__(self, session_factory, llm_client=None):
        """
        Args:
            session_factory: 异步会话工厂（async context manager），
                             如 db.session 或 db.session_factory
            llm_client: LLM 客户端实例（可选，默认从 runtime 获取）
        """
        self.session_factory = session_factory
        self._llm = llm_client

    def _get_target_time(self, target_date: datetime | None = None) -> datetime:
        """获取目标日期时间

        记忆使用真实 UTC 时间戳存储，因此这里使用真实时间查询。
        """
        return target_date or datetime.now(UTC)

    async def generate_diary(
        self,
        character_id: UUID,
        character_name: str,
        period: str = "day",
        target_date: datetime | None = None,
    ) -> dict | None:
        """为角色生成指定周期的日记

        Args:
            character_id: 角色 ID
            character_name: 角色名
            period: day/week/month/year
            target_date: 日记日期（默认为世界引擎当前时间）

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

        target = self._get_target_time(target_date)
        days = self.PERIOD_DAYS[period]
        start_date = target - timedelta(days=days)

        # 从数据库获取这段时间的记忆
        from src.db.repositories.memory_repo import MemoryRepository

        async with self.session_factory() as session:
            repo = MemoryRepository(session)
            memories = await repo.get_by_character_and_time_range(character_id, start_date, target)

        if not memories or len(memories) < 3:
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
        prompt = (
            f"你是角色「{character_name}」，请根据以下记忆记录，写一篇{period_cn}的日记。\n\n"
            f"记忆记录：\n{memory_summary}\n\n"
            f"要求：\n"
            f"1. 以第一人称写，体现角色的性格和情感\n"
            f"2. 不要罗列事实，而是叙事性地总结\n"
            f"3. 包含角色的感受和思考\n"
            f"4. 字数 200-500 字\n"
            f"5. 不要暴露你是 AI\n\n"
            f'请输出 JSON: {{"title": "日记标题", "content": "日记正文", "mood": "情绪"}}'
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
                "diary_date": target,  # datetime 对象，asyncpg 需要
                "diary_end_date": start_date if period != "day" else None,
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

    async def _save_diary(self, data: dict) -> None:
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
    ) -> list:
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
