"""角色对用户的记忆服务

管理角色对每个用户的独立记忆，每次用户交互后更新记忆。
更新为增量合并语义（由 person_memory.yaml 约束 LLM 保留旧事实），
并落库结构化 preferences；热度由后台任务周期衰减。
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from structlog import get_logger

from src.db.models import PersonMemory, PersonMemoryEntry
from src.runtime import get_llm

logger = get_logger(__name__)


class PersonMemoryService:
    """管理角色对每个用户的独立记忆

    每次用户交互后更新记忆，包含：
    - 用户偏好（preferences JSONB，结构化）
    - 关系进展与共同话题（content 自然语言）
    - 热度机制：交互越频繁热度越高，后台任务周期衰减
    """

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

    async def get_memory(self, character_id: UUID, user_id: str) -> dict[str, Any] | None:
        """获取角色对某用户的记忆"""
        async with self.session_factory() as session:
            stmt = select(PersonMemory).where(
                PersonMemory.character_id == character_id,
                PersonMemory.user_id == user_id,
            )
            row = await session.execute(stmt)
            memory = row.scalars().first()
            if memory is None:
                return None
            return {
                "id": str(memory.id),
                "character_id": str(memory.character_id),
                "user_id": memory.user_id,
                "platform": memory.platform,
                "content": memory.content,
                "summary": memory.summary,
                "heat": memory.heat,
                "preferences": memory.preferences,
                "last_interaction_at": memory.last_interaction_at.isoformat() if memory.last_interaction_at else None,
            }

    async def update_memory(
        self,
        character_id: UUID,
        character_name: str,
        user_id: str,
        platform: str,
        user_message: str,
        character_reply: str,
    ) -> dict[str, Any] | None:
        """交互后更新角色对用户的记忆（两层结构·抽取式）

        LLM 按 person_memory.yaml 从对话中抽取**新事实**（不做全文重写）：
        {"facts": [...], "preferences": {...}}；
        事实逐条追加到 person_memory_entries（append-only），
        主档 content 由后台压缩任务定期合并生成。
        解析失败时回退将用户消息截断作为单条事实，不丢交互痕迹。

        Returns:
            {"appended": 追加条数}，或 None（LLM 不可用/失败）
        """
        llm = self._llm or get_llm()
        if not llm:
            return None

        existing = await self.get_memory(character_id, user_id)
        existing_content = existing.get("content", "") if existing else "（初次交流）"

        from src.runtime import get_prompts

        prompts = self._prompts or get_prompts()
        if not prompts:
            logger.warning("person_memory_prompts_unavailable", character_id=str(character_id))
            return None
        prompt = prompts.render(
            "person_memory",
            character_name=character_name,
            user_id=user_id,
            existing_content=existing_content,
            user_message=user_message,
            character_reply=character_reply,
        )

        try:
            response = await llm.chat(prompt, model="chat")
            facts, preferences = self._parse_memory_response(response)

            if not facts:
                # 抽取为空也要留下交互痕迹：回退单条事实（截断防膨胀）
                facts = [f"用户提到：{user_message[:120]}"]

            await self._append_entries(character_id, user_id, platform, facts)
            await self._upsert_memory(character_id, user_id, platform, preferences)
            logger.info(
                "person_memory_updated",
                character_id=str(character_id),
                user_id=user_id,
                appended=len(facts),
                has_preferences=bool(preferences),
            )
            return {"appended": len(facts)}

        except Exception as e:
            logger.error(
                "person_memory_update_failed",
                error=str(e),
                exc_info=True,
            )
            return None

    @staticmethod
    def _parse_memory_response(raw: str) -> tuple[list[str], dict[str, Any]]:
        """解析 LLM 抽取输出

        优先解析 JSON {"facts": [...], "preferences": {...}}；失败回退整段文本作为单条事实。
        """
        text = raw.strip()
        if text.startswith("```"):
            lines = [ln for ln in text.split("\n") if not ln.startswith("```")]
            text = "\n".join(lines).strip()

        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            parsed = json.loads(text[start:end])
            if isinstance(parsed, dict):
                facts_raw = parsed.get("facts")
                facts = (
                    [f.strip() for f in facts_raw if isinstance(f, str) and f.strip()]
                    if isinstance(facts_raw, list)
                    else []
                )
                preferences = parsed.get("preferences")
                return facts, preferences if isinstance(preferences, dict) else {}
        except (ValueError, json.JSONDecodeError):
            pass
        # 解析失败：返回空事实，由调用方回退为「用户提到：<消息>」，不丢交互痕迹
        return [], {}

    async def _append_entries(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        facts: list[str],
    ) -> None:
        """追加事实条目（append-only，只写不改）"""
        async with self.session_factory() as session:
            for fact in facts:
                session.add(
                    PersonMemoryEntry(
                        character_id=character_id,
                        user_id=user_id,
                        platform=platform,
                        content=fact,
                    )
                )
            await session.commit()

    async def _upsert_memory(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        preferences: dict[str, Any] | None = None,
    ) -> None:
        """确保主档行存在并累积热度（content 由压缩任务维护，此处不写）"""
        async with self.session_factory() as session:
            stmt = (
                update(PersonMemory)
                .where(
                    PersonMemory.character_id == character_id,
                    PersonMemory.user_id == user_id,
                )
                .values(
                    heat=PersonMemory.heat + 1,
                    last_interaction_at=func.now(),
                    updated_at=func.now(),
                    **({"preferences": preferences} if preferences else {}),
                )
            )
            result = await session.execute(stmt)
            if int(result.rowcount or 0) == 0:
                session.add(
                    PersonMemory(
                        character_id=character_id,
                        user_id=user_id,
                        platform=platform,
                        content="",
                        heat=1,
                        preferences=preferences,
                    )
                )
            await session.commit()

    async def get_relevant_context(self, character_id: UUID, user_id: str) -> str:
        """获取角色对用户的记忆上下文，注入对话 system prompt（两层组装）

        = 主档 content（后台压缩合并的稳定认知）
          + 最近未压缩事实条目（最多 8 条，时间倒序——最新交互的细节）
        """
        memory = await self.get_memory(character_id, user_id)
        profile = str(memory.get("content") or "").strip() if memory else ""

        async with self.session_factory() as session:
            stmt = (
                select(PersonMemoryEntry.content)
                .where(
                    PersonMemoryEntry.character_id == character_id,
                    PersonMemoryEntry.user_id == user_id,
                    PersonMemoryEntry.compacted.is_(False),
                )
                .order_by(PersonMemoryEntry.created_at.desc())
                .limit(8)
            )
            recent = [row[0] for row in (await session.execute(stmt)).all()]

        parts = []
        if profile:
            parts.append(profile)
        if recent:
            parts.append("最近了解到的：\n" + "\n".join(f"- {fact}" for fact in recent))
        return "\n".join(parts) if parts else "（初次与该用户交流）"
