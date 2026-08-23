"""Character Tick - 角色行为决策与执行闭环

五阶段流程：
1. 感知环境：读取角色状态、世界状态、记忆
2. 候选过滤：ActionRegistry.get_candidates(state)
3. LLM 决策：结构化输出 DecisionResult
4. 执行 Action：事务化执行，更新状态
5. 记忆沉淀：写入 MemoryEpisode + 反思检查

并发控制：
- 使用 asyncio.Semaphore 限制并发 Tick 数量
- 使用 Redis 分布式锁避免同一角色重复 Tick
"""

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from structlog import get_logger

from src.actions import Action, ActionRegistry, DecisionResult
from src.config import settings
from src.core.locks import lock_watchdog, release_lock
from src.core.state_codec import decode_state_value, encode_state_mapping
from src.cost_control import CircuitOpen
from src.db.models import ActionRecord, Character
from src.db.repositories import (
    ActionRepository,
    CharacterRepository,
    ConversationRepository,
    MemoryRepository,
    PlanRepository,
    ReflectionRepository,
)
from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.memory import EpisodeService, ReflectionService, RetrievalService

logger = get_logger(__name__)


class CharacterTickEngine:
    """角色 Tick 引擎 - 管理所有角色的行为闭环"""

    SEMAPHORE: asyncio.Semaphore | None = None  # 并发控制信号量
    SEMAPHORE_LIMIT: int = 0  # 当前信号量容量（用于检测 character_max_concurrent 热更新）
    LOCK_PREFIX = "char:tick:lock:"  # 角色锁前缀
    LOCK_TTL = 30  # 锁 TTL（秒）

    def __init__(
        self,
        redis: Redis,
        registry: ActionRegistry,
        llm: LLMClient,
        prompts: PromptTemplates,
    ):
        """初始化 Tick 引擎

        Args:
            redis: Redis 客户端（用于分布式锁和状态缓存）
            registry: Action 注册表
            llm: LLM 客户端
            prompts: Prompt 模板管理器
        """
        self.redis = redis
        self.registry = registry
        self.llm = llm
        self.prompts = prompts

        self._ensure_semaphore()

    async def tick_character(self, character_id: UUID) -> None:
        """执行单个角色的 Tick

        流程：
        1. 获取分布式锁（避免重复执行）
        2. 并发信号量控制
        3. 五阶段闭环
        4. 释放锁

        Args:
            character_id: 角色 ID
        """
        lock_key = f"{self.LOCK_PREFIX}{character_id}"
        lock_token = uuid4().hex

        # 尝试获取锁（写入唯一 token，释放时 compare-and-delete 防止误删他人锁）
        acquired = await self.redis.set(lock_key, lock_token, ex=self.LOCK_TTL, nx=True)
        if not acquired:
            logger.debug("character_tick_skipped", character_id=str(character_id))
            return

        # 看门狗：单次 Tick 含多次 LLM 调用，耗时可能超过 LOCK_TTL，定期续租防止锁过期易主
        renew_stop = asyncio.Event()
        watchdog = asyncio.create_task(lock_watchdog(self.redis, renew_stop, {lock_key: lock_token}, self.LOCK_TTL))

        try:
            self._ensure_semaphore()
            semaphore = CharacterTickEngine.SEMAPHORE
            assert semaphore is not None
            async with semaphore:
                await self._execute_tick(character_id)
        except CircuitOpen:
            # 熔断器开启时 LLM 调用必然失败，重试无意义，跳过本角色本周期
            logger.warning("character_tick_skipped_circuit_open", character_id=str(character_id))
        finally:
            renew_stop.set()
            with suppress(asyncio.CancelledError):
                await watchdog
            try:
                await release_lock(self.redis, lock_key, lock_token)
            except Exception:
                logger.warning("character_tick_lock_release_failed", character_id=str(character_id))

    @classmethod
    def _ensure_semaphore(cls) -> None:
        """确保信号量容量与 character_max_concurrent 配置一致（热更新生效点）

        重建瞬间旧信号量仍被持有中的协程引用，短暂双信号量无害：
        旧引用随协程结束释放，新请求全部走新信号量。
        """
        if cls.SEMAPHORE is None or cls.SEMAPHORE_LIMIT != settings.character_max_concurrent:
            cls.SEMAPHORE = asyncio.Semaphore(settings.character_max_concurrent)
            cls.SEMAPHORE_LIMIT = settings.character_max_concurrent
            logger.info(
                "character_tick_semaphore_rebuilt",
                max_concurrent=settings.character_max_concurrent,
            )

    async def _execute_tick(self, character_id: UUID) -> None:
        """五阶段闭环核心逻辑

        Args:
            character_id: 角色 ID
        """
        logger.info("character_tick_start", character_id=str(character_id))

        start_perf = time.perf_counter()
        cid = str(character_id)

        # 1. 感知环境
        context = await self._perceive(character_id)

        # 2. 候选过滤
        candidates = self.registry.get_candidates(context["state"], scene=context["state"].get("location"))

        if not candidates:
            logger.warn("no_candidates", character_id=str(character_id))
            return

        # 3. LLM 决策（ReAct 循环：工具调用 → 观察结果 → 再次决策）
        # 最多 3 轮工具调用，防止无限循环
        tool_observations: list[dict[str, Any]] = []
        decision = await self._decide(character_id, context, candidates, tool_observations)

        for _react_iter in range(3):
            if decision.action != "use_tool":
                break

            # 执行工具调用
            tool_result = await self._execute_tool(character_id, decision, context)
            if tool_result:
                tool_observations.append(
                    {
                        "tool_name": decision.params.get("tool_name", ""),
                        "tool_args": decision.params.get("tool_args", {}),
                        "result": tool_result.get("result", tool_result),
                        "success": tool_result.get("success", False),
                    }
                )

            # 对状态变更类工具，应用 deltas
            if tool_result and tool_result.get("state_mutating"):
                await self._apply_tool_deltas(character_id, tool_result.get("result", {}), context)

            # 再次决策（带工具观察结果）
            decision = await self._decide(character_id, context, candidates, tool_observations)

        # 如果 3 轮后仍在 use_tool，强制改为 wait
        if decision.action == "use_tool":
            logger.warning(
                "react_max_iterations_reached",
                character_id=str(character_id),
                tool_observations=tool_observations,
            )
            decision.action = "wait"

        # 4. 执行 Action
        await self._execute_action(character_id, decision, context)

        # 5. 记忆沉淀
        await self._memorize(character_id, decision, context)

        # 6. 主动分享（若 LLM 决策产生分享意图）
        if decision.proactive_share_intent:
            try:
                await self._maybe_proactive_share(character_id, decision, context)
            except Exception as e:
                # 分享失败不影响 Tick 主流程
                logger.warning(
                    "proactive_share_tick_failed",
                    character_id=str(character_id),
                    error=str(e),
                    exc_info=True,
                )

        from src.observability.metrics import (
            CHARACTER_TICK_DURATION,
            CHARACTER_TICK_TOTAL,
        )

        tick_elapsed = time.perf_counter() - start_perf
        CHARACTER_TICK_DURATION.observe(tick_elapsed)
        CHARACTER_TICK_TOTAL.labels(character_id=cid).inc()

        from src.observability.langfuse_tracing import trace_character_tick

        trace_character_tick(
            character_id=str(character_id),
            action=decision.action,
            duration_ms=int(tick_elapsed * 1000),
        )

        logger.info(
            "character_tick_end",
            character_id=str(character_id),
            action=decision.action,
        )

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
        query = f"角色{character.name}当前在{state.get('location')}，最近在做什么"
        memories = []
        try:
            async with db.session() as session:
                mem_repo = MemoryRepository(session)
                retrieval_service = RetrievalService(self.llm, mem_repo)
                memories = await retrieval_service.search(character_id, query, top_k=10)
        except Exception as e:
            logger.warning(
                "memory_retrieval_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        # 加载角色全部关系映射（target_id -> strength），供工具调用注入 current_relation_strength
        relations_map: dict[str, int] = {}
        try:
            async with db.session() as session:
                from src.db.repositories import RelationRepository

                rel_repo = RelationRepository(session)
                rels = await rel_repo.get_relations(character_id)
                relations_map = {str(r.target_id): r.strength for r in rels}
        except Exception as e:
            logger.warning(
                "relations_load_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        # 感知同场景其他角色（多智能体交互关键）
        # 提供角色名、性格、当前动作、关系强度，供 LLM 决策是否发起社交
        nearby_characters: list[dict[str, Any]] = []
        current_location = state.get("location")
        if current_location:
            try:
                async with db.session() as session:
                    char_repo = CharacterRepository(session)
                    others = await char_repo.get_characters_by_location(
                        location=current_location,
                        exclude_id=character_id,
                    )

                # 查询关系（批量读取，避免 N+1）
                from src.modules.relation.graph import RelationGraph

                for other_char, other_state in others:
                    # 关系查询使用独立 session（RelationGraph 内部走 repo）
                    rel_snapshot = None
                    try:
                        async with db.session() as rel_session:
                            graph = RelationGraph(rel_session, self.redis)
                            rel_snapshot = await graph.get_relation(character_id, other_char.id)
                    except Exception as rel_err:
                        logger.debug(
                            "relation_query_failed_continue",
                            character_id=str(character_id),
                            target_id=str(other_char.id),
                            error=str(rel_err),
                        )

                    personality = (other_char.traits or {}).get("personality", [])
                    if isinstance(personality, list):
                        personality_text = "、".join(personality)
                    else:
                        personality_text = str(personality)

                    nearby_characters.append(
                        {
                            "id": str(other_char.id),
                            "name": other_char.name,
                            "personality": personality_text,
                            "mood": other_state.mood,
                            "relationship_type": rel_snapshot.relationship_type if rel_snapshot else "stranger",
                            "strength": rel_snapshot.strength if rel_snapshot else 0,
                            "current_action": (other_state.current_action or {}).get("action_name")
                            if other_state.current_action
                            else None,
                        }
                    )
            except Exception as e:
                logger.warning(
                    "nearby_characters_query_failed_continue",
                    character_id=str(character_id),
                    location=current_location,
                    error=str(e),
                )

        return {
            "character": character,
            "state": state,
            "world": world,
            "memories": memories,
            "plans": plans,
            "nearby_characters": nearby_characters,
            "relations": relations_map,
        }

    async def _decide(
        self,
        character_id: UUID,
        context: dict[str, Any],
        candidates: list[Action],
        tool_observations: list[dict[str, Any]] | None = None,
    ) -> DecisionResult:
        """LLM 决策 - 结构化输出（ReAct 模式）

        使用 PromptTemplates.render() 生成决策 Prompt
        调用 LLMClient.structured_output() 获取结构化结果

        ReAct 循环：当 LLM 决策为 use_tool 时，执行工具后将结果加入 tool_observations，
        再次调用本方法让 LLM 基于工具结果推理下一步行动。

        Args:
            character_id: 角色 ID
            context: 感知环境结果
            candidates: 候选 Action 列表
            tool_observations: 前序工具调用的观察结果（ReAct 模式）

        Returns:
            DecisionResult: 决策结果
        """
        character = context["character"]
        state = context["state"]
        world = context["world"]

        # 构建候选 Action 列表文本
        candidates_text = "\n".join(
            [f"- {a.id}: {a.name}（耗时{a.duration_minutes}分钟，体力消耗{a.energy_cost}）" for a in candidates]
        )

        # 构建工具列表文本（角色可调用本地工具获取信息或执行操作）
        try:
            from src.tools import ToolRegistry

            tool_registry = ToolRegistry()
            tools_text = await tool_registry.format_tools_for_prompt()
        except Exception:
            tools_text = "（工具不可用）"

        # 构建记忆文本
        memories_text = (
            "\n".join([m.get("content", str(m)) if isinstance(m, dict) else str(m) for m in context["memories"]])
            if context["memories"]
            else "暂无相关记忆"
        )

        # 构建计划文本
        plans_text = (
            "\n".join([f"- {p.title}（进度{p.progress}%）" for p in context["plans"]])
            if context["plans"]
            else "暂无计划"
        )

        # 构建同场景其他角色文本（多智能体交互核心）
        # 让 LLM 知道谁在身边、性格如何、关系如何，决策是否发起 chat_with
        nearby = context.get("nearby_characters") or []
        if nearby:
            nearby_lines = []
            for n in nearby:
                action_desc = f"，正在{n['current_action']}" if n.get("current_action") else ""
                nearby_lines.append(
                    f"- {n['name']}（ID: {n['id']}）| 性格: {n['personality']} | "
                    f"关系: {n['relationship_type']}（强度 {n['strength']}）| "
                    f"情绪: {n.get('mood') or '未知'}{action_desc}"
                )
            nearby_text = "\n".join(nearby_lines)
        else:
            nearby_text = "（当前场景没有其他角色）"

        # 渲染决策 Prompt
        prompt = self.prompts.render(
            "decision",
            name=character.name,
            personality=", ".join(character.traits.get("personality", [])) or "无",
            backstory=character.backstory or "无",
            location=state.get("location", "未知"),
            energy=state.get("stamina", 50),
            hunger=state.get("satiety", 50),
            mood=state.get("mood", "平静"),
            world_time=world.get("world_time", datetime.now(UTC).isoformat()),
            weather=world.get("weather", "sunny"),
            scenes="",  # 简化
            memories=memories_text,
            plans=plans_text,
            candidates=candidates_text,
            nearby_characters=nearby_text,
        )

        # 追加工具信息到 Prompt
        prompt += self.prompts.render("decision_tools", tools_text=tools_text)

        # ReAct 模式：如果有前序工具调用结果，加入 Prompt 让 LLM 基于结果推理
        if tool_observations:
            obs_lines = []
            for i, obs in enumerate(tool_observations, 1):
                success_tag = "成功" if obs.get("success") else "失败"
                result_str = str(obs.get("result", ""))[:800]
                obs_lines.append(
                    f"{i}. 调用 {obs['tool_name']}({obs.get('tool_args', {})}) [{success_tag}]\n   结果: {result_str}"
                )
            prompt += self.prompts.render("decision_react", observations=chr(10).join(obs_lines))

        # 定义决策结果 schema
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "reason": {"type": "string"},
                "params": {"type": "object"},
                "duration": {"type": "integer"},
                "planChanges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "planId": {"type": "string"},
                            "action": {"type": "string"},  # update/complete/abandon
                            "progress": {"type": "integer"},
                        },
                    },
                },
                "proactiveShareIntent": {"type": "boolean"},
            },
            "required": ["action", "reason"],
        }

        # 调用 LLM
        result = await self.llm.structured_output(prompt, schema, model="chat")

        # 验证 Action ID 合法性
        action_id = result.get("action", "wait")
        valid_action_ids = [a.id for a in candidates]
        if action_id not in valid_action_ids:
            logger.warn("invalid_action", action=action_id, fallback="wait")
            action_id = "wait" if "wait" in valid_action_ids else valid_action_ids[0]

        # 防御性处理 LLM 返回值类型
        # 注意：LLM 可能返回 "planChanges": null，此时 dict.get() 返回 None 而非默认值 []
        raw_plan_changes = result.get("planChanges") or []
        plan_changes = [pc if isinstance(pc, dict) else {"description": str(pc)} for pc in raw_plan_changes]

        raw_share_intent = result.get("proactiveShareIntent", False)
        proactive_share_intent = bool(raw_share_intent) if raw_share_intent is not None else False

        return DecisionResult(
            action=action_id,
            reason=result.get("reason", ""),
            params=result.get("params") or {},
            duration=result.get("duration"),
            plan_changes=plan_changes,
            proactive_share_intent=proactive_share_intent,
        )

    async def _execute_tool(
        self, character_id: UUID, decision: DecisionResult, context: dict[str, Any]
    ) -> dict[str, Any] | None:
        """执行工具调用

        当 LLM 决定使用工具时，通过 ToolRegistry 直接调用本地 async 函数，
        将工具结果存入角色记忆，并对状态变更类工具应用 deltas 到角色状态。

        Args:
            character_id: 角色 ID
            decision: 决策结果（params 中包含 tool_name 和 tool_args）
            context: 感知环境结果（含 state、relations）

        Returns:
            工具返回结果字典，失败时返回 None
        """
        from src.tools import ToolRegistry

        tool_name = decision.params.get("tool_name", "")
        tool_args = decision.params.get("tool_args", {})

        if not tool_name:
            logger.warning("tool_call_no_tool_name", character_id=str(character_id))
            return None

        character = context["character"]
        logger.info(
            "tool_call_start",
            character_id=str(character_id),
            character_name=character.name,
            tool_name=tool_name,
            tool_args=tool_args,
        )

        # 构建工具上下文：character_id + state + relations（供注入参数）
        tool_context = {
            "character_id": str(character_id),
            "state": context["state"],
            "relations": context.get("relations", {}),
        }

        registry = ToolRegistry()
        result = await registry.call_tool_with_context(tool_name, tool_args, tool_context)

        if result.get("success"):
            tool_result = result.get("result", {})
            logger.info(
                "tool_call_success",
                character_id=str(character_id),
                tool_name=tool_name,
                result_preview=str(tool_result)[:200],
            )

            # 状态变更类工具的 deltas 由 ReAct 循环统一应用（避免重复）
            # 将工具结果存入角色记忆
            try:
                async with db.session() as session:
                    mem_repo = MemoryRepository(session)
                    episode_service = EpisodeService(self.llm, mem_repo, prompts=self.prompts)
                    await episode_service.create_episode(
                        character_id,
                        f"[工具调用] {tool_name}({tool_args}) → {str(tool_result)[:500]}",
                        action_id="use_tool",
                        location=context["state"].get("location"),
                        importance=7,
                        character_name=character.name,
                        reason=f"使用工具 {tool_name}",
                        mood=context["state"].get("mood"),
                    )
                    await session.commit()
            except Exception as e:
                logger.warning(
                    "tool_memory_save_failed",
                    character_id=str(character_id),
                    error=str(e),
                )
        else:
            logger.warning(
                "tool_call_failed",
                character_id=str(character_id),
                tool_name=tool_name,
                error=result.get("error"),
            )

        return result

    async def _apply_tool_deltas(
        self,
        character_id: UUID,
        tool_result: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        """将工具返回的状态 deltas 应用到角色内存状态

        P0-1：工具 delta 不再直接写 Redis，仅更新内存 state，
        由 _execute_action 在 PG 事务提交后统一写 Redis，
        避免工具变更绕过 PG 事务导致镜像不一致。

        支持的 delta 字段：
        - money_delta: 金钱变化（正=收入，负=支出）
        - inventory_delta: {item_id: quantity_change}（正=增加，负=减少）
        - relation_strength_delta: 好感度变化（需配合 target_id）
        - mood_delta: 情绪变化（字符串，如 "happy"）

        Args:
            character_id: 角色 ID
            tool_result: 工具返回的结果字典
            context: 感知环境结果
        """
        state = context["state"]

        # 金钱变化
        money_delta = tool_result.get("money_delta")
        if money_delta and isinstance(money_delta, int | float):
            current_money = int(state.get("money", 0) or 0)
            new_money = max(0, current_money + int(money_delta))
            state["money"] = new_money
            logger.info(
                "tool_delta_money",
                character_id=str(character_id),
                delta=money_delta,
                new_money=new_money,
            )

        # 库存变化
        inventory_delta = tool_result.get("inventory_delta")
        if inventory_delta and isinstance(inventory_delta, dict):
            current_inventory: dict[str, int] = state.get("inventory") or {}
            for item_id, qty_change in inventory_delta.items():
                current_qty = int(current_inventory.get(item_id, 0) or 0)
                new_qty = max(0, current_qty + int(qty_change))
                if new_qty > 0:
                    current_inventory[item_id] = new_qty
                elif item_id in current_inventory:
                    del current_inventory[item_id]
            state["inventory"] = current_inventory
            logger.info(
                "tool_delta_inventory",
                character_id=str(character_id),
                delta=inventory_delta,
                new_inventory=current_inventory,
            )

        # 情绪变化
        mood_delta = tool_result.get("mood_delta")
        if mood_delta and isinstance(mood_delta, str):
            state["mood"] = mood_delta
            logger.info(
                "tool_delta_mood",
                character_id=str(character_id),
                new_mood=mood_delta,
            )

        # 关系强度变化（需写入 PG relations 表）
        relation_delta = tool_result.get("relation_strength_delta")
        target_id = tool_result.get("target_id")
        if relation_delta and target_id:
            try:
                async with db.session() as session:
                    from src.db.repositories import RelationRepository

                    rel_repo = RelationRepository(session)
                    rel = await rel_repo.get_or_create(character_id, UUID(target_id))
                    new_strength = max(0, min(100, rel.strength + int(relation_delta)))
                    await rel_repo.update_relation(
                        character_id,
                        UUID(target_id),
                        strength=new_strength,
                    )
                    # 更新 context 中的关系映射
                    relations = context.get("relations", {})
                    relations[str(target_id)] = new_strength
                    logger.info(
                        "tool_delta_relation",
                        character_id=str(character_id),
                        target_id=str(target_id),
                        delta=relation_delta,
                        new_strength=new_strength,
                    )
            except Exception as e:
                logger.warning(
                    "tool_relation_update_failed",
                    character_id=str(character_id),
                    target_id=str(target_id),
                    error=str(e),
                )

    @staticmethod
    def _current_world_hour(context: dict[str, Any]) -> int | None:
        """从世界状态解析当前虚拟小时；解析失败返回 None（移动校验跳过开放时间检查）"""
        raw = context.get("world", {}).get("world_time")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw)).hour
        except (ValueError, TypeError):
            return None

    async def _execute_action(self, character_id: UUID, decision: DecisionResult, context: dict[str, Any]) -> None:
        """执行 Action - 事务化

        流程：
        1. 获取 Action 定义
        2. 计算状态变更
        3. 单一事务：写入 ActionRecord + 更新 PG 状态 + 写入 MemoryEpisode
        4. 更新 Redis 实时状态

        Args:
            character_id: 角色 ID
            decision: 决策结果
            context: 感知环境结果
        """
        start_perf = time.perf_counter()
        action_def = self.registry.get(decision.action)
        if not action_def:
            logger.error("action_not_found", action=decision.action)
            from src.observability.metrics import ACTION_EXECUTION_TOTAL

            ACTION_EXECUTION_TOTAL.labels(action_id=decision.action, status="failed").inc()
            return

        # 多智能体交互：chat_with 需要生成对话、更新关系、为双方写记忆
        # 在状态变更前执行，确保对话内容能写入 ActionRecord.result
        chat_dialogue: str | None = None
        if decision.action == "chat_with":
            chat_dialogue = await self._handle_character_chat(character_id, decision, context)
            # 失败时降级为 wait，不阻塞 Tick
            if chat_dialogue is None:
                logger.warning(
                    "chat_with_failed_fallback_to_wait",
                    character_id=str(character_id),
                )
                decision = decision.model_copy(update={"action": "wait", "params": {}})
                action_def = self.registry.get(decision.action)
                if action_def is None:
                    logger.error("fallback_wait_action_not_found", character_id=str(character_id))
                    from src.observability.metrics import ACTION_EXECUTION_TOTAL

                    ACTION_EXECUTION_TOTAL.labels(action_id="chat_with", status="failed").inc()
                    return

        # move 决策先经 MovementSystem 校验（目标存在且连通、场景开放），失败降级为 wait。
        # LLM 幻觉的不存在场景在此被拦截，不再直接写入位置
        move_total_minutes: int | None = None
        if decision.action == "move":
            from src.runtime import get_movement_system

            movement_system = get_movement_system()
            if movement_system is None:
                logger.warning(
                    "move_rejected_fallback_to_wait",
                    character_id=str(character_id),
                    reason="movement_system_not_initialized",
                )
                decision = decision.model_copy(update={"action": "wait", "params": {}})
                action_def = self.registry.get(decision.action)
                if action_def is None:
                    logger.error("fallback_wait_action_not_found", character_id=str(character_id))
                    from src.observability.metrics import ACTION_EXECUTION_TOTAL

                    ACTION_EXECUTION_TOTAL.labels(action_id="move", status="failed").inc()
                    return
            else:
                target = str(decision.params.get("target_scene") or "")
                current_location = str(context["state"].get("location") or "")
                move_result = await movement_system.calculate_move(
                    current_location,
                    target,
                    hour=self._current_world_hour(context),
                )
                if not move_result.success:
                    logger.warning(
                        "move_rejected_fallback_to_wait",
                        character_id=str(character_id),
                        from_scene=current_location,
                        to_scene=target,
                        reason=move_result.reason,
                    )
                    decision = decision.model_copy(update={"action": "wait", "params": {}})
                    action_def = self.registry.get(decision.action)
                    if action_def is None:
                        logger.error("fallback_wait_action_not_found", character_id=str(character_id))
                        from src.observability.metrics import ACTION_EXECUTION_TOTAL

                        ACTION_EXECUTION_TOTAL.labels(action_id="move", status="failed").inc()
                        return
                else:
                    move_total_minutes = move_result.total_minutes

        # 计算状态变更：
        # - move 使用移动矩阵的真实耗时
        # - LLM 动态时长仅在 Action 声明 allow_dynamic_duration 时生效，防止任意改时长
        if move_total_minutes is not None:
            duration = move_total_minutes
        elif action_def.allow_dynamic_duration and decision.duration:
            duration = decision.duration
        else:
            duration = action_def.duration_minutes
        new_state = context["state"].copy()

        # 应用资源变更（使用 apply_cost_fields 辅助函数）
        from src.actions.base import apply_cost_fields

        changes = apply_cost_fields(new_state, action_def)
        new_state.update(changes)

        # executor 计算动作特有状态变更（如 move 的位置更新），返回值优先覆盖默认成本字段
        if action_def.executor is not None:
            executor_changes = action_def.executor(new_state, decision.params)
            if executor_changes:
                new_state.update(executor_changes)

        # 被动恢复：仅在"休息类"动作下恢复社交能量（休息/睡觉/读书等独处活动）
        # phone_battery 仅通过 charge_phone 恢复（已在 action cost 中定义）
        # 避免资源永久为 0，同时不违反常识（读书不会给手机充电）
        _SOLO_RECOVERY_ACTIONS = {"relax", "sleep", "read_book"}
        if decision.action in _SOLO_RECOVERY_ACTIONS:
            cur_se = int(new_state.get("social_energy", 0) or 0)
            new_state["social_energy"] = min(100, cur_se + 10)

        # 设置当前动作（供前端展示"当前行为"）
        from datetime import timedelta

        action_end = datetime.now(UTC) + timedelta(minutes=duration)
        new_state["current_action"] = {
            "action_id": decision.action,
            "action_name": action_def.name,
            "params": decision.params,
            "reason": decision.reason,
            "end_time": action_end.isoformat(),
        }

        # 事务化执行
        try:
            async with db.session() as session:
                action_repo = ActionRepository(session)
                char_repo = CharacterRepository(session)

                # 写入行为记录
                # chat_with 时附带对话内容与对方角色 ID（供回放与关系溯源）
                related_ids: list[str] = []
                if decision.action == "chat_with":
                    target_id = decision.params.get("target_character_id")
                    if target_id:
                        related_ids = [str(target_id)]

                record = ActionRecord(
                    character_id=character_id,
                    action_id=action_def.id,
                    action_name=action_def.name,
                    params=decision.params,
                    reason=decision.reason,
                    result=chat_dialogue,
                    duration_minutes=duration,
                    location=new_state.get("location", "unknown"),
                    related_characters=related_ids,
                    timestamp=datetime.now(UTC),
                )
                await action_repo.add(record)

                # 更新 PG 状态（数值字段从 Redis 读取为 str，需转为 int）
                _INT_FIELDS = {"stamina", "satiety", "money", "phone_battery", "social_energy"}
                await char_repo.update_state(
                    character_id,
                    stamina=int(new_state["stamina"]) if new_state.get("stamina") is not None else None,
                    satiety=int(new_state["satiety"]) if new_state.get("satiety") is not None else None,
                    mood=new_state.get("mood"),
                    money=int(new_state["money"]) if new_state.get("money") is not None else None,
                    phone_battery=int(new_state["phone_battery"])
                    if new_state.get("phone_battery") is not None
                    else None,
                    social_energy=int(new_state["social_energy"])
                    if new_state.get("social_energy") is not None
                    else None,
                    location=new_state.get("location"),
                    current_action=new_state.get("current_action"),
                    inventory=new_state.get("inventory"),
                )

                # 写入状态历史快照（支持前端状态趋势图表）
                from src.db.models import CharacterStateHistory

                history = CharacterStateHistory(
                    character_id=character_id,
                    location=new_state.get("location"),
                    stamina=int(new_state.get("stamina", 0) or 0),
                    satiety=int(new_state.get("satiety", 0) or 0),
                    mood=new_state.get("mood"),
                    money=int(new_state.get("money", 0) or 0),
                    phone_battery=int(new_state.get("phone_battery", 0) or 0),
                    social_energy=int(new_state.get("social_energy", 0) or 0),
                    action_id=decision.action,
                    recorded_at=datetime.now(UTC),
                )
                session.add(history)

            # 更新 Redis 实时状态
            await self.redis.hset(
                f"char:{character_id}:state",
                mapping=encode_state_mapping(new_state),  # type: ignore[arg-type]
            )

            # 位置变化时维护场景在场人数（SceneEvolution 拥挤度数据源）
            old_location = context["state"].get("location")
            new_location = new_state.get("location")
            if decision.action == "move" and new_location and old_location != new_location:
                from src.core.world.evolutions.scene_evolution import VISITORS_KEY

                if old_location:
                    await self.redis.hincrby(VISITORS_KEY, str(old_location), -1)
                await self.redis.hincrby(VISITORS_KEY, str(new_location), 1)

            from src.observability.metrics import ACTION_EXECUTION_DURATION, ACTION_EXECUTION_TOTAL

            ACTION_EXECUTION_TOTAL.labels(action_id=decision.action, status="success").inc()
            ACTION_EXECUTION_DURATION.labels(action_id=decision.action).observe(time.perf_counter() - start_perf)

            logger.info(
                "action_executed",
                character_id=str(character_id),
                action=decision.action,
                duration=duration,
            )
        except Exception:
            from src.observability.metrics import ACTION_EXECUTION_TOTAL

            ACTION_EXECUTION_TOTAL.labels(action_id=decision.action, status="failed").inc()
            raise

    async def _handle_character_chat(
        self,
        character_id: UUID,
        decision: DecisionResult,
        context: dict[str, Any],
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
        from src.core.locks import acquire_resource_locks

        async with acquire_resource_locks(self.redis, character_id, target_id) as acquired:
            if not acquired:
                logger.info(
                    "chat_with_lock_busy",
                    character_id=str(character_id),
                    target_id=target_id_str,
                )
                return None
            return await self._do_chat_with(character_id, target_id, target_id_str, character, decision, context)

    async def _do_chat_with(
        self,
        character_id: UUID,
        target_id: UUID,
        target_id_str: str,
        character: Any,
        decision: DecisionResult,
        context: dict[str, Any],
    ) -> str | None:
        """chat_with 实际执行逻辑（在跨角色锁保护下运行）"""
        async with db.session() as session:
            char_repo = CharacterRepository(session)
            target_data = await char_repo.get_character_with_state(target_id)
        if target_data is None:
            logger.warning("chat_with_target_not_found", target_id=target_id_str)
            return None
        target_char, _ = target_data

        # 读取关系（用于在 prompt 中说明亲密度，影响对话语气）
        from src.modules.relation.graph import RelationGraph

        rel_snapshot = None
        try:
            async with db.session() as rel_session:
                graph = RelationGraph(rel_session, self.redis)
                rel_snapshot = await graph.get_relation(character_id, target_id)
        except Exception as e:
            logger.debug("chat_relation_query_failed_continue", error=str(e))

        relationship_desc = "陌生人"
        if rel_snapshot:
            relationship_desc = rel_snapshot.relationship_type

        # 提取双方性格
        def _personality_text(c: Character) -> str:
            p = (c.traits or {}).get("personality", [])
            return "、".join(p) if isinstance(p, list) else str(p)

        # 生成对话（一次往返：发起方说一句，对方回应一句）
        # 不暴露工程概念，用自然语言描述场景
        state = context["state"]
        world = context["world"]
        prompt = self.prompts.render(
            "chat_with",
            location=state.get("location", "某处"),
            world_time=world.get("world_time", "未知"),
            weather=world.get("weather", "未知"),
            initiator_name=character.name,
            initiator_personality=_personality_text(character),
            mood=state.get("mood", "calm"),
            target_name=target_char.name,
            target_personality=_personality_text(target_char),
            relationship=relationship_desc,
            intent=decision.reason,
        )

        try:
            dialogue = await self.llm.chat(prompt, model="chat", system_prompt=self.prompts.render("safety"))
            dialogue = dialogue.strip()
            if len(dialogue) < 5:
                return None
            # 截断超长对话
            dialogue = dialogue[:800]
        except Exception as e:
            logger.error(
                "chat_dialogue_generation_failed",
                character_id=str(character_id),
                target_id=target_id_str,
                error=str(e),
                exc_info=True,
            )
            return None

        # 更新双向关系：陌生人破冰 +2，其他 +5（双方同步）
        strength_delta = 2 if relationship_desc == "stranger" else 5
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
        # 让两人都记得这次对话，未来检索时可回忆起
        try:
            from src.db.models import MemoryEpisode

            async with db.session() as session:
                now = datetime.now(UTC)

                # 发起方记忆：第一人称视角
                session.add(
                    MemoryEpisode(
                        character_id=character_id,
                        content=f"在{state.get('location', '某处')}和{target_char.name}聊天。{dialogue}",
                        importance=6,
                        timestamp=now,
                        source_type="conversation",
                        related_characters=[target_id],
                        location=state.get("location"),
                    )
                )

                # 对方记忆：第一人称视角（target 视角）
                session.add(
                    MemoryEpisode(
                        character_id=target_id,
                        content=f"在{state.get('location', '某处')}和{character.name}聊天。{dialogue}",
                        importance=6,
                        timestamp=now,
                        source_type="conversation",
                        related_characters=[character_id],
                        location=state.get("location"),
                    )
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
            dialogue_length=len(dialogue),
        )

        return dialogue

    async def _memorize(self, character_id: UUID, decision: DecisionResult, context: dict[str, Any]) -> None:
        """记忆沉淀

        流程：
        1. 生成记忆内容（基于 Action + 状态）
        2. 写入 MemoryEpisode
        3. 检查是否需要反思

        Args:
            character_id: 角色 ID
            decision: 决策结果
            context: 感知环境结果
        """
        character = context["character"]
        state = context["state"]

        # 生成记忆内容
        memory_content = f"{character.name}在{state.get('location')}执行了{decision.action}。理由：{decision.reason}"

        # 写入记忆（需要 db session）
        async with db.session() as session:
            mem_repo = MemoryRepository(session)
            ref_repo = ReflectionRepository(session)

            # 创建服务实例
            episode_service = EpisodeService(self.llm, mem_repo, prompts=self.prompts)
            reflection_service = ReflectionService(self.llm, mem_repo, ref_repo, prompts=self.prompts)

            # 写入记忆片段
            # 根据动作类型动态计算重要性（1-10）
            _ACTION_IMPORTANCE = {
                "wait": 2,
                "rest": 3,
                "sleep": 3,
                "eat": 4,
                "drink": 4,
                "move": 4,
                "go_out": 5,
                "work": 6,
                "study": 6,
                "practice": 6,
                "social": 7,
                "chat": 7,
                "play": 6,
                "shop": 5,
                "buy": 5,
                "explore": 7,
                "adventure": 8,
            }
            base_importance = _ACTION_IMPORTANCE.get(decision.action, 5)
            # 如果理由中包含情绪关键词，提升重要性
            reason_lower = (decision.reason or "").lower()
            if any(kw in reason_lower for kw in ["开心", "兴奋", "生气", "难过", "惊讶", "重要", "特别"]):
                base_importance = min(10, base_importance + 2)
            importance = max(1, min(10, base_importance))

            await episode_service.create_episode(
                character_id,
                memory_content,
                action_id=decision.action,
                location=state.get("location"),
                importance=importance,
                character_name=character.name,
                reason=decision.reason,
                mood=state.get("mood"),
            )

            # 检查反思
            await reflection_service.check_and_reflect(character_id)

        logger.debug(
            "memory_created",
            character_id=str(character_id),
            action=decision.action,
        )

    async def _maybe_proactive_share(
        self, character_id: UUID, decision: DecisionResult, context: dict[str, Any]
    ) -> None:
        """主动分享 - 角色主动向用户推送消息

        触发条件：LLM 决策的 proactive_share_intent=True
        流程：
        1. 调用 ProactiveSharingService.evaluate_and_share 生成文案并写入 DB
        2. 对 QQ 平台用户，通过 OneBotAdapter.push_share 主动推送
        3. 对 Web 用户，通过 WebSocketManager.send_to_user 推送

        分享失败不中断 Tick 主流程（由调用方 try/except 兜底）。

        Args:
            character_id: 角色 ID
            decision: 决策结果
            context: 感知环境结果
        """
        # 延迟导入避免循环依赖（main.py 导入 character_tick）
        from src.messaging.proactive_sharing import ProactiveSharingService

        # 获取 ActionRecord（evaluate_and_share 需要 action 参数）
        # 从最近的 ActionRecord 中取本次 Tick 的行为
        action_record = None
        try:
            async with db.session() as session:
                action_repo = ActionRepository(session)
                recent_actions = await action_repo.get_by_character(character_id, limit=1)
                if recent_actions:
                    action_record = recent_actions[0]
        except Exception as e:
            logger.warning(
                "proactive_share_load_action_failed",
                character_id=str(character_id),
                error=str(e),
            )

        # 调用 ProactiveSharingService 生成分享并写入 DB
        async with db.session() as session:
            # 获取 ws_manager（可能为 None，Web 客户端实时推送可选）
            from src.runtime import get_ws_manager

            ws_manager = get_ws_manager()

            sharing_svc = ProactiveSharingService(
                session=session,
                llm=self.llm,
                prompts=self.prompts,
                ws_manager=ws_manager,
                redis=self.redis,
            )

            result = await sharing_svc.evaluate_and_share(
                character_id=character_id,
                action=action_record,
                state=None,  # 从 DB 加载
            )

        if not result.get("shared"):
            logger.debug(
                "proactive_share_skipped",
                character_id=str(character_id),
                reason=result.get("reason"),
            )
            return

        content = result.get("content", "")
        recipients = result.get("recipients", 0)
        logger.info(
            "proactive_share_delivered",
            character_id=str(character_id),
            recipients=recipients,
            content_length=len(content),
        )

        # QQ 平台主动推送：查询该角色的 QQ 平台活跃会话，通过 OneBot 推送
        if content and recipients > 0:
            await self._push_share_to_qq(character_id, content)

    async def _push_share_to_qq(self, character_id: UUID, content: str) -> None:
        """将主动分享推送到 QQ 平台有活跃会话的用户

        查询 conversations 表中 platform=qq 的会话，提取 user_id（格式 qq_{qq_number}），
        通过 OneBotAdapter.push_share 发送主动消息。

        Args:
            character_id: 角色 ID
            content: 分享文案
        """
        try:
            from src.runtime import get_onebot_adapter

            onebot_adapter = get_onebot_adapter()
        except (ImportError, AttributeError):
            logger.debug("onebot_adapter_not_available_for_share")
            return

        if onebot_adapter is None:
            return

        # 查询 QQ 平台会话
        try:
            async with db.session() as session:
                conv_repo = ConversationRepository(session)
                conversations = await conv_repo.list_by_character(
                    character_id=character_id,
                    limit=100,
                )
        except Exception as e:
            logger.warning(
                "qq_share_list_conversations_failed",
                character_id=str(character_id),
                error=str(e),
            )
            return

        # 筛选 QQ 平台会话，提取 QQ 号
        qq_pushed = 0
        for conv in conversations:
            if conv.platform != "qq":
                continue
            # user_id 格式：qq_{qq_number}
            user_id_str = conv.user_id or ""
            if not user_id_str.startswith("qq_"):
                continue
            qq_number = user_id_str[3:]
            if not qq_number or not qq_number.isdigit():
                continue

            try:
                ok = await onebot_adapter.push_share(
                    user_id=int(qq_number),
                    group_id=None,
                    message=content,
                )
                if ok:
                    qq_pushed += 1
            except Exception as e:
                logger.warning(
                    "qq_share_push_failed",
                    character_id=str(character_id),
                    qq_number=qq_number,
                    error=str(e),
                )

        if qq_pushed > 0:
            logger.info(
                "proactive_share_qq_pushed",
                character_id=str(character_id),
                pushed=qq_pushed,
            )

    async def tick_all_active(
        self,
        characters: list[Character] | None = None,
    ) -> list[tuple[Character, BaseException | None]]:
        """并发执行所有活跃角色的 Tick

        Args:
            characters: 可选，外部已查询的活跃角色列表；None 时自行查询。
                主循环传入以复用查询结果并获取逐角色执行结果。

        Returns:
            (character, exception) 列表；exception 为 None 表示该角色 Tick 成功
        """
        if characters is None:
            async with db.session() as session:
                char_repo = CharacterRepository(session)
                characters = await char_repo.get_active_characters()

        logger.info("tick_all_start", count=len(characters))

        results = await asyncio.gather(
            *(self.tick_character(char.id) for char in characters),
            return_exceptions=True,
        )

        logger.info("tick_all_end", count=len(characters))
        return list(zip(characters, results, strict=True))
