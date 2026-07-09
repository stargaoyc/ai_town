"""消息服务 - 处理用户与角色的对话

职责：
1. 接收用户消息，写入 messages 表
2. 构造 LLM 上下文（角色档案 + 对话历史 + 检索记忆）
3. 调用 LLM 生成回复，写入 messages 表
4. 记录 token / cost 供成本控制
5. 维护 conversation.context 摘要（超过阈值时压缩）
6. 可选：将用户消息与角色回复沉淀为 memory_episodes（source_type=conversation）

设计要点：
- 上下文窗口管理：保留最近 N 条消息（默认 20），超出走 LLM 摘要压缩
- 失败容错：LLM 调用失败时返回默认错误消息，不影响用户会话状态
- 事务边界：用户消息与角色回复在同一事务内提交，保证一致性
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from structlog import get_logger

from src.db.models import Character, Conversation, Message
from src.db.repositories import (
    CharacterRepository,
    ConversationRepository,
    MemoryRepository,
    MessageRepository,
)
from src.llm import LLMClient, PromptTemplates

logger = get_logger(__name__)


# 上下文管理常量
DEFAULT_HISTORY_LIMIT = 20        # 默认拉取最近 20 条消息构造 history
CONTEXT_COMPRESS_THRESHOLD = 50   # 会话累计消息超过 50 条时触发压缩
COMPRESSED_HISTORY_LIMIT = 10     # 压缩后保留最近 10 条原文

# 默认错误回复（LLM 失败时返回，避免用户会话阻塞）
DEFAULT_ERROR_REPLY = "（角色陷入了沉思，未能给出回复，请稍后再试）"


class MessageService:
    """消息服务 - 用户与角色对话的核心业务层

    使用方式：
        async with db.session() as session:
            svc = MessageService(
                session=session,
                llm=llm,
                prompts=prompts,
            )
            response = await svc.handle_user_message(
                character_id=cid,
                user_id="user_123",
                platform="web",
                content="你好",
            )
    """

    def __init__(
        self,
        session,
        llm: LLMClient,
        prompts: PromptTemplates,
    ):
        """
        Args:
            session: 异步数据库会话
            llm: LLM 客户端
            prompts: Prompt 模板管理器
        """
        self.session = session
        self.llm = llm
        self.prompts = prompts

        # Repository 实例（与 session 绑定）
        self.conversation_repo = ConversationRepository(session)
        self.message_repo = MessageRepository(session)
        self.character_repo = CharacterRepository(session)
        self.memory_repo = MemoryRepository(session)

    async def handle_user_message(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        content: str,
    ) -> dict:
        """处理用户消息的完整流程

        流程：
        1. 获取/创建会话
        2. 写入用户消息
        3. 加载角色档案 + 对话历史
        4. 检索相关记忆（可选，按需启用）
        5. 调用 LLM 生成回复
        6. 写入角色回复
        7. 更新会话 last_message_at 与 context
        8. 返回回复内容与元数据

        Args:
            character_id: 角色 ID
            user_id: 用户标识
            platform: 来源平台（web/qq/lark/internal）
            content: 用户消息内容

        Returns:
            {
                "conversation_id": UUID,
                "message_id": UUID,        # 角色回复消息 ID
                "content": str,             # 回复内容
                "tokens": int,              # 本轮 token 消耗
                "cost": float,              # 本轮费用 USD
                "error": str | None,        # 错误信息（成功为 None）
            }
        """
        # 1. 获取/创建会话
        conversation = await self.conversation_repo.get_or_create(
            character_id=character_id,
            user_id=user_id,
            platform=platform,
        )

        # 2. 写入用户消息
        await self.message_repo.add(
            conversation_id=conversation.id,
            sender="user",
            content=content,
        )

        # 3. 加载角色档案
        character_data = await self.character_repo.get_character_with_state(character_id)
        if character_data is None:
            logger.warning(
                "character_not_found_for_conversation",
                character_id=str(character_id),
                conversation_id=str(conversation.id),
            )
            # 写入系统消息提示用户
            await self.message_repo.add(
                conversation_id=conversation.id,
                sender="system",
                content=f"角色 {character_id} 不存在或已下线",
            )
            await self.session.commit()
            return {
                "conversation_id": conversation.id,
                "message_id": None,
                "content": DEFAULT_ERROR_REPLY,
                "tokens": 0,
                "cost": 0.0,
                "error": "character_not_found",
            }

        character, state = character_data

        # 4. 构造 LLM 上下文
        history = await self.message_repo.list_recent(
            conversation_id=conversation.id,
            limit=DEFAULT_HISTORY_LIMIT,
        )
        # 排除刚写入的用户消息（避免在 history 中重复）
        # list_recent 返回最近 N 条含刚写入的，需确保末尾为用户消息
        context_text = await self._build_context(
            conversation=conversation,
            character=character,
            state=state,
            history=history,
        )

        # 5. 调用 LLM 生成回复
        reply_text, tokens, cost, error = await self._generate_reply(
            character=character,
            context=context_text,
            history=history,
            user_message=content,
        )

        # 6. 写入角色回复
        reply_msg = await self.message_repo.add(
            conversation_id=conversation.id,
            sender="character",
            content=reply_text,
            tokens=tokens,
            cost=cost,
            extra_data={"error": error} if error else None,
        )

        # 7. 更新会话（轻量更新 last_message_at，必要时压缩 context）
        await self._maybe_compress_context(conversation, character)

        await self.session.commit()

        logger.info(
            "message_handled",
            conversation_id=str(conversation.id),
            character_id=str(character_id),
            user_id=user_id,
            reply_length=len(reply_text),
            tokens=tokens,
            cost=cost,
            error=error,
        )

        return {
            "conversation_id": conversation.id,
            "message_id": reply_msg.id,
            "content": reply_text,
            "tokens": tokens,
            "cost": cost,
            "error": error,
        }

    async def _build_context(
        self,
        conversation: Conversation,
        character: Character,
        state,
        history: list[Message],
    ) -> str:
        """构造 LLM 上下文文本

        包含：
        - 角色档案（姓名/性格/背景）
        - 当前状态（位置/精力/情绪）
        - 对话历史摘要（若 conversation.context 存在）
        - 当前情绪状态

        Args:
            conversation: 会话对象
            character: 角色档案
            state: 角色实时状态
            history: 最近消息列表

        Returns:
            渲染后的上下文文本
        """
        personality = (character.traits or {}).get("personality", [])
        if isinstance(personality, list):
            personality_text = "、".join(personality)
        else:
            personality_text = str(personality)

        # 优先使用已压缩的 context 摘要，否则使用空字符串
        context_summary = ""
        if conversation.context:
            context_summary = conversation.context.get("summary", "")

        return (
            f"[角色档案]\n"
            f"姓名: {character.name}\n"
            f"性格: {personality_text}\n"
            f"背景: {character.backstory or '（无）'}\n\n"
            f"[当前状态]\n"
            f"位置: {state.location or '未知'}\n"
            f"精力: {state.stamina}/100\n"
            f"情绪: {state.mood or 'calm'}\n\n"
            f"[对话摘要]\n"
            f"{context_summary or '（新对话，暂无摘要）'}\n"
        )

    async def _generate_reply(
        self,
        character: Character,
        context: str,
        history: list[Message],
        user_message: str,
    ) -> tuple[str, int, float, str | None]:
        """调用 LLM 生成角色回复

        Args:
            character: 角色档案
            context: 已构造的上下文文本
            history: 对话历史
            user_message: 用户消息

        Returns:
            (reply_text, tokens, cost, error)
            - error 非 None 时 reply_text 为默认错误回复
        """
        # 构造历史文本（最近 N 条）
        history_text = "\n".join([
            f"{'用户' if m.sender == 'user' else character.name}: {m.content}"
            for m in history
            if m.sender in ("user", "character")
        ])

        try:
            # 使用 chat 模型生成回复（避免 structured_output 的格式约束）
            prompt = (
                f"{context}\n"
                f"[对话历史]\n{history_text}\n\n"
                f"[用户消息]\n{user_message}\n\n"
                f"请以 {character.name} 的身份自然回复用户消息，保持角色性格一致。"
                f"回复要简洁有趣，避免暴露你是 AI 模型。"
            )

            response = await self.llm.chat(prompt, model="chat")

            # ⚠️ Phase 3.5 将接入 Langfuse 精确统计 token/cost
            # 当前使用粗略估算（中文约 1.5 字/token，英文约 4 字符/token）
            estimated_tokens = max(
                len(prompt) // 3,
                len(response) // 3,
            )
            estimated_cost = estimated_tokens * 0.000001  # 假设 $1/M tokens

            return response, estimated_tokens, estimated_cost, None

        except Exception as e:
            logger.error(
                "llm_reply_failed",
                character_id=str(character.id),
                error=str(e),
                exc_info=True,
            )
            return DEFAULT_ERROR_REPLY, 0, 0.0, str(e)

    async def _maybe_compress_context(
        self,
        conversation: Conversation,
        character: Character,
    ) -> None:
        """按需压缩会话上下文

        当会话累计消息超过 CONTEXT_COMPRESS_THRESHOLD 时，调用 LLM 将早期
        历史压缩为摘要，存入 conversation.context.summary。
        保留最近 COMPRESSED_HISTORY_LIMIT 条原文不压缩。

        Args:
            conversation: 会话对象
            character: 角色档案（用于 prompt 渲染）
        """
        # 统计当前会话消息数
        recent_msgs = await self.message_repo.list_by_conversation(
            conversation_id=conversation.id,
            limit=1,
            order_desc=True,
        )
        # 仅在有消息时执行（避免空会话触发压缩）
        if not recent_msgs:
            return

        # 拉取稍多的窗口判断是否触发压缩
        all_recent = await self.message_repo.list_by_conversation(
            conversation_id=conversation.id,
            limit=CONTEXT_COMPRESS_THRESHOLD + 1,
            order_desc=True,
        )
        if len(all_recent) <= CONTEXT_COMPRESS_THRESHOLD:
            # 未达阈值，仅更新 last_message_at
            await self.conversation_repo.touch_last_message(conversation.id)
            return

        # 已达阈值，执行压缩
        # 取最近 COMPRESSED_HISTORY_LIMIT 条之前的消息作为压缩输入
        to_compress = all_recent[COMPRESSED_HISTORY_LIMIT:]
        if not to_compress:
            await self.conversation_repo.touch_last_message(conversation.id)
            return

        # 构造压缩输入文本
        history_text = "\n".join([
            f"{'用户' if m.sender == 'user' else character.name}: {m.content}"
            for m in reversed(to_compress)  # 时间正序
            if m.sender in ("user", "character")
        ])

        try:
            compress_prompt = (
                f"请将以下 {character.name} 与用户的对话历史压缩为一段简洁的摘要（200字以内），"
                f"保留关键事件、角色情绪变化与用户偏好：\n\n{history_text}"
            )
            summary = await self.llm.chat(compress_prompt, model="flash")

            # 写入压缩后的 context
            existing_context = conversation.context or {}
            existing_context["summary"] = summary
            existing_context["compressed_at"] = datetime.now(timezone.utc).isoformat()
            existing_context["compressed_count"] = len(to_compress)

            await self.conversation_repo.update_context(
                conversation_id=conversation.id,
                context=existing_context,
            )

            logger.info(
                "context_compressed",
                conversation_id=str(conversation.id),
                compressed_count=len(to_compress),
                summary_length=len(summary),
            )
        except Exception as e:
            # 压缩失败不影响主流程，仅记录
            logger.warning(
                "context_compress_failed",
                conversation_id=str(conversation.id),
                error=str(e),
            )
            await self.conversation_repo.touch_last_message(conversation.id)
