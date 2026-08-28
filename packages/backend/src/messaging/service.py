"""消息服务 - 处理用户与角色的对话

职责：
1. 接收用户消息，写入 messages 表
2. 构造 LLM 上下文（角色档案 + 对话历史 + 检索记忆 + Person Memory）
3. 调用 LLM 生成回复，写入 messages 表
4. 记录 token / cost 供成本控制
5. 维护 conversation.context 摘要（超过阈值时压缩）

用户对话的记忆沉淀走 Person Memory 独立管线（person_memories +
person_memory_entries），不写入 memory_episodes——角色间经历与
用户专属记忆是两套隔离体系（见 docs/memory-system.md）。

设计要点：
- 上下文窗口管理：保留最近 N 条消息（默认 20），超出走 LLM 摘要压缩
- 失败容错：LLM 调用失败时返回默认错误消息，不影响用户会话状态
- 事务边界：用户消息与角色回复在同一事务内提交，保证一致性
"""

from __future__ import annotations

import random
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.db.models import Character, CharacterState, Conversation, Message
from src.db.repositories import (
    CharacterRepository,
    ConversationRepository,
    MemoryRepository,
    MessageRepository,
)
from src.llm import LLMClient, PromptTemplates
from src.llm.client import bind_cost_scope, clear_cost_scope
from src.observability.langfuse_tracing import bind_chat_context, clear_chat_context
from src.observability.tracing import trace_span
from src.security.prompt_guard import PromptGuard

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# Prompt 防护实例（无状态，可复用）
_prompt_guard = PromptGuard()


# 上下文管理常量
DEFAULT_HISTORY_LIMIT = 20  # 默认拉取最近 20 条消息构造 history
CONTEXT_COMPRESS_THRESHOLD = 50  # 会话累计消息超过 50 条时触发压缩
COMPRESSED_HISTORY_LIMIT = 10  # 压缩后保留最近 10 条原文

# 认知注入（反思/日报）单条截断上限：浓缩长文的尾部对对话增益递减，
# 截断以守住用户对话的 token 预算（与 tick 决策注入同型护栏，规格更紧）
_COGNITION_ITEM_MAX_CHARS = 300
# 认知未开启/无数据/加载失败时统一占位：模板键恒存在，render_system 才能渲染
_COGNITION_EMPTY_TEXT = "暂无"

# 默认错误回复（LLM 失败时返回，避免用户会话阻塞）
DEFAULT_ERROR_REPLY = "（角色陷入了沉思，未能给出回复，请稍后再试）"

# LLM 输出无法解析出 response 字段时的兜底回复（R6-L12b）：
# 直接把 raw JSON/blob 原文透传给用户会暴露工程噪音，改用固定歉意文案；
# 原始输出记 warning 日志供排查
REPLY_EXTRACTION_FALLBACK = "（未能完全理解，换个说法试试）"

# 出站回复过滤（审查 安全-05）：LLM 输出→用户的单向路径此前零过滤。
# 角色可能泄露内部标识（UUID/字段名）或残留工程痕迹，统一在此剥离。
_OUTBOUND_LEAK_PATTERNS: tuple[tuple[str, str], ...] = (
    # UUID 形态（character_id / memory id 等内部标识）
    ("uuid", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    # 内部字段名泄露（user_id / character_id / action_id / tick_id）
    ("field_name", r"\b(user_id|character_id|action_id|tick_id|conversation_id|episode_id)\b"),
    # JSON 字段残留（"response": "xxx" 等未被解析干净的 blob）
    ("json_key", r'["\u201c](?:response|emotion|action)["\u201d]\s*[:：]'),
)


def _filter_outbound_reply(text: str) -> str:
    """剥离出站回复中的内部标识与工程痕迹（审查 安全-05）

    逐条匹配泄露模式并以中性占位替换。仅做剥离不做重写——
    保持语义完整性的前提下移除不可读的工程痕迹。
    """
    import re

    cleaned = text
    for desc, pattern in _OUTBOUND_LEAK_PATTERNS:
        cleaned = re.sub(pattern, f"[{desc}]", cleaned, flags=re.IGNORECASE)
    return cleaned


# 群聊智能回复各分支的回复概率（互斥分支非叠加，最终回复率为各触发路径的组合上界）
GROUP_REPLY_PROBABILITY_CAP = 0.7  # 疑问句启发式回复概率
GROUP_REPLY_EMOTION_PROBABILITY = 0.5  # 情绪句启发式回复概率
# 问候语命中回复概率（round-3 H5：原为确定性直接回复，是乒乓死循环的主要燃料——
# 两个关键词机器人互道早安永不停歇；0.9 闸门打破确定性互答，仅轻微降低活跃度）
GROUP_REPLY_GREETING_PROBABILITY = 0.9
GROUP_REPLY_LLM_NO_FALLBACK = 0.15  # LLM 判定不回复后的活跃度兜底


def _probability_roll(p: float) -> bool:
    """统一概率闸门：所有随机回复分支必须经过此处，保证概率语义单一可审计"""
    return random.random() < p


# 群聊智能回复：常见问候语关键词（命中则直接回复）
GREETING_KEYWORDS = frozenset(
    {
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "hey",
        "早上好",
        "下午好",
        "晚上好",
        "早安",
        "晚安",
        "午安",
        "在吗",
        "在不在",
        "有人吗",
        "你好呀",
        "你好啊",
        "哈喽啊",
        "大家好",
    }
)


def _build_greeting_matchers() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """问候关键词编译为匹配器：纯 ASCII 词用词边界，CJK 词用子串（R6-M1）

    ASCII 词必须整词命中——裸子串会让 hi/hey 命中 this/they/which 的内部，
    使 0.9 概率的问候层在混合语言群里变成误报引擎；CJK 无词边界概念，
    子串匹配才能覆盖「早上好呀」等扩展语气形式。按关键词排序保证
    多词同时命中时的决策与 reason 确定性。
    """
    return tuple(
        (
            keyword,
            re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
            if keyword.isascii()
            else re.compile(re.escape(keyword), re.IGNORECASE),
        )
        for keyword in sorted(GREETING_KEYWORDS)
    )


_GREETING_MATCHERS = _build_greeting_matchers()


def _match_greeting_keyword(text: str) -> str | None:
    """边界感知的问候语匹配：命中返回首个关键词，未命中返回 None"""
    for keyword, matcher in _GREETING_MATCHERS:
        if matcher.search(text):
            return keyword
    return None


# 匹配 [CQ:xxx,...] 码（OneBot 图片/表情/at 等）
_CQ_CODE_PATTERN = re.compile(r"\[CQ:[^\]]+\]")


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
        session: AsyncSession,
        llm: LLMClient,
        prompts: PromptTemplates,
        redis: Redis | None = None,
    ):
        """
        Args:
            session: 异步数据库会话
            llm: LLM 客户端
            prompts: Prompt 模板管理器
            redis: Redis 客户端（可选，用于读取世界状态注入对话上下文）
        """
        self.session = session
        self.llm = llm
        self.prompts = prompts
        self.redis = redis

        # Repository 实例（与 session 绑定）
        self.conversation_repo = ConversationRepository(session)
        self.message_repo = MessageRepository(session)
        self.character_repo = CharacterRepository(session)
        self.memory_repo = MemoryRepository(session)

    async def should_reply_in_group(
        self,
        character_id: UUID,
        character_name: str,
        message: str,
        sender_user_id: str,
    ) -> tuple[bool, str]:
        """群聊智能回复决策 - 判断角色是否应该回复这条非 @ 消息

        决策逻辑（四层过滤，从轻到重）：
        1. 关键词命中：消息包含角色名 → 直接回复；问候语 → 0.9 概率回复
        2. 启发式规则：疑问句 / 情绪强烈 → 概率回复
        3. LLM 判断：调用轻量级 LLM 判断相关性
        4. 概率兜底：LLM 未命中时小概率主动回复

        成本控制：
        - 每次调用最多 1 次 LLM 请求（chat 模型）
        - LLM 判断失败时不回复（fail-closed），避免故障期间爆发无上下文回复
        - CQ 码（图片/表情等）在判断前清理，避免 URL 中 ? 误判为疑问句

        Args:
            character_id: 角色 ID（用于加载角色档案）
            character_name: 角色名（用于关键词匹配）
            message: 群聊消息纯文本（已移除 @ 前缀）
            sender_user_id: 发送者内部用户 ID

        Returns:
            (should_reply, reason)
            - should_reply: 是否应该回复
            - reason: 决策原因（用于日志追踪）
        """
        if not message or not message.strip():
            return False, "empty_message"

        # 清理 CQ 码（图片/表情/at 等），避免 URL 中的 ? 误判为疑问句
        raw_text = message.strip()
        text = _CQ_CODE_PATTERN.sub("", raw_text).strip()

        # 如果清理后为空（纯图片/表情消息），用原始消息做后续判断
        if not text:
            text = raw_text

        # 1. 关键词命中
        # 1a. 消息包含角色名 → 直接回复
        if character_name and character_name in text:
            return True, "name_mentioned"

        # 1b. 问候语关键词 → 概率回复（round-3 H5：不再确定性直接回复。
        # 问候层无任何上下文判断，回显实现或第二个关键词机器人会与本角色
        # 互相触发问候形成乒乓死循环；0.9 概率闸门打破确定性互答。
        # 名字命中（1a）保持确定性：显式点名理应得到回应。
        # R6-M1：匹配边界感知——ASCII 词整词命中，不再误报单词内部子串。）
        greeting_hit = _match_greeting_keyword(text)
        if greeting_hit is not None:
            if _probability_roll(GROUP_REPLY_GREETING_PROBABILITY):
                return True, f"greeting:{greeting_hit}"
            return False, f"greeting_skip_probability:{greeting_hit}"

        # 2. 启发式规则（概率回复）
        # 2a. 疑问句（包含问号或疑问词结尾）
        if "?" in text or "？" in text or text.endswith("吗") or text.endswith("呢"):
            if _probability_roll(GROUP_REPLY_PROBABILITY_CAP):
                return True, "question_heuristic"
            return False, "question_skip_probability"

        # 2b. 情绪强烈（包含感叹号或 QQ 表情）
        if "！" in text or "!" in text or "[CQ:face" in raw_text:
            if _probability_roll(GROUP_REPLY_EMOTION_PROBABILITY):
                return True, "emotion_heuristic"
            return False, "emotion_skip_probability"

        # 3. LLM 判断：调用轻量级 LLM 判断相关性
        try:
            character_data = await self.character_repo.get_by_id(character_id)
            if character_data is None:
                return False, "character_not_found"

            personality = (character_data.traits or {}).get("personality", [])
            if isinstance(personality, list):
                personality_text = "、".join(personality)
            else:
                personality_text = str(personality)

            judge_prompt = self.prompts.render(
                "group_reply",
                character_name=character_name,
                personality=personality_text,
                backstory=character_data.backstory or "（无）",
                message=text,
            )

            result = await self.llm.structured_output(
                judge_prompt,
                schema={
                    "type": "object",
                    "properties": {
                        "should_reply": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["should_reply", "reason"],
                },
            )

            should = bool(result.get("should_reply", False))
            reason = result.get("reason", "llm_judgment")

            # LLM 判断为回复时，不再受概率上限约束（LLM 已做了相关性判断）
            if should:
                return True, f"llm:{reason}"

            # 4. 概率兜底：LLM 说不回复时，仍有小概率主动回复（增加活跃度）
            if _probability_roll(GROUP_REPLY_LLM_NO_FALLBACK):
                return True, f"random_fallback:{reason}"

            return False, f"llm_no:{reason}"

        except Exception as e:
            logger.warning(
                "group_reply_judge_failed",
                character_id=str(character_id),
                error=str(e),
                error_type=type(e).__name__,
            )
            # R6-M2：fail-closed——判定链路不可用时保持沉默，故障期间
            # 宁可少说话，也不发无上下文的随机回复（与文档承诺一致）
            return False, f"llm_judge_error:{type(e).__name__}"

    @trace_span("message.process")
    async def handle_user_message(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        content: str,
        group_context: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
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
        # 0. Prompt 注入检测 + 输入消毒
        start_perf = time.perf_counter()
        is_safe, matched_pattern = _prompt_guard.check_injection(content)
        if not is_safe:
            logger.warning(
                "prompt_injection_blocked",
                character_id=str(character_id),
                user_id=user_id,
                pattern=matched_pattern,
            )
            from src.observability.metrics import MESSAGE_PROCESSED_TOTAL

            MESSAGE_PROCESSED_TOTAL.labels(platform=platform, status="failed").inc()
            return {
                "conversation_id": None,
                "message_id": None,
                "content": "（检测到不安全的内容，已拦截）",
                "tokens": 0,
                "cost": 0.0,
                "error": "prompt_injection_blocked",
            }

        # 消毒用户输入（移除危险内容 + 控制字符 + 长度截断）
        content = _prompt_guard.sanitize_user_input(content)

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
            from src.observability.metrics import MESSAGE_PROCESSED_TOTAL

            MESSAGE_PROCESSED_TOTAL.labels(platform=platform, status="failed").inc()
            return {
                "conversation_id": conversation.id,
                "message_id": None,
                "content": DEFAULT_ERROR_REPLY,
                "tokens": 0,
                "cost": 0.0,
                "error": "character_not_found",
            }

        character, state = character_data

        # 4. 构造 LLM 上下文（返回 chat.yaml 模板参数字典）
        history = await self.message_repo.list_recent(
            conversation_id=conversation.id,
            limit=DEFAULT_HISTORY_LIMIT,
        )
        # 排除刚写入的用户消息（避免在 history 中重复）
        # list_recent 返回最近 N 条含刚写入的，需确保末尾为用户消息
        context = await self._build_context(
            conversation=conversation,
            character=character,
            state=state,
            history=history,
            group_context=group_context,
            user_message=content,
        )

        # 5. 调用 LLM 生成回复
        # 会话上下文仅覆盖主生成段：Langfuse 靠 session_id 把同一会话的
        # trace 归组，缺失时对话在 UI 中无法按用户回溯（R5-L16）
        bind_chat_context(session_id=str(conversation.id), user_id=user_id)
        # 预算归属绑定到用户：QQ 是公开入口，单用户高频对话不应挤占全局预算
        # 拖垮整个小镇（审查 §4.8.2 成本-02）
        bind_cost_scope(character_id=str(character_id), user_id=user_id)
        try:
            reply_text, tokens, cost, error = await self._generate_reply(
                character=character,
                context=context,
                history=history,
                user_message=content,
            )
        finally:
            clear_cost_scope()
            clear_chat_context()

        # 6. 写入角色回复
        # 生成失败（error 非 None）时 reply_text 为兜底文案，不能以「character」
        # 身份落库：否则会作为角色自己的话混入后续 prompt 的 history，污染人设。
        # history 组装只保留 user/character 两种 sender，system 消息天然被排除
        sender = "system" if error else "character"
        reply_msg = await self.message_repo.add(
            conversation_id=conversation.id,
            sender=sender,
            content=reply_text,
            tokens=tokens,
            cost=cost,
            extra_data={"error": error} if error else None,
        )

        # 7. 更新会话（轻量更新 last_message_at，必要时压缩 context）
        await self._maybe_compress_context(conversation, character)

        await self.session.commit()

        from src.observability.metrics import MESSAGE_PROCESSED_TOTAL, MESSAGE_PROCESSING_DURATION

        duration = time.perf_counter() - start_perf
        if error:
            MESSAGE_PROCESSED_TOTAL.labels(platform=platform, status="failed").inc()
        else:
            MESSAGE_PROCESSED_TOTAL.labels(platform=platform, status="success").inc()
            MESSAGE_PROCESSING_DURATION.observe(duration)

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

        # 异步更新角色对用户的记忆（不阻塞回复）
        try:
            from src.db.session import db
            from src.memory.person_memory_service import PersonMemoryService

            pm_service = PersonMemoryService(
                session_factory=db.session,  # 使用独立的 session factory
                llm_client=self.llm,
                prompts=self.prompts,
            )
            # 异步执行，不等待；注册表持有强引用并记录异常（P-2）
            from src.core.background import spawn_background

            spawn_background(
                pm_service.update_memory(
                    character_id=character_id,
                    character_name=character.name,
                    user_id=user_id,
                    platform=platform,
                    user_message=content,
                    character_reply=reply_text,
                ),
                name=f"person_memory_update:{character_id}",
            )
        except Exception as e:
            logger.warning("person_memory_spawn_failed", error=str(e))  # 记忆更新失败不影响主流程

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
        state: CharacterState,
        history: list[Message],
        group_context: list[dict[str, str]] | None = None,
        user_message: str | None = None,
    ) -> dict[str, Any]:
        """构造 LLM 上下文字段（供 chat.yaml 模板渲染使用）

        返回包含所有 chat 模板占位符的字典：
        name, personality, backstory, world_time, weather,
        location, energy, mood, context_summary, group_context,
        person_memory, reflections, diary

        Args:
            conversation: 会话对象
            character: 角色档案
            state: 角色实时状态
            history: 最近消息列表
            group_context: 群聊近期消息（R4-M14，仅 QQ 群聊传入）

        Returns:
            模板参数字典
        """
        personality = (character.traits or {}).get("personality", [])
        if isinstance(personality, list):
            personality_text = "、".join(personality)
        else:
            personality_text = str(personality)

        # 优先使用已压缩的 context 摘要
        context_summary = ""
        if conversation.context:
            context_summary = conversation.context.get("summary", "")

        # 读取世界状态（虚拟时间/天气）
        world_time = "未知"
        weather = "sunny"
        if self.redis:
            try:
                world_state = await self.redis.hgetall("world:state")
                if world_state:
                    import json

                    world_time_raw = str(world_state.get("world_time", ""))
                    try:
                        parsed = json.loads(world_time_raw)
                        world_time = parsed if isinstance(parsed, str) else world_time_raw
                    except (json.JSONDecodeError, TypeError):
                        world_time = world_time_raw
                    weather = str(world_state.get("weather", "sunny"))
            except Exception:
                pass  # Redis 读取失败不影响对话

        # 角色对该用户的长期认知回流对话上下文（审查 §五-P0：Person Memory 只写不读修复）
        person_memory_text = "（初次与该用户交流）"
        try:
            from src.db.session import db
            from src.memory.person_memory_service import PersonMemoryService

            pm_service = PersonMemoryService(session_factory=db.session)
            # P1-9：以当前消息为检索线索召回相关记忆条目
            person_memory_text = await pm_service.get_relevant_context(
                character.id, conversation.user_id, query_hint=user_message
            )
        except Exception as e:
            logger.warning("person_memory_context_load_failed", error=str(e))

        # 近期认知注入（反思+最新日报）：默认关闭保持上下文精简；开启后角色
        # 带着「最近想过什么/经历过什么」回应用户。占位恒传，保证模板键完整。
        from src.config import settings

        if settings.chat_inject_cognition:
            reflections_text, diary_text = await self._load_cognition_texts(character.id)
        else:
            reflections_text, diary_text = _COGNITION_EMPTY_TEXT, _COGNITION_EMPTY_TEXT

        # 近期经历注入（对齐 Tick 决策感知）：角色回复时知道"自己最近在小镇里
        # 经历过什么"——语义检索相关记忆 + 传闻 + 世界动态 + 当前计划。
        # 与 chat_inject_cognition 解耦：近期经历是角色自我认知的基础事实，
        # 默认注入（用户对话常问「你最近在做什么」，缺失会导致答非所问）。
        (
            recent_experiences_text,
            recent_gossip_text,
            world_events_text,
            current_plans_text,
        ) = await self._load_recent_context(
            character.id,
            character_name=character.name,
            location=state.location or "未知",
            mood=state.mood or "平静",
            user_message=user_message,
        )

        # 群聊共享上下文（R4-M14）：群消息按发送者建独立会话，其他成员的消息
        # 此前完全不进上下文，多方对话答非所问；此处注入群内近期发言
        if group_context:
            group_lines = [
                f"- {item.get('sender', '群友')}：{item.get('text', '')[:100]}"
                for item in group_context
                if isinstance(item, dict)
            ]
            group_context_text = "\n".join(group_lines) if group_lines else _COGNITION_EMPTY_TEXT
        else:
            group_context_text = "（非群聊场景）"

        return {
            "name": character.name,
            "personality": personality_text,
            "backstory": character.backstory or "（无）",
            "world_time": world_time,
            "weather": weather,
            "location": state.location or "未知",
            "energy": state.stamina,
            "mood": state.mood or "calm",
            "context_summary": context_summary or "（新对话，暂无摘要）",
            "group_context": group_context_text,
            "person_memory": person_memory_text,
            "reflections": reflections_text,
            "diary": diary_text,
            "recent_experiences": recent_experiences_text,
            "recent_gossip": recent_gossip_text,
            "world_events": world_events_text,
            "current_plans": current_plans_text,
        }

    async def _load_cognition_texts(self, character_id: UUID) -> tuple[str, str]:
        """加载角色近期认知（top-3 反思 + 最新日报）供对话注入

        失败隔离：任一来源失败仅记录 warning 并降级为「暂无」，
        不阻断对话主流程（与 tick 决策注入的分块降级模式一致）。

        Args:
            character_id: 角色 ID

        Returns:
            (reflections_text, diary_text)，无数据/失败时为「暂无」
        """
        reflections_text = _COGNITION_EMPTY_TEXT
        diary_text = _COGNITION_EMPTY_TEXT

        try:
            from src.db.repositories import DiaryRepository, ReflectionRepository
            from src.db.session import db

            async with db.session() as session:
                try:
                    refs = await ReflectionRepository(session).get_by_character(character_id, limit=3)
                    if refs:
                        reflections_text = "\n".join(f"- {r.content[:_COGNITION_ITEM_MAX_CHARS]}" for r in refs)
                except Exception as e:
                    await session.rollback()
                    logger.warning(
                        "chat_reflections_load_failed_continue",
                        character_id=str(character_id),
                        error=str(e),
                    )

                try:
                    latest_diary = await DiaryRepository(session).get_latest(character_id, period="day")
                    if latest_diary and latest_diary.content:
                        diary_text = latest_diary.content[:_COGNITION_ITEM_MAX_CHARS]
                except Exception as e:
                    await session.rollback()
                    logger.warning(
                        "chat_diary_load_failed_continue",
                        character_id=str(character_id),
                        error=str(e),
                    )
        except Exception as e:
            logger.warning(
                "chat_cognition_session_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        return reflections_text, diary_text

    async def _load_recent_context(
        self,
        character_id: UUID,
        *,
        character_name: str,
        location: str,
        mood: str,
        user_message: str | None,
    ) -> tuple[str, str, str, str]:
        """加载角色近期经历上下文（对齐 Tick 决策感知）

        返回 (recent_experiences, recent_gossip, world_events, current_plans)
        ——四段文本已渲染为模板可直接嵌入的字符串。任一来源失败仅降级为占位，
        不阻断对话主流程（与 _load_cognition_texts 同型护栏）。
        """
        experiences_text = "（暂无近期经历）"
        gossip_text = _COGNITION_EMPTY_TEXT
        world_events_text = _COGNITION_EMPTY_TEXT
        plans_text = _COGNITION_EMPTY_TEXT

        from src.db.session import db

        # 1. 近期经历：语义检索相关记忆（对齐 perception 动态查询：角色+位置+
        #    情绪+当前消息线索）。检索失败降级为时间倒序最近 12 条。
        try:
            async with db.session() as session:
                mem_repo = MemoryRepository(session)
                memories: list[Any] = []
                query_hint = (user_message or "").strip()[:80]
                try:
                    # 语义检索优先：以「角色当前处境 + 用户当前话题」为查询线索
                    query = f"{character_name}最近在{location}的经历，{mood}，{query_hint}，相关往事与最近发生的事"
                    query_vec = await self.llm.embed(query)
                    memories = await mem_repo.search_hybrid(character_id=character_id, query_vec=query_vec, top_k=12)
                except Exception as e:
                    logger.warning(
                        "chat_recent_memory_semantic_failed_continue",
                        character_id=str(character_id),
                        error=str(e),
                    )
                    # 降级：时间倒序最近经历（无向量也能注入基础事实）
                    episodes = await mem_repo.recent(character_id, limit=12)
                    memories = [
                        {
                            "content": e.content,
                            "importance": e.importance,
                            "timestamp": e.timestamp,
                        }
                        for e in episodes
                    ]

                if memories:
                    lines = []
                    for mem in memories[:12]:
                        content = str(mem.get("content", ""))
                        if not content:
                            continue
                        lines.append(f"- {content[:_COGNITION_ITEM_MAX_CHARS]}")
                    if lines:
                        experiences_text = "\n".join(lines)
        except Exception as e:
            logger.warning(
                "chat_recent_context_load_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        # 2-4. 传闻 / 世界动态 / 当前计划：合并单会话查询，失败各自降级
        from src.db.repositories import PlanRepository, WorldEventRepository

        async with db.session() as session:
            try:
                gossips = await MemoryRepository(session).fetch_recent_gossip(character_id, hours=24, limit=2)
                if gossips:
                    gossip_text = "\n".join(f"- {g}" for g in gossips)
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "chat_gossip_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            try:
                world = await self._read_world_state()
                current_tick = int(str(world.get("tick_id", "0")) or 0)
                if current_tick > 0:
                    notable = await WorldEventRepository(session).get_recent_notable(current_tick)
                    if notable:
                        world_events_text = "\n".join(f"- {e.event_type}: {str(e.payload)[:120]}" for e in notable)
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "chat_world_events_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            try:
                plans = await PlanRepository(session).get_active_plans(character_id)
                if plans:
                    plans_text = "\n".join(f"- {p.title}" for p in plans[:5])
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "chat_plans_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

        return experiences_text, gossip_text, world_events_text, plans_text

    async def _read_world_state(self) -> dict[str, Any]:
        """读取 Redis 世界状态（失败返回空 dict，不阻断）"""
        if not self.redis:
            return {}
        try:
            raw = await self.redis.hgetall("world:state")
            if not raw:
                return {}
            return {str(k): v.decode() if isinstance(v, bytes) else v for k, v in raw.items()}
        except Exception as e:
            logger.warning("chat_world_state_read_failed_continue", error=str(e))
            return {}

    async def _generate_reply(
        self,
        character: Character,
        context: dict[str, Any],
        history: list[Message],
        user_message: str,
    ) -> tuple[str, int, float, str | None]:
        """调用 LLM 生成角色回复

        Args:
            character: 角色档案
            context: chat.yaml 模板参数字典（由 _build_context 返回）
            history: 对话历史
            user_message: 用户消息

        Returns:
            (reply_text, tokens, cost, error)
            - error 非 None 时 reply_text 为默认错误回复
        """
        # 构造历史文本（最近 N 条）
        history_text = "\n".join(
            [
                f"{'用户' if m.sender == 'user' else character.name}: {m.content}"
                for m in history
                if m.sender in ("user", "character")
            ]
        )

        try:
            # 构建安全 prompt（用户消息用分隔符包裹，防止角色覆盖）
            safe_user_message = _prompt_guard.wrap_user_message(user_message)
            # 渲染 SystemMessage（安全底线+硬约束，优先级最高）
            system_prompt: str | None = None
            if self.prompts.has_system("chat"):
                system_prompt = self.prompts.render_system("chat", **context)
            prompt = self.prompts.render(
                "chat",
                **context,
                history=history_text,
                user_message=safe_user_message,
            )

            response, usage = await self.llm.chat_with_usage(prompt, system_prompt=system_prompt)

            # chat.yaml 要求 LLM 输出 JSON：{"response", "emotion", "action"}
            # 这里容错解析：优先提取 JSON 中的 response 字段；解析失败则直接使用原文
            reply_text = self._extract_chat_response(response)
            # 出站过滤：剥离内部标识泄露（安全-05）
            reply_text = _filter_outbound_reply(reply_text)

            # 持久化真实 token 用量（A-7：预算/持久化/指标单轨，杜绝估算值）
            return reply_text, usage.total_tokens, usage.cost, None

        except Exception as e:
            logger.error(
                "llm_reply_failed",
                character_id=str(character.id),
                error=str(e),
                exc_info=True,
            )
            return DEFAULT_ERROR_REPLY, 0, 0.0, str(e)

    @staticmethod
    def _extract_chat_response(raw: str) -> str:
        """从 LLM 输出中提取回复文本

        chat.yaml 要求输出 JSON：{"response": "...", "emotion": "...", "action": "..."}
        但 LLM 可能：
        1. 直接返回纯文本（旧模型或配置变更）
        2. 返回带 markdown code fence 的 JSON
        3. 返回 JSON 但带额外说明文字
        4. 返回 malformed JSON（缺少逗号、引号不匹配等）

        本方法依次尝试：
        1. 标准 JSON 解析
        2. 正则提取 "response" 字段值
        3. 返回原文

        Args:
            raw: LLM 原始输出

        Returns:
            提取后的回复文本
        """
        import json
        import re

        text = raw.strip()
        # 去除可能的 markdown code fence
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 策略1：标准 JSON 解析
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                response = data.get("response")
                if isinstance(response, str) and response.strip():
                    return response.strip()
        except (json.JSONDecodeError, TypeError):
            pass

        # 策略2：正则提取 "response" 字段值（容错 malformed JSON）
        # 匹配 "response": "..." 或 "response"："..."（中文冒号）
        # 支持转义引号和跨行内容
        match = re.search(
            r'"response"\s*[:：]\s*"((?:[^"\\]|\\.)*)"',
            text,
            re.DOTALL,
        )
        if match:
            value = match.group(1)
            # 反转义常见的转义序列
            value = value.replace("\\n", "\n").replace("\\'", "'").replace('\\"', '"')
            if value.strip():
                return value.strip()

        # 策略3：未解析出 response 字段，说明输出不符合 chat.yaml 的 JSON 契约。
        # 不再透传 raw 原文（可能是 JSON 垃圾），改为固定歉意文案；原文入日志排查
        logger.warning(
            "chat_reply_extraction_failed",
            raw_length=len(text),
            raw_preview=text[:300],
        )
        return REPLY_EXTRACTION_FALLBACK

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
        history_text = "\n".join(
            [
                f"{'用户' if m.sender == 'user' else character.name}: {m.content}"
                for m in reversed(to_compress)  # 时间正序
                if m.sender in ("user", "character")
            ]
        )

        try:
            compress_prompt = self.prompts.render(
                "context_compress",
                character_name=character.name,
                history_text=history_text,
            )
            summary = await self.llm.chat(compress_prompt)

            # 写入压缩后的 context
            existing_context = conversation.context or {}
            existing_context["summary"] = summary
            existing_context["compressed_at"] = datetime.now(UTC).isoformat()
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
