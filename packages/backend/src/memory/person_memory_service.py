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

from src.db.models import PersonMemory
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
        """交互后更新角色对用户的记忆

        LLM 按 person_memory.yaml 输出增量合并后的 JSON：
        {"content": "...", "preferences": {...}}；
        解析失败时回退将原文作为 content、不更新 preferences。

        Returns:
            更新后的记忆数据，或 None（LLM 不可用/失败）
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
            new_content, preferences = self._parse_memory_response(response)

            await self._upsert_memory(character_id, user_id, platform, new_content, preferences)
            logger.info(
                "person_memory_updated",
                character_id=str(character_id),
                user_id=user_id,
                has_preferences=bool(preferences),
            )
            return {"content": new_content}

        except Exception as e:
            logger.error(
                "person_memory_update_failed",
                error=str(e),
                exc_info=True,
            )
            return None

    @staticmethod
    def _parse_memory_response(raw: str) -> tuple[str, dict[str, Any]]:
        """解析 LLM 记忆更新输出

        优先解析 JSON {"content", "preferences"}；失败回退纯文本作为 content。
        """
        text = raw.strip()
        if text.startswith("```"):
            lines = [ln for ln in text.split("\n") if not ln.startswith("```")]
            text = "\n".join(lines).strip()

        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            parsed = json.loads(text[start:end])
            if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
                preferences = parsed.get("preferences")
                return (
                    parsed["content"].strip(),
                    preferences if isinstance(preferences, dict) else {},
                )
        except (ValueError, json.JSONDecodeError):
            pass
        return text, {}

    async def _upsert_memory(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        content: str,
        preferences: dict[str, Any] | None = None,
    ) -> None:
        """插入或更新记忆（热度 +1，刷新交互时间）"""
        async with self.session_factory() as session:
            stmt = (
                update(PersonMemory)
                .where(
                    PersonMemory.character_id == character_id,
                    PersonMemory.user_id == user_id,
                )
                .values(
                    content=content,
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
                        content=content,
                        heat=1,
                        preferences=preferences,
                    )
                )
            await session.commit()

    async def get_relevant_context(self, character_id: UUID, user_id: str) -> str:
        """获取角色对用户的记忆上下文（用于注入对话 system prompt）"""
        memory = await self.get_memory(character_id, user_id)
        if not memory:
            return "（初次与该用户交流）"
        content = str(memory.get("content") or "").strip()
        return content if content else "（无记忆）"
