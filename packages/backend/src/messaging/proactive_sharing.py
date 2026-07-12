"""主动分享链路 - 角色主动向用户推送消息

设计目标（roadmap 6.2）：
- 分享意图评估：角色在 Tick 中产生"想分享"的意图时，LLM 评估是否合适
- 分享文案生成：以角色性格生成自然语言，不暴露工程概念
- 发送调度：通过 WebSocketManager 推送给相关用户，避免刷屏

触发场景：
1. 角色完成重要 Action（如获得新物品、达成里程碑）
2. 角色情绪强烈变化（兴奋/沮丧）
3. 角色与他人发生有趣互动
4. 定时日常分享（早安/晚安/吃饭）

调用方式：
    由 CharacterTickEngine 在 Action 执行完成后调用：
    await sharing_service.evaluate_and_share(character_id, action_record, state)

    或由 WorldEngine 在特定事件触发：
    await sharing_service.send_routine_share(character_id, "morning_greeting")
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.config import settings
from src.db.models import ActionRecord, Character, CharacterState
from sqlalchemy import select, desc, func
from src.db.repositories import (
    CharacterRepository,
    ConversationRepository,
    MessageRepository,
)
from src.llm import LLMClient, PromptTemplates

logger = get_logger(__name__)


# 触发分享的 Action 类型白名单（仅这些 action 完成后评估分享意图)
SHAREABLE_ACTION_IDS = {
    "buy_item", "receive_gift", "meet_friend", "achieve_goal",
    "finish_work", "play_game", "read_book", "travel",
}

# 触发分享的情绪状态
SHAREABLE_MOODS = {"excited", "happy", "surprised", "proud"}


class ProactiveSharingService:
    """主动分享服务

    使用方式：
        async with db.session() as session:
            svc = ProactiveSharingService(
                session=session,
                llm=llm,
                prompts=prompts,
                ws_manager=ws_manager,
            )
            await svc.evaluate_and_share(character_id, action_record, state)
    """

    def __init__(
        self,
        session,
        llm: LLMClient,
        prompts: PromptTemplates,
        ws_manager=None,
        redis=None,
    ):
        """
        Args:
            session: 异步数据库会话
            llm: LLM 客户端
            prompts: Prompt 模板管理器
            ws_manager: WebSocket 管理器（可选，无则不推送实时消息）
            redis: Redis 客户端（可选，用于读取世界状态）
        """
        self.session = session
        self.llm = llm
        self.prompts = prompts
        self.ws_manager = ws_manager
        self.redis = redis

        self.character_repo = CharacterRepository(session)
        self.conversation_repo = ConversationRepository(session)
        self.message_repo = MessageRepository(session)

    async def evaluate_and_share(
        self,
        character_id: UUID,
        action: ActionRecord | None = None,
        state: CharacterState | None = None,
    ) -> dict[str, Any]:
        """评估并执行主动分享

        流程：
        1. 加载角色与状态
        2. 检查分享频率限制（冷却 + 日限额）
        3. 评估分享意图（基于 action 类型与情绪）
        4. 若决定分享，生成文案
        5. 推送给所有与该角色有活跃会话的用户

        Args:
            character_id: 角色 ID
            action: 触发分享的 Action（可选）
            state: 角色当前状态（可选，未提供则从 DB 加载）

        Returns:
            {
                "shared": bool,
                "reason": str,           # 未分享的原因
                "content": str | None,   # 分享文案（shared=True 时）
                "recipients": int,       # 推送用户数
            }
        """
        # 1. 加载角色与状态
        character_data = await self.character_repo.get_character_with_state(character_id)
        if character_data is None:
            return {"shared": False, "reason": "character_not_found", "content": None, "recipients": 0}

        character, current_state = character_data
        if state is None:
            state = current_state

        # 不活跃角色不分享
        if not character.is_active:
            return {"shared": False, "reason": "character_inactive", "content": None, "recipients": 0}

        # 2. 评估分享意图
        should_share, intent_reason = self._evaluate_intent(action, state)
        if not should_share:
            return {"shared": False, "reason": intent_reason, "content": None, "recipients": 0}

        # 3. 检查频率限制
        cooldown_ok = await self._check_cooldown(character_id)
        if not cooldown_ok:
            return {"shared": False, "reason": "cooldown_active", "content": None, "recipients": 0}

        daily_count = await self._get_today_share_count(character_id)
        if daily_count >= settings.share_daily_limit:
            return {"shared": False, "reason": "daily_limit_reached", "content": None, "recipients": 0}

        # 4. 生成分享文案
        content = await self._generate_share_content(character, action, state)
        if not content:
            return {"shared": False, "reason": "content_generation_failed", "content": None, "recipients": 0}

        # 5. 推送给所有活跃会话用户
        recipients = await self._deliver_share(character_id, character, content)

        logger.info(
            "proactive_share_sent",
            character_id=str(character_id),
            character_name=character.name,
            content_length=len(content),
            recipients=recipients,
            trigger_action=action.action_id if action else None,
            mood=state.mood,
        )

        return {
            "shared": True,
            "reason": "ok",
            "content": content,
            "recipients": recipients,
        }

    async def send_routine_share(
        self,
        character_id: UUID,
        routine_type: str,
    ) -> dict[str, Any]:
        """发送日常分享（早安/晚安/吃饭等定时分享）

        Args:
            character_id: 角色 ID
            routine_type: 日常类型（morning_greeting/evening_greeting/meal_time/etc）

        Returns:
            同 evaluate_and_share 返回结构
        """
        character_data = await self.character_repo.get_character_with_state(character_id)
        if character_data is None:
            return {"shared": False, "reason": "character_not_found", "content": None, "recipients": 0}

        character, state = character_data
        if not character.is_active:
            return {"shared": False, "reason": "character_inactive", "content": None, "recipients": 0}

        # 日常分享也检查日限额（但不检查 action 触发冷却）
        daily_count = await self._get_today_share_count(character_id)
        if daily_count >= settings.share_daily_limit:
            return {"shared": False, "reason": "daily_limit_reached", "content": None, "recipients": 0}

        content = await self._generate_routine_content(character, state, routine_type)
        if not content:
            return {"shared": False, "reason": "content_generation_failed", "content": None, "recipients": 0}

        recipients = await self._deliver_share(character_id, character, content)

        logger.info(
            "routine_share_sent",
            character_id=str(character_id),
            character_name=character.name,
            routine_type=routine_type,
            recipients=recipients,
        )

        return {
            "shared": True,
            "reason": "ok",
            "content": content,
            "recipients": recipients,
        }

    def _evaluate_intent(
        self,
        action: ActionRecord | None,
        state: CharacterState,
    ) -> tuple[bool, str]:
        """评估分享意图（本地规则 + 概率控制）

        基于 action 类型、情绪状态、随机概率的综合判断。
        概率值从配置读取，可通过环境变量或前端动态调整。

        Returns:
            (should_share, reason)
        """
        import random

        # 规则 1：特定 Action 完成时分享（概率从配置读取）
        if action and action.action_id in SHAREABLE_ACTION_IDS:
            if random.random() < settings.share_probability_action:
                return True, f"action_{action.action_id}"
            return False, f"action_{action.action_id}_skip"

        # 规则 2：强烈情绪时分享（概率从配置读取）
        if state.mood and state.mood in SHAREABLE_MOODS:
            if random.random() < settings.share_probability_mood:
                return True, f"mood_{state.mood}"
            return False, f"mood_{state.mood}_skip"

        # 规则 3：位置变化时偶尔分享（概率从配置读取）
        if action and action.action_id == "move":
            if random.random() < settings.share_probability_location:
                return True, "location_change"
            return False, "location_change_skip"

        # 规则 4：日常行为偶尔分享（概率从配置读取）
        if action and action.action_id in ("read_book", "play_game", "relax", "use_phone"):
            if random.random() < settings.share_probability_routine:
                return True, f"routine_{action.action_id}"
            return False, f"routine_{action.action_id}_skip"

        # 规则 5：无触发条件
        return False, "no_trigger"

    async def _check_cooldown(self, character_id: UUID) -> bool:
        """检查分享冷却（基于最近一次分享的 share_id 时间）

        按 share_id 去重，避免一次分享投递给多个用户导致冷却误判。
        若距现在不足 SHARE_COOLDOWN_SECONDS 则冷却中。
        """
        from datetime import timedelta
        from sqlalchemy import text as sql_text

        from src.db.models import Message, Conversation

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.share_cooldown_seconds)

        # 按 share_id 去重查询最近一次分享时间
        stmt = (
            select(func.max(Message.created_at))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.character_id == character_id,
                Message.sender == "character",
                Message.created_at >= cutoff,
                Message.extra_data["share_type"].astext.isnot(None),
            )
        )
        result = await self.session.execute(stmt)
        last_share = result.scalar_one_or_none()

        # 若冷却期内有分享记录，则冷却中
        return last_share is None

    async def _get_today_share_count(self, character_id: UUID) -> int:
        """获取今日该角色的主动分享次数（按 share_id 去重）"""
        from datetime import datetime, timezone

        from src.db.models import Message, Conversation

        # 今日 UTC 0 点
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # 按 share_id 去重计数（一次分享投递给多个用户只算一次）
        stmt = (
            select(func.count(func.distinct(Message.extra_data["share_id"].astext)))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.character_id == character_id,
                Message.sender == "character",
                Message.created_at >= today_start,
                Message.extra_data["share_type"].astext.isnot(None),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _generate_share_content(
        self,
        character: Character,
        action: ActionRecord | None,
        state: CharacterState,
    ) -> str | None:
        """调用 LLM 生成分享文案

        Prompt 设计：
        - 以角色第一人称
        - 自然口语化，不暴露"系统消息"特征
        - 结合 action 结果与当前情绪
        - 注入世界状态约束时间/天气
        - 控制长度（50-100 字）
        """
        personality = (character.traits or {}).get("personality", [])
        personality_text = "、".join(personality) if isinstance(personality, list) else str(personality)

        action_desc = "刚做了一件事"
        if action:
            action_desc = f"刚{action.action_name or '做了一件事'}"
            if action.result:
                action_desc += f"，{action.result}"

        mood_desc = state.mood or "calm"

        # 读取世界状态
        world_section = ""
        if self.redis:
            try:
                world_state = await self.redis.hgetall("world:state")
                if world_state:
                    import json
                    world_time_raw = world_state.get("world_time", "")
                    try:
                        world_time = json.loads(world_time_raw)
                        if not isinstance(world_time, str):
                            world_time = world_time_raw
                    except (json.JSONDecodeError, TypeError):
                        world_time = world_time_raw
                    weather = world_state.get("weather", "sunny")
                    world_section = f"当前虚拟时间: {world_time}，天气: {weather}\n"
            except Exception:
                pass

        prompt = (
            f"你是 {character.name}，性格特点：{personality_text}。\n"
            f"{world_section}"
            f"你刚刚的经历：{action_desc}。\n"
            f"你现在的情绪：{mood_desc}。\n"
            f"请以 {character.name} 的身份，用自然口语向关心你的朋友分享此刻的心情，"
            f"50-100 字，不要提及'系统'或'AI'，要符合角色性格。\n"
            f"严格约束：不得编造与上述虚拟时间/天气不符的信息。\n"
            f"严格以世界状态中的事实为准，过往对话中的信息可能有误，两者冲突时以世界状态为准。"
            f"不要每句话都带emoji，可以使用颜文字如 (｡･ω･｡) (*≧▽≦)。\n"
            f"直接输出分享内容，不要加引号或前缀。\n"
            f"要人性化，避免暴露你是 AI 模型。"
            f"\n\n"
        )

        try:
            content = await self.llm.chat(prompt, model="chat")
            # 清理可能的引号包裹
            content = content.strip().strip('"').strip("'")
            if len(content) < 5:
                return None
            return content[:500]  # 截断超长内容
        except Exception as e:
            logger.error(
                "share_content_generation_failed",
                character_id=str(character.id),
                error=str(e),
                exc_info=True,
            )
            return None

    async def _generate_routine_content(
        self,
        character: Character,
        state: CharacterState,
        routine_type: str,
    ) -> str | None:
        """生成日常分享文案（早安/晚安等）"""
        personality = (character.traits or {}).get("personality", [])
        personality_text = "、".join(personality) if isinstance(personality, list) else str(personality)

        routine_prompts = {
            "morning_greeting": "清晨醒来，向朋友问好，分享新的一天的期待",
            "evening_greeting": "夜深了，向朋友道晚安，分享今天的小感悟",
            "meal_time": "正在吃饭，分享当下的美食与心情",
            "weekend": "周末到了，分享轻松愉快的心情",
        }

        routine_desc = routine_prompts.get(routine_type, "想跟朋友聊聊天")

        prompt = (
            f"你是 {character.name}，性格特点：{personality_text}。\n"
            f"当前情绪：{state.mood or 'calm'}，位置：{state.location or '家中'}。\n"
            f"场景：{routine_desc}。\n"
            f"请以 {character.name} 的身份，用自然口语说一句话，"
            f"30-80 字，不要提及'系统'或'AI'。\n"
            f"直接输出内容，不要加引号或前缀。"
        )

        try:
            content = await self.llm.chat(prompt, model="chat")
            content = content.strip().strip('"').strip("'")
            if len(content) < 5:
                return None
            return content[:500]
        except Exception as e:
            logger.error(
                "routine_content_generation_failed",
                character_id=str(character.id),
                routine_type=routine_type,
                error=str(e),
                exc_info=True,
            )
            return None

    async def _deliver_share(
        self,
        character_id: UUID,
        character: Character,
        content: str,
    ) -> int:
        """将分享消息推送给所有与该角色有活跃会话的用户

        - 写入 messages 表（sender=character, extra_data.share_type 标记）
        - 通过 WebSocketManager 实时推送（若可用）
        - 同一次分享的所有投递共享同一个 share_id，便于去重展示

        Returns:
            推送的用户数
        """
        # 查询所有活跃会话
        conversations = await self.conversation_repo.list_by_character(
            character_id=character_id,
            limit=100,
        )

        if not conversations:
            return 0

        from uuid import uuid4
        share_id = str(uuid4())  # 同一次分享的唯一 ID，所有投递共享
        now = datetime.now(timezone.utc)
        delivered = 0

        for conv in conversations:
            try:
                # 写入消息（标记为主动分享，带 share_id 便于去重）
                await self.message_repo.add(
                    conversation_id=conv.id,
                    sender="character",
                    content=content,
                    extra_data={
                        "share_type": "proactive",
                        "share_id": share_id,
                        "character_id": str(character_id),
                        "character_name": character.name,
                        "sent_at": now.isoformat(),
                    },
                )

                # WebSocket 实时推送
                if self.ws_manager is not None:
                    try:
                        await self.ws_manager.send_to_user(
                            user_id=conv.user_id,
                            character_id=character_id,
                            message={
                                "type": "share",
                                "content": content,
                                "character_name": character.name,
                                "character_id": str(character_id),
                                "timestamp": now.isoformat(),
                            },
                        )
                    except Exception as ws_err:
                        logger.debug(
                            "ws_push_failed",
                            user_id=conv.user_id,
                            error=str(ws_err),
                        )

                # 创建通知中心记录
                try:
                    from src.main import _create_notification
                    await _create_notification(
                        user_id=conv.user_id,
                        notif_type="share",
                        title=f"{character.name} 向你分享了动态",
                        content=content[:200],
                    )
                except Exception as notif_err:
                    logger.debug(
                        "notification_create_failed",
                        user_id=conv.user_id,
                        error=str(notif_err),
                    )

                delivered += 1
            except Exception as e:
                logger.error(
                    "share_delivery_failed",
                    conversation_id=str(conv.id),
                    user_id=conv.user_id,
                    error=str(e),
                    exc_info=True,
                )

        await self.session.commit()
        return delivered
