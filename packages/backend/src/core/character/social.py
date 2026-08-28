"""Character Tick 社交动作 - SocialMixin

从 tick.py 机械抽取的社交/群体动力学分支（R5-L14 行为保持重构）：
角色间多轮对话（chat_with）、群活动集体叙事、传闻传播与主动分享。
跨角色资源锁、失锁闸口的调用点随方法迁入；锁原语本身仍在 core.locks。

专属模块级助手（chat 常量、_clip_tail、_personality_text、_parse_chat_line）
随其唯一消费者一并迁入，不在原处保留副本。
"""

import asyncio
import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from structlog import get_logger

from src.actions import DecisionResult
from src.config import settings
from src.core.locks import acquire_resource_locks
from src.db.models import Character
from src.db.repositories import CharacterRepository, MemoryRepository
from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.memory import EpisodeService
from src.memory.group_activity_service import GroupActivityService, parse_group_narrative
from src.modules.relation.graph import RelationGraph
from src.runtime import get_proactive_share_handler

logger = get_logger(__name__)


# === chat_with 多轮对话常量 ===

# 轮数硬上限：无论配置多大最多 3 轮（每轮双方各一句 = 2 次 LLM 调用），
# 控制单次 chat_with 的调用次数上界
_CHAT_MAX_ROUNDS = 3
# 每轮 prompt 只携带对话记录的末尾窗口：逐轮重发全量记录会让 token 成本随轮数平方增长，
# 且对下一句真正有影响的只是最近的交流
_CHAT_TRANSCRIPT_MAX_CHARS = 800
# 整场对话写入记忆前压缩到该长度以内：记忆条目被长期检索复用，超长原文会稀释向量召回
_CHAT_MEMORY_MAX_CHARS = 300
# LLM 评估的关系增量钳制范围：防止单次对话的异常评分扭曲关系图谱
_CHAT_QUALITY_DELTA_LIMIT = 10
# LLM 评估不可用时的固定回退增量（历史行为）：陌生人破冰小幅加固，其他常规增幅
_CHAT_LEGACY_STRANGER_DELTA = 2
_CHAT_LEGACY_DEFAULT_DELTA = 5


def _clip_tail(text: str, max_chars: int) -> str:
    """只保留文本末尾 max_chars 字符（对话的最新部分比开场更影响下一句）"""
    return text[-max_chars:]


def _personality_text(c: Character) -> str:
    """性格列表转顿号分隔文本（chat prompt 注入用）"""
    p = (c.traits or {}).get("personality", [])
    return "、".join(p) if isinstance(p, list) else str(p)


def _parse_chat_line(raw: str) -> str | None:
    """解析单轮台词的严格 JSON 输出；失败返回 None（调用方判定整场对话失败）"""
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.split("\n") if not ln.startswith("```")).strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    line = parsed.get("line") if isinstance(parsed, dict) else None
    return line.strip() if isinstance(line, str) and line.strip() else None


class SocialMixin:
    """社交动作 Mixin。

    契约：不定义 __init__，依赖宿主引擎初始化的属性：
    - redis：Redis 客户端（跨角色资源锁、RelationGraph、群活动在场记账）
    - llm：LLM 客户端（对话生成与关系评估）
    - prompts：Prompt 模板管理器（chat_turn / chat_quality / group_activity）
    """

    redis: Redis
    llm: LLMClient
    prompts: PromptTemplates

    async def _handle_character_chat(
        self,
        character_id: UUID,
        decision: DecisionResult,
        context: dict[str, Any],
        *,
        lock_lost: asyncio.Event | None = None,
    ) -> str | None:
        """处理角色间对话（多智能体交互核心）

        当 LLM 选择 chat_with Action 时调用：
        1. 校验 target_character_id 在同场景
        2. 加载双方角色档案与关系
        3. 用 LLM 生成一段简短对话（双方各一句）
        4. 通过 RelationGraph 更新双向关系（+5 强度，陌生人破冰 +2）
        5. 为双方各写入一条 MemoryEpisode（source_type=interaction）
        6. 返回对话文本，供 ActionRecord.result 持久化

        Args:
            character_id: 发起方角色 ID
            decision: 决策结果（params.target_character_id 必填）
            context: 感知环境结果（用于读取 nearby_characters 验证同场景）
            lock_lost: 看门狗失锁信号，透传给 _do_chat_with 的写入闸口

        Returns:
            对话文本（含双方发言），失败返回 None
        """
        target_id_str = decision.params.get("target_character_id")
        if not target_id_str:
            logger.warning("chat_with_no_target", character_id=str(character_id))
            return None

        try:
            target_id = UUID(target_id_str)
        except (ValueError, TypeError):
            logger.warning("chat_with_invalid_target_id", character_id=str(character_id), raw=target_id_str)
            return None

        # 校验目标在 nearby_characters 中（同场景）
        nearby = context.get("nearby_characters") or []
        nearby_ids = {n["id"] for n in nearby}
        if target_id_str not in nearby_ids:
            logger.warning(
                "chat_with_target_not_nearby",
                character_id=str(character_id),
                target_id=target_id_str,
            )
            return None

        # 加载双方档案
        character = context["character"]

        # 跨角色资源锁：防止 A→B 和 B→A 同时执行导致关系更新竞争

        # 跨角色锁的失锁独立于 Tick 锁：两者保护的写入集合不同，
        # 混用同一个事件会让「资源锁丢失」连带掐掉本角色自己的记忆写入
        resource_lost = asyncio.Event()
        async with acquire_resource_locks(self.redis, character_id, target_id, lock_lost=resource_lost) as acquired:
            if not acquired:
                logger.info(
                    "chat_with_lock_busy",
                    character_id=str(character_id),
                    target_id=target_id_str,
                )
                return None
            return await self._do_chat_with(
                character_id,
                target_id,
                target_id_str,
                character,
                decision,
                context,
                lock_lost=lock_lost,
                resource_lost=resource_lost,
            )

    async def _do_chat_with(
        self,
        character_id: UUID,
        target_id: UUID,
        target_id_str: str,
        character: Any,
        decision: DecisionResult,
        context: dict[str, Any],
        *,
        lock_lost: asyncio.Event | None = None,
        resource_lost: asyncio.Event | None = None,
    ) -> str | None:
        """chat_with 实际执行逻辑（在跨角色锁保护下运行）

        多轮对话：每轮发起方先说一句、对方回一句（逐轮小 LLM 调用，
        各自看到不断增长的对话记录）；结束后由 LLM 评估本次交流对
        双方关系的增量，评估不可用时回退固定值。

        失锁闸口（R5-M6）：多轮对话的 LLM 往返可能横跨锁 TTL，锁易主后
        关系与记忆写入一律跳过——实现与 _execute_action docstring 的
        「失锁后无任何 PG 写入」承诺保持一致。

        Args:
            lock_lost: 角色 Tick 锁的失锁信号
            resource_lost: 跨角色资源锁的失锁信号（审查 §4.1.3 并发-03）。
                两者任一置位即中止关系写入；此前资源锁续租失败只记日志，
                调用方无从感知，锁易主后仍会写关系。
        """
        lost = lock_lost if lock_lost is not None else asyncio.Event()
        resource_locks_lost = resource_lost if resource_lost is not None else asyncio.Event()
        async with db.session() as session:
            char_repo = CharacterRepository(session)
            target_data = await char_repo.get_character_with_state(target_id)
        if target_data is None:
            logger.warning("chat_with_target_not_found", target_id=target_id_str)
            return None
        target_char, _ = target_data

        # 读取关系（用于在 prompt 中说明亲密度，影响对话语气）
        rel_snapshot = None
        try:
            async with db.session() as rel_session:
                graph = RelationGraph(rel_session, self.redis)
                rel_snapshot = await graph.get_relation(character_id, target_id)
        except Exception as e:
            logger.debug("chat_relation_query_failed_continue", error=str(e))

        relationship_desc = "陌生人"
        # 缺省 20 与 RelationGraph.ensure_relation 首次建交写入的默认强度一致
        rel_strength = 20
        if rel_snapshot:
            relationship_desc = rel_snapshot.relationship_type
            rel_strength = rel_snapshot.strength

        state = context["state"]
        world = context["world"]

        # 检索双方共同经历（关系记忆注入）：related_characters 含对方即「共同经历」，
        # 按时间倒序取最近 3 条，注入对话 prompt 让角色「还记得上次…」
        shared_memories: list[str] = []
        try:
            async with db.session() as mem_session:
                mem_repo = MemoryRepository(mem_session)
                rows = await mem_repo.search_shared_with(character_id, target_id, limit=3)
                shared_memories = [r.content[:60] for r in rows if r.content]
        except Exception as e:
            logger.debug("chat_shared_memory_query_failed_continue", error=str(e))
        shared_memory_block = (
            "你们之间的共同经历：\n" + "\n".join(f"- {m}" for m in shared_memories) if shared_memories else ""
        )

        # 逐轮生成；任一句生成/解析失败即整场失败，调用方降级为 wait（保持既有语义）
        rounds = min(settings.chat_with_max_rounds, _CHAT_MAX_ROUNDS)
        transcript_lines: list[str] = []
        try:
            for round_index in range(rounds):
                for speaker, listener in ((character, target_char), (target_char, character)):
                    line = await self._generate_chat_turn(
                        speaker=speaker,
                        listener=listener,
                        relationship_desc=relationship_desc,
                        rel_strength=rel_strength,
                        topic_hint=decision.reason,
                        state=state,
                        world=world,
                        transcript="\n".join(transcript_lines),
                        shared_memory_block=shared_memory_block,
                    )
                    if line is None:
                        logger.error(
                            "chat_dialogue_generation_failed",
                            character_id=str(character_id),
                            target_id=target_id_str,
                            error="turn_parse_failed",
                        )
                        return None
                    transcript_lines.append(f"{speaker.name}: {line}")
                    logger.info(
                        "chat_with_turn_completed",
                        character_id=str(character_id),
                        target_id=target_id_str,
                        round=round_index + 1,
                        speaker=speaker.name,
                        listener=listener.name,
                    )
        except Exception as e:
            logger.error(
                "chat_dialogue_generation_failed",
                character_id=str(character_id),
                target_id=target_id_str,
                error=str(e),
                exc_info=True,
            )
            return None

        dialogue = "\n".join(transcript_lines)

        # 关系增量：优先 LLM 按对话质量评估，不可用时回退固定值——
        # 文档化降级（同 group_activity 模板叙事回退哲学），非静默吞错
        strength_delta = _CHAT_LEGACY_STRANGER_DELTA if relationship_desc == "stranger" else _CHAT_LEGACY_DEFAULT_DELTA
        quality_assessed = False
        if settings.chat_quality_enabled:
            assessed = await self._assess_chat_delta(
                char_a_name=character.name,
                char_b_name=target_char.name,
                relationship_type=relationship_desc,
                strength=rel_strength,
                transcript=dialogue,
            )
            if assessed is not None:
                strength_delta = assessed
                quality_assessed = True
            else:
                logger.warning(
                    "chat_quality_fallback_legacy_delta",
                    character_id=str(character_id),
                    target_id=target_id_str,
                    strength_delta=strength_delta,
                )

        # H10 闸口：对话生成完成后、任何持久化写入前自查——多轮 LLM 往返期间
        # 锁可能已被他实例接走，继续写关系/记忆即 double-tick；
        # 对话文本照常返回，其后的 ActionRecord/Redis 写入由调用方失锁闸口拦截
        if lost.is_set() or resource_locks_lost.is_set():
            logger.warning(
                "chat_with_writes_skipped_lock_lost",
                character_id=str(character_id),
                target_id=target_id_str,
                tick_lock_lost=lost.is_set(),
                resource_lock_lost=resource_locks_lost.is_set(),
            )
            return dialogue

        # 更新双向关系（双方同步）
        try:
            async with db.session() as rel_session:
                graph = RelationGraph(rel_session, self.redis)
                await graph.update_on_interaction(
                    char_a=character_id,
                    char_b=target_id,
                    strength_delta=strength_delta,
                )
        except Exception as e:
            logger.warning(
                "chat_relation_update_failed_continue",
                character_id=str(character_id),
                target_id=target_id_str,
                error=str(e),
            )

        # 为双方各写入一条记忆（source_type=conversation）
        # 让两人都记得这次多轮交流，未来检索时可回忆起。
        # R6-M7：统一经 EpisodeService 落库——近邻去重、LLM 重要性评分与
        # embedding worker 管线对对话记忆与 Action 记忆一致生效，
        # 不再绕过服务层直插 ORM（此前无去重、无评分、importance 硬编码）
        try:
            async with db.session() as session:
                episode_service = EpisodeService(self.llm, MemoryRepository(session), prompts=self.prompts)

                # 发起方记忆：第一人称视角
                await episode_service.create_episode(
                    character_id=character_id,
                    content=(
                        f"在{state.get('location', '某处')}和{target_char.name}聊天。"
                        f"{dialogue[:_CHAT_MEMORY_MAX_CHARS]}"
                    ),
                    location=state.get("location"),
                    importance=6,
                    character_name=character.name,
                    reason=decision.reason,
                    mood=state.get("mood"),
                    related_characters=[target_id],
                    source_type="conversation",
                )

                # 对方记忆：第一人称视角（target 视角）
                await episode_service.create_episode(
                    character_id=target_id,
                    content=(
                        f"在{state.get('location', '某处')}和{character.name}聊天。{dialogue[:_CHAT_MEMORY_MAX_CHARS]}"
                    ),
                    location=state.get("location"),
                    importance=6,
                    character_name=target_char.name,
                    reason=decision.reason,
                    mood=state.get("mood"),
                    related_characters=[character_id],
                    source_type="conversation",
                )
                await session.commit()
        except Exception as e:
            logger.warning(
                "chat_memory_persist_failed_continue",
                character_id=str(character_id),
                target_id=target_id_str,
                error=str(e),
            )

        logger.info(
            "character_chat_completed",
            character_id=str(character_id),
            target_id=target_id_str,
            character_name=character.name,
            target_name=target_char.name,
            relationship=relationship_desc,
            strength_delta=strength_delta,
            quality_assessed=quality_assessed,
            rounds=len(transcript_lines),
            dialogue_length=len(dialogue),
        )

        return dialogue

    async def _generate_chat_turn(
        self,
        *,
        speaker: Any,
        listener: Any,
        relationship_desc: str,
        rel_strength: int,
        topic_hint: str,
        state: dict[str, Any],
        world: dict[str, Any],
        transcript: str,
        shared_memory_block: str = "",
    ) -> str | None:
        """生成多轮对话中的单句台词；解析失败返回 None，渲染/LLM 异常向上抛"""
        if transcript:
            # 只喂末尾窗口：每轮都重发全量记录会让 token 成本随轮数平方增长
            transcript_block = (
                f"目前为止的对话记录（仅保留最近部分）：\n{_clip_tail(transcript, _CHAT_TRANSCRIPT_MAX_CHARS)}"
            )
        else:
            transcript_block = "这是对话的开场，请由你先开口。"

        # 不暴露工程概念，用自然语言描述场景
        prompt = self.prompts.render(
            "chat_turn",
            location=state.get("location", "某处"),
            world_time=world.get("world_time", "未知"),
            mood=state.get("mood", "calm"),
            speaker_name=speaker.name,
            speaker_personality=_personality_text(speaker),
            listener_name=listener.name,
            relationship=relationship_desc,
            strength=int(rel_strength),
            topic_hint=topic_hint,
            transcript_block=transcript_block,
            shared_memory_block=shared_memory_block,
        )
        raw = await self.llm.chat(prompt, model="chat", system_prompt=self.prompts.render("safety"))
        return _parse_chat_line(raw)

    async def _assess_chat_delta(
        self,
        *,
        char_a_name: str,
        char_b_name: str,
        relationship_type: str,
        strength: int,
        transcript: str,
    ) -> int | None:
        """LLM 结构化评估一次对话对双方关系强度的增量；任何失败返回 None（调用方回退固定值）"""
        schema = {
            "type": "object",
            "properties": {
                "delta": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["delta"],
        }
        try:
            prompt = self.prompts.render(
                "chat_quality",
                char_a_name=char_a_name,
                char_b_name=char_b_name,
                relationship_type=relationship_type,
                strength=int(strength),
                transcript=_clip_tail(transcript, _CHAT_TRANSCRIPT_MAX_CHARS),
            )
            # R4-L3：关系评审与对话生成解耦，避免「自己给自己打分」的同源偏差；
            # 档位体系收敛后仅剩 chat 档（原 MODEL_STRONG 已移除），同源偏差
            # 由 delta 钳制（_CHAT_QUALITY_DELTA_LIMIT）兜底
            result = await self.llm.structured_output(prompt, schema)
            # 缺键直接 KeyError → 由降级路径捕获回退固定值
            return max(-_CHAT_QUALITY_DELTA_LIMIT, min(_CHAT_QUALITY_DELTA_LIMIT, int(result["delta"])))
        except Exception as e:
            logger.warning("chat_quality_assessment_failed", error=str(e))
            return None

    async def _propagate_gossip(self, character_id: UUID, session_factory: Any | None = None) -> None:
        """群体动力学·传闻传播 - 好友的显著经历以第二手记忆扩散

        独立 db session：传闻写入与主 Tick 事务解耦，失败仅告警。
        内容取自源记忆原文模板拼接（非 LLM 编造），importance 减半递减；
        每好友每窗口最多一条，经既有检索管线回流后续决策。
        """
        from src.memory.gossip_service import GossipService

        factory = session_factory or db.session
        async with factory() as session:
            mem_repo = MemoryRepository(session)
            episode_service = EpisodeService(self.llm, mem_repo, prompts=self.prompts)
            gossip = GossipService(session, episode_service)
            await gossip.propagate_from_friends(character_id)

    async def _handle_group_activity(
        self,
        character_id: UUID,
        decision: DecisionResult,
        context: dict[str, Any],
        session_factory: Any | None = None,
    ) -> str | None:
        """群体动力学·群活动 - 同场景 >=3 人临时小聚

        单次 LLM 调用生成集体叙事（与 chat_with 同哲学：一次往返保证连贯），
        为每个参与者写共同经历记忆（related_characters 互指）并两两关系 +2。
        失败返回 None，调用方降级为 wait。

        Returns:
            集体叙事文本；失败 None
        """
        nearby = context.get("nearby_characters") or []
        if len(nearby) < 2:
            return None
        character = context["character"]
        location = str(context["state"].get("location") or "未知")
        participants = [{"id": str(character_id), "name": character.name}] + [
            {"id": n["id"], "name": n["name"]} for n in nearby[: settings.group_activity_participant_max - 1]
        ]
        names_text = "、".join(p["name"] for p in participants)

        narrative: str | None = None
        try:
            prompt = self.prompts.render("group_activity", scene=location, participants_text=names_text)
            response = await self.llm.chat(prompt, model="chat")
            narrative = parse_group_narrative(response)
        except Exception as e:
            logger.warning("group_activity_llm_failed", character_id=str(character_id), error=str(e))
        if not narrative:
            # LLM 不可用/解析失败时退化为模板叙事——聚会照常发生，只是没有文采
            narrative = f"{names_text}在{location}不期而遇，闲聊近况后各自散去。"

        factory = session_factory or db.session
        async with factory() as session:
            episode_service = EpisodeService(self.llm, MemoryRepository(session), prompts=self.prompts)
            service = GroupActivityService(session, episode_service)
            await service.persist(
                initiator_id=character_id,
                participants=participants,
                location=location,
                narrative=narrative,
                redis=self.redis,
            )

        logger.info(
            "group_activity_completed",
            character_id=str(character_id),
            participants=len(participants),
            location=location,
        )
        return f"{names_text}在{location}聚会：{narrative}"

    async def _maybe_proactive_share(
        self, character_id: UUID, decision: DecisionResult, context: dict[str, Any]
    ) -> None:
        """主动分享 - 委托装配层注册的分享处理器

        core 层不直接依赖 messaging：main.py lifespan 将
        messaging.proactive_sharing.run_tick_proactive_share 注册到 runtime，
        本方法仅经回调触发；分享失败不影响 Tick 主流程（调用方统一捕获）。
        """
        handler = get_proactive_share_handler()
        if handler is None:
            logger.debug("proactive_share_handler_not_registered", character_id=str(character_id))
            return
        await handler(character_id)
