"""角色对用户的记忆服务

管理角色对每个用户的独立记忆，每次用户交互后更新记忆。
"""
from uuid import UUID

from structlog import get_logger

from src.runtime import get_llm

logger = get_logger(__name__)


class PersonMemoryService:
    """管理角色对每个用户的独立记忆

    每次用户交互后更新记忆，包含：
    - 用户偏好（喜欢的话题、说话风格）
    - 关系进展（亲密度变化、重要事件）
    - 共同话题（可以聊的内容）

    记忆有热度机制：交互越频繁热度越高，长时间不交互热度衰减。
    """

    def __init__(self, session_factory, llm_client=None):
        """
        Args:
            session_factory: 异步会话工厂（async context manager），
                             如 db.session 或 db.session_factory
            llm_client: LLM 客户端实例（可选，默认从 runtime 获取）
        """
        self.session_factory = session_factory
        self._llm = llm_client

    async def get_memory(self, character_id: UUID, user_id: str) -> dict | None:
        """获取角色对某用户的记忆

        Args:
            character_id: 角色 ID
            user_id: 用户标识

        Returns:
            记忆记录字典，或 None（无记忆）
        """
        from sqlalchemy import text
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT * FROM person_memories
                    WHERE character_id = :cid AND user_id = :uid
                """),
                {"cid": str(character_id), "uid": user_id}
            )
            row = result.fetchone()
            return dict(row) if row else None

    async def update_memory(
        self,
        character_id: UUID,
        character_name: str,
        user_id: str,
        platform: str,
        user_message: str,
        character_reply: str,
    ) -> dict | None:
        """交互后更新角色对用户的记忆

        Args:
            character_id: 角色 ID
            character_name: 角色名
            user_id: 用户标识
            platform: 平台
            user_message: 用户消息
            character_reply: 角色回复

        Returns:
            更新后的记忆数据，或 None（LLM 不可用/失败）
        """
        llm = self._llm or get_llm()
        if not llm:
            return None

        # 获取现有记忆
        existing = await self.get_memory(character_id, user_id)
        existing_content = existing.get("content", "") if existing else "（初次交流）"

        # 构造 Prompt 让 LLM 更新记忆
        prompt = (
            f"你是角色「{character_name}」的记忆系统。请根据以下对话更新你对用户 {user_id} 的记忆。\n\n"
            f"现有记忆：\n{existing_content}\n\n"
            f"最新对话：\n用户: {user_message}\n{character_name}: {character_reply}\n\n"
            f"请更新你对这个用户的认知，包含：\n"
            f"1. 用户的偏好和兴趣\n"
            f"2. 关系进展\n"
            f"3. 值得记住的细节\n\n"
            f"请输出更新后的记忆内容（自然语言，200 字以内）："
        )

        try:
            response = await llm.chat(prompt, model="chat")
            new_content = response if isinstance(response, str) else str(response)

            # 保存或更新
            await self._upsert_memory(character_id, user_id, platform, new_content)
            logger.info(
                "person_memory_updated",
                character_id=str(character_id),
                user_id=user_id,
            )
            return {"content": new_content}

        except Exception as e:
            logger.error(
                "person_memory_update_failed",
                error=str(e),
                exc_info=True,
            )
            return None

    async def _upsert_memory(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        content: str,
    ) -> None:
        """插入或更新记忆

        - 存在则更新内容、热度+1、刷新交互时间
        - 不存在则插入新记录，热度初始化为 1
        """
        from sqlalchemy import text
        async with self.session_factory() as session:
            # 检查是否存在
            result = await session.execute(
                text(
                    "SELECT id, heat FROM person_memories "
                    "WHERE character_id = :cid AND user_id = :uid"
                ),
                {"cid": str(character_id), "uid": user_id}
            )
            row = result.fetchone()

            if row:
                # 更新：热度 +1
                await session.execute(
                    text("""
                        UPDATE person_memories
                        SET content = :content, heat = heat + 1,
                            last_interaction_at = NOW(), updated_at = NOW()
                        WHERE character_id = :cid AND user_id = :uid
                    """),
                    {"content": content, "cid": str(character_id), "uid": user_id}
                )
            else:
                # 插入
                await session.execute(
                    text("""
                        INSERT INTO person_memories
                            (character_id, user_id, platform, content, heat)
                        VALUES
                            (:cid, :uid, :platform, :content, 1)
                    """),
                    {
                        "cid": str(character_id),
                        "uid": user_id,
                        "platform": platform,
                        "content": content,
                    }
                )
            await session.commit()

    async def get_relevant_context(self, character_id: UUID, user_id: str) -> str:
        """获取角色对用户的记忆上下文（用于注入 LLM prompt）

        Args:
            character_id: 角色 ID
            user_id: 用户标识

        Returns:
            记忆内容文本，或默认提示（无记忆时）
        """
        memory = await self.get_memory(character_id, user_id)
        if not memory:
            return "（初次与该用户交流）"
        return memory.get("content", "（无记忆）")
