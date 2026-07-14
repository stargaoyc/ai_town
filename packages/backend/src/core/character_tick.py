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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from structlog import get_logger

from src.actions import Action, ActionRegistry, DecisionResult
from src.config import settings
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

        # 初始化并发信号量（类级别共享）
        if CharacterTickEngine.SEMAPHORE is None:
            CharacterTickEngine.SEMAPHORE = asyncio.Semaphore(settings.character_max_concurrent)

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

        # 尝试获取锁
        acquired = await self.redis.set(lock_key, "tick", ex=self.LOCK_TTL, nx=True)
        if not acquired:
            logger.debug("character_tick_skipped", character_id=str(character_id))
            return

        try:
            semaphore = CharacterTickEngine.SEMAPHORE
            assert semaphore is not None
            async with semaphore:
                await self._execute_tick(character_id)
        finally:
            await self.redis.delete(lock_key)

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

        # 3. LLM 决策
        decision = await self._decide(character_id, context, candidates)

        # 3.5 MCP 工具调用（如果 LLM 决定使用工具）
        if decision.action == "use_tool":
            await self._execute_mcp_tool(character_id, decision, context)
            # 工具调用后，继续执行一个默认 Action（如 wait）
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

    async def _perceive(self, character_id: UUID) -> dict:
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
            {str(k): v for k, v in redis_state.items()}
            if redis_state
            else {
                "location": char_state.location,
                "stamina": char_state.stamina,
                "satiety": char_state.satiety,
                "mood": char_state.mood,
                "money": char_state.money,
                "phone_battery": char_state.phone_battery,
                "social_energy": char_state.social_energy,
            }
        )

        # 确保 state 中的数值字段是 int 类型（Redis 读取为 str/bytes）
        _NUMERIC_KEYS = {"stamina", "satiety", "money", "phone_battery", "social_energy"}
        for key in _NUMERIC_KEYS:
            if key in state:
                val = state[key]
                if isinstance(val, (bytes, bytearray)):
                    state[key] = int(val.decode())
                elif isinstance(val, str):
                    state[key] = int(val)
            elif char_state:
                state[key] = getattr(char_state, key, 50)

        # mood 可以是字符串
        if "mood" in state and isinstance(state["mood"], (bytes, bytearray)):
            state["mood"] = state["mood"].decode()
        elif "mood" not in state and char_state:
            state["mood"] = getattr(char_state, "mood", "calm")

        # 从 Redis 读取世界状态
        world_state = await self.redis.hgetall("world:state")
        world = dict(world_state) if world_state else {}

        # 解码 world state 中的 bytes
        for key, value in world.items():
            if isinstance(value, bytes):
                world[key] = value.decode()

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

        # 感知同场景其他角色（多智能体交互关键）
        # 提供角色名、性格、当前动作、关系强度，供 LLM 决策是否发起社交
        nearby_characters: list[dict] = []
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
        }

    async def _decide(self, character_id: UUID, context: dict, candidates: list[Action]) -> DecisionResult:
        """LLM 决策 - 结构化输出

        使用 PromptTemplates.render() 生成决策 Prompt
        调用 LLMClient.structured_output() 获取结构化结果

        Args:
            character_id: 角色 ID
            context: 感知环境结果
            candidates: 候选 Action 列表

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

        # 构建 MCP 工具列表文本（角色可调用外部工具获取信息）
        try:
            from src.mcp import MCPClient

            mcp_client = MCPClient()
            mcp_tools_text = await mcp_client.format_tools_for_prompt()
        except Exception:
            mcp_tools_text = "（MCP 工具不可用）"

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

        # 追加 MCP 工具信息到 Prompt
        prompt += (
            f"\n\n[可用工具（MCP）]\n"
            f"你可以在行动中使用以下工具获取信息或执行操作：\n"
            f"{mcp_tools_text}\n"
            f'如需使用工具，在 action 字段填写 "use_tool"，'
            f'在 params 中填写 tool_name（如 "weather.get_current_weather"）和 tool_args（参数字典）。'
        )

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

    async def _execute_mcp_tool(self, character_id: UUID, decision: DecisionResult, context: dict) -> dict | None:
        """执行 MCP 工具调用

        当 LLM 决定使用工具时，通过 MCPClient 调用对应 MCP Server，
        将工具结果存入角色记忆，供后续决策使用。

        Args:
            character_id: 角色 ID
            decision: 决策结果（params 中包含 tool_name 和 tool_args）
            context: 感知环境结果

        Returns:
            工具返回结果字典，失败时返回 None
        """
        from src.mcp import MCPClient

        tool_name = decision.params.get("tool_name", "")
        tool_args = decision.params.get("tool_args", {})

        if not tool_name:
            logger.warning("mcp_tool_call_no_tool_name", character_id=str(character_id))
            return None

        character = context["character"]
        logger.info(
            "mcp_tool_call_start",
            character_id=str(character_id),
            character_name=character.name,
            tool_name=tool_name,
            tool_args=tool_args,
        )

        client = MCPClient()
        result = await client.call_tool_by_full_name(tool_name, tool_args)

        if result.get("success"):
            logger.info(
                "mcp_tool_call_success",
                character_id=str(character_id),
                tool_name=tool_name,
                result_preview=str(result.get("result", ""))[:200],
            )

            # 将工具结果存入角色记忆
            try:
                async with db.session() as session:
                    mem_repo = MemoryRepository(session)
                    episode_service = EpisodeService(self.llm, mem_repo)
                    await episode_service.create_episode(
                        character_id,
                        f"[工具调用] {tool_name}({tool_args}) → {str(result.get('result', ''))[:500]}",
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
                    "mcp_tool_memory_save_failed",
                    character_id=str(character_id),
                    error=str(e),
                )
        else:
            logger.warning(
                "mcp_tool_call_failed",
                character_id=str(character_id),
                tool_name=tool_name,
                error=result.get("error"),
            )

        return result

    async def _execute_action(self, character_id: UUID, decision: DecisionResult, context: dict) -> None:
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

        # 计算状态变更
        duration = decision.duration or action_def.duration_minutes
        new_state = context["state"].copy()

        # 应用资源变更（使用 apply_cost_fields 辅助函数）
        from src.actions.base import apply_cost_fields

        changes = apply_cost_fields(new_state, action_def)
        new_state.update(changes)

        # 更新位置（如果是移动 Action）
        if decision.action == "move" and decision.params.get("target_scene"):
            new_state["location"] = decision.params["target_scene"]

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
                mapping={k: str(v) for k, v in new_state.items() if v is not None},
            )

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
        context: dict,
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
        prompt = (
            f"场景：{state.get('location', '某处')}，"
            f"虚拟时间：{world.get('world_time', '未知')}，天气：{world.get('weather', '未知')}\n"
            f"发起方：{character.name}（性格：{_personality_text(character)}），"
            f"当前情绪：{state.get('mood', 'calm')}\n"
            f"对方：{target_char.name}（性格：{_personality_text(target_char)}）\n"
            f"双方关系：{relationship_desc}\n"
            f"发起方意图：{decision.reason}\n\n"
            f"请生成两人之间简短自然的对话，各说一两句话，符合双方性格和关系亲密程度。\n"
            f"陌生人：礼貌客气，可能聊天气或场景；熟人/朋友：可以聊日常；密友/挚友：可以聊心事。\n"
            f"严格约束：\n"
            f"- 不要暴露 Action/system/LLM 等工程概念\n"
            f"- 不要用括号描写动作\n"
            f"- 不要让角色播报数值（如体力 80/100）\n"
            f"- 时间/天气必须与上述场景信息一致\n"
            f"- 总长度 60-200 字\n"
            f"输出格式：\n"
            f"{character.name}: <台词>\n"
            f"{target_char.name}: <台词>"
        )

        try:
            dialogue = await self.llm.chat(prompt, model="chat")
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

    async def _memorize(self, character_id: UUID, decision: DecisionResult, context: dict) -> None:
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
            episode_service = EpisodeService(self.llm, mem_repo)
            reflection_service = ReflectionService(self.llm, mem_repo, ref_repo)

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

    async def _maybe_proactive_share(self, character_id: UUID, decision: DecisionResult, context: dict) -> None:
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

    async def tick_all_active(self) -> None:
        """执行所有活跃角色的 Tick

        从数据库获取所有活跃角色，并发执行 Tick
        """
        async with db.session() as session:
            char_repo = CharacterRepository(session)
            characters = await char_repo.get_active_characters()

        logger.info("tick_all_start", count=len(characters))

        # 并发执行所有角色的 Tick
        tasks = [self.tick_character(char.id) for char in characters]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("tick_all_end", count=len(characters))
