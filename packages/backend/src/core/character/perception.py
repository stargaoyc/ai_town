"""Character Tick 感知组装 - PerceptionMixin

从 tick.py 机械抽取的感知阶段（R5-L14 行为保持重构）：
_perceive 及其专属装配助手。锁/信号量/事务等引擎机制仍在 tick.py 的
CharacterTickEngine；本模块只承载感知装配，不含任何状态写入路径。

共享常量的单一真相源：_DECISION_ITEM_MAX_CHARS、_parse_world_hour、
_world_hour 在此定义，由 tick.py 导入复用。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from structlog import get_logger

from src.core.state_codec import decode_state_value
from src.db.models import Reflection
from src.db.repositories import (
    CharacterRepository,
    DiaryRepository,
    MemoryRepository,
    PlanRepository,
    ReflectionRepository,
    RelationRepository,
    WorldEventRepository,
)
from src.db.session import db
from src.llm import LLMClient
from src.memory import RetrievalService
from src.memory.person_memory_service import PersonMemoryService
from src.observability.tracing import trace_span

logger = get_logger(__name__)


# 决策 Prompt 中单条反思/记忆的截断上限。二者均为 LLM 生成文本、长度无上界，
# 不截断会撑爆决策上下文预算（round-3 review M7）
_DECISION_ITEM_MAX_CHARS = 500


def _parse_world_hour(raw: str | None) -> int | None:
    """解析虚拟小时：兼容 ISO datetime 与纯 "HH:MM" 两种格式；失败返回 None"""
    if not raw:
        return None
    text = str(raw).strip()
    try:
        return datetime.fromisoformat(text).hour
    except (ValueError, TypeError):
        pass
    time_part = text.replace("T", " ").split()[-1]
    try:
        return int(time_part.split(":")[0])
    except (IndexError, ValueError):
        return None


def _world_hour(world: dict[str, Any]) -> int:
    """从世界状态解析虚拟小时；解析失败回退现实小时"""
    hour = _parse_world_hour(str(world.get("world_time") or ""))
    return hour if hour is not None else datetime.now(UTC).hour


class PerceptionMixin:
    """感知环境 Mixin。

    契约：不定义 __init__，依赖宿主引擎初始化的属性：
    - redis：Redis 客户端（实时角色/世界状态读取）
    - llm：LLM 客户端（记忆检索 embedding）
    """

    redis: Redis
    llm: LLMClient

    @trace_span("character.perceive")
    async def _perceive(self, character_id: UUID) -> dict[str, Any]:
        """感知环境 - 读取角色状态、世界状态、记忆、同场景其他角色

        Args:
            character_id: 角色 ID

        Returns:
            dict: {
                "character": Character,        # 角色档案
                "state": dict,                 # 角色状态（Redis 缓存优先）
                "world": dict,                 # 世界状态
                "memories": list[dict],        # 相关记忆
                "plans": list[Plan],           # 当前计划
                "nearby_characters": list[dict],  # 同场景其他角色（用于多智能体交互）
            }
        """
        # 从数据库获取角色档案和状态
        async with db.session() as session:
            char_repo = CharacterRepository(session)
            result = await char_repo.get_character_with_state(character_id)
            if result is None:
                raise ValueError(f"角色不存在: {character_id}")

            character, char_state = result

            plan_repo = PlanRepository(session)
            plans = await plan_repo.get_active_plans(character_id)

        # 从 Redis 读取实时状态（缓存优先）
        redis_state = await self.redis.hgetall(f"char:{character_id}:state")
        state: dict[str, Any] = (
            {str(k): decode_state_value(str(k), v) for k, v in redis_state.items()}
            if redis_state
            else {
                "location": char_state.location,
                "stamina": char_state.stamina,
                "satiety": char_state.satiety,
                "mood": char_state.mood,
                "money": char_state.money,
                "phone_battery": char_state.phone_battery,
                "social_energy": char_state.social_energy,
                "inventory": char_state.inventory,
            }
        )

        # 补齐 Redis 缺失的数值字段与 mood（新导入角色 Redis 可能未初始化完整）
        _NUMERIC_KEYS = {"stamina", "satiety", "money", "phone_battery", "social_energy"}
        for key in _NUMERIC_KEYS:
            if key not in state and char_state:
                state[key] = getattr(char_state, key, 50)
        if "mood" not in state and char_state:
            state["mood"] = getattr(char_state, "mood", "calm")

        # 从 Redis 读取世界状态（hgetall 返回值可能含 bytes，统一解码为 str）
        world_state = await self.redis.hgetall("world:state")
        world: dict[str, Any] = {
            str(k): v.decode() if isinstance(v, bytes) else v for k, v in (world_state or {}).items()
        }

        # 检索相关记忆（需要 db session 创建 RetrievalService）
        # embedding 失败时降级为空记忆列表，不阻断 Tick
        # 检索 query 动态化：拼入时段/情绪/计划标题，提升向量区分度（审查 §五-P1）
        # 时段用虚拟小时：角色活在虚拟时间里，与现实小时混用会造成时间语义错乱
        hour_now = _world_hour(world)
        time_band = "凌晨" if hour_now < 6 else "上午" if hour_now < 12 else "下午" if hour_now < 18 else "晚上"
        plan_titles = "、".join(p.title for p in plans[:3]) if plans else "无"
        query = (
            f"{character.name}在{state.get('location')}，{time_band}，"
            f"情绪{state.get('mood', '平静')}，计划：{plan_titles}，最近的经历与相关往事"
        )
        memories = []
        query_vec: list[float] = []
        try:
            async with db.session() as session:
                mem_repo = MemoryRepository(session)
                retrieval_service = RetrievalService(self.llm, mem_repo)
                # 单次 embed 复用：同一查询向量同时供记忆与反思语义检索
                query_vec = await self.llm.embed(query)
                memories = await retrieval_service.search_with_vec(character_id, query_vec, top_k=10)
        except Exception as e:
            logger.warning(
                "memory_retrieval_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        # 检索近期反思（高层认知）注入决策——认知产物必须回流上下文（审查 §五-P0）
        # 反思/日记/传闻/关系/同场景角色均为纯读且相邻，合并为单会话
        # （审查 §三坏味道 #5：此前每类数据独立开 session，往返开销随角色数放大）
        reflections_text = "暂无高层认知"
        diary_text = "暂无日记"
        gossips_text = "暂无听说的消息"
        relations_map: dict[str, int] = {}
        relation_types: dict[str, str] = {}
        nearby_characters: list[dict[str, Any]] = []
        current_location = state.get("location")
        async with db.session() as session:
            # 近期世界动态（事件中断重规划的感知面，审查 §4.3）：
            # 天气/资源/节日类变化注入决策，提示 LLM 可用 planChanges 顺应调整
            world_events_text = "暂无近期世界动态"
            try:
                current_tick = int(str(world.get("tick_id", "0")) or 0)
                if current_tick > 0:
                    notable = await WorldEventRepository(session).get_recent_notable(current_tick)
                    if notable:
                        world_events_text = "\n".join(
                            f"- [Tick {e.tick_id}] {e.event_type}: {str(e.payload)[:120]}" for e in notable
                        )
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "world_events_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            try:
                # 语义优先：按当前情境召回相关反思（embedding 由反思保存时生成）；
                # 不足 limit 或向量缺失（历史行/生成失败降级）时以最近反思补齐
                refs: list[Reflection] = []
                if query_vec:
                    refs = await ReflectionRepository(session).search_semantic(character_id, query_vec, limit=5)
                if len(refs) < 5:
                    seen_ids = {r.id for r in refs}
                    recent = await ReflectionRepository(session).get_by_character(character_id, limit=5)
                    refs.extend(r for r in recent if r.id not in seen_ids)
                refs = refs[:5]
                if refs:
                    reflections_text = "\n".join(f"- {r.content[:_DECISION_ITEM_MAX_CHARS]}" for r in refs)
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "reflections_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            # 检索最近一篇日报注入决策——角色带着"今天经历过什么"的叙事做决策（审查 §五-P0）
            try:
                latest_diary = await DiaryRepository(session).get_latest(character_id, period="day")
                if latest_diary and latest_diary.content:
                    diary_text = latest_diary.content[:300]
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "diary_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            # 检索最近听说的传闻（群体动力学 B4：作为社交话题提示注入，
            # 让角色在 chat_with 中自然提起听来的消息，而非只沉默存档）
            try:
                gossips = await MemoryRepository(session).fetch_recent_gossip(character_id, hours=24, limit=2)
                if gossips:
                    gossips_text = "\n".join(f"- {g}" for g in gossips)
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "gossip_context_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            # 加载角色全部出向关系（一次查询，供工具注入与同场景角色感知复用）
            try:
                rels = await RelationRepository(session).get_relations(character_id)
                relations_map = {str(r.target_id): r.strength for r in rels}
                relation_types = {str(r.target_id): r.relationship_type for r in rels}
            except Exception as e:
                await session.rollback()
                logger.warning(
                    "relations_load_failed_continue",
                    character_id=str(character_id),
                    error=str(e),
                )

            # 感知同场景其他角色（多智能体交互关键）
            # 提供角色名、性格、当前动作、关系强度，供 LLM 决策是否发起社交
            if current_location:
                try:
                    others = await CharacterRepository(session).get_characters_by_location(
                        location=current_location,
                        exclude_id=character_id,
                    )

                    # 关系信息直接复用上方一次性加载的出向关系，避免逐角色查询（N+1）
                    for other_char, other_state in others:
                        oid = str(other_char.id)
                        rel_type = relation_types.get(oid, "stranger")
                        rel_strength = relations_map.get(oid, 0)

                        personality = (other_char.traits or {}).get("personality", [])
                        if isinstance(personality, list):
                            personality_text = "、".join(personality)
                        else:
                            personality_text = str(personality)

                        nearby_characters.append(
                            {
                                "id": oid,
                                "name": other_char.name,
                                "personality": personality_text,
                                "mood": other_state.mood,
                                "relationship_type": rel_type,
                                "strength": rel_strength,
                                "current_action": (other_state.current_action or {}).get("action_name")
                                if other_state.current_action
                                else None,
                            }
                        )
                except Exception as e:
                    await session.rollback()
                    logger.warning(
                        "nearby_characters_query_failed_continue",
                        character_id=str(character_id),
                        location=current_location,
                        error=str(e),
                    )

        # 用户记忆摘要（按热度 top-N）：让陪伴关系影响镇内决策（审查 §4.4 断层）
        # 失败降级为占位文本，不阻断 Tick
        known_users_text = "（暂无认识的用户）"
        try:
            pm_service = PersonMemoryService(session_factory=db.session)
            known_users_text = await pm_service.get_top_users_context(character_id) or "（暂无认识的用户）"
        except Exception as e:
            logger.warning(
                "known_users_query_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        return {
            "character": character,
            "state": state,
            "world": world,
            "memories": memories,
            "reflections": reflections_text,
            "diary": diary_text,
            "recent_gossip": gossips_text,
            "world_events": world_events_text,
            "plans": plans,
            "nearby_characters": nearby_characters,
            "relations": relations_map,
            "known_users": known_users_text,
        }
