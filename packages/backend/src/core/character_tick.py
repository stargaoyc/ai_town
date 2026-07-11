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
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from structlog import get_logger

from src.actions import Action, ActionRegistry, DecisionResult
from src.config import settings
from src.db.models import ActionRecord
from src.db.repositories import (
    ActionRepository,
    CharacterRepository,
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
            CharacterTickEngine.SEMAPHORE = asyncio.Semaphore(
                settings.character_max_concurrent
            )

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
        acquired = await self.redis.set(
            lock_key, "tick", ex=self.LOCK_TTL, nx=True
        )
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
        candidates = self.registry.get_candidates(
            context["state"], scene=context["state"].get("location")
        )

        if not candidates:
            logger.warn("no_candidates", character_id=str(character_id))
            return

        # 3. LLM 决策
        decision = await self._decide(character_id, context, candidates)

        # 4. 执行 Action
        await self._execute_action(character_id, decision, context)

        # 5. 记忆沉淀
        await self._memorize(character_id, decision, context)

        from src.observability.metrics import (
            CHARACTER_TICK_DURATION,
            CHARACTER_TICK_ERRORS,
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
        """感知环境 - 读取角色状态、世界状态、记忆

        Args:
            character_id: 角色 ID

        Returns:
            dict: {
                "character": Character,  # 角色档案
                "state": dict,           # 角色状态（Redis 缓存优先）
                "world": dict,           # 世界状态
                "memories": list[dict],  # 相关记忆
                "plans": list[Plan],     # 当前计划
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
        state: dict[str, Any] = {str(k): v for k, v in redis_state.items()} if redis_state else {
            "location": char_state.location,
            "stamina": char_state.stamina,
            "satiety": char_state.satiety,
            "mood": char_state.mood,
            "money": char_state.money,
            "phone_battery": char_state.phone_battery,
            "social_energy": char_state.social_energy,
        }

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
                memories = await retrieval_service.search(
                    character_id, query, top_k=10
                )
        except Exception as e:
            logger.warning(
                "memory_retrieval_failed_continue",
                character_id=str(character_id),
                error=str(e),
            )

        return {
            "character": character,
            "state": state,
            "world": world,
            "memories": memories,
            "plans": plans,
        }

    async def _decide(
        self, character_id: UUID, context: dict, candidates: list[Action]
    ) -> DecisionResult:
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
        candidates_text = "\n".join([
            f"- {a.id}: {a.name}（耗时{a.duration_minutes}分钟，体力消耗{a.energy_cost}）"
            for a in candidates
        ])

        # 构建记忆文本
        memories_text = "\n".join([
            m.get("content", str(m)) if isinstance(m, dict) else str(m)
            for m in context["memories"]
        ]) if context["memories"] else "暂无相关记忆"

        # 构建计划文本
        plans_text = "\n".join([
            f"- {p.title}（进度{p.progress}%）"
            for p in context["plans"]
        ]) if context["plans"] else "暂无计划"

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
            world_time=world.get("world_time", datetime.now(timezone.utc).isoformat()),
            weather=world.get("weather", "sunny"),
            scenes="",  # 简化
            memories=memories_text,
            plans=plans_text,
            candidates=candidates_text,
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
                        }
                    }
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
        raw_plan_changes = result.get("planChanges", [])
        plan_changes = [
            pc if isinstance(pc, dict) else {"description": str(pc)}
            for pc in raw_plan_changes
        ]

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

    async def _execute_action(
        self, character_id: UUID, decision: DecisionResult, context: dict
    ) -> None:
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

        # 事务化执行
        try:
            async with db.session() as session:
                action_repo = ActionRepository(session)
                char_repo = CharacterRepository(session)

                # 写入行为记录
                record = ActionRecord(
                    character_id=character_id,
                    action_id=action_def.id,
                    action_name=action_def.name,
                    params=decision.params,
                    reason=decision.reason,
                    duration_minutes=duration,
                    location=new_state.get("location", "unknown"),
                    timestamp=datetime.now(timezone.utc),
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
                    phone_battery=int(new_state["phone_battery"]) if new_state.get("phone_battery") is not None else None,
                    social_energy=int(new_state["social_energy"]) if new_state.get("social_energy") is not None else None,
                    location=new_state.get("location"),
                )

            # 更新 Redis 实时状态
            await self.redis.hset(
                f"char:{character_id}:state",
                mapping={k: str(v) for k, v in new_state.items() if v is not None},
            )

            from src.observability.metrics import ACTION_EXECUTION_TOTAL, ACTION_EXECUTION_DURATION
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

    async def _memorize(
        self, character_id: UUID, decision: DecisionResult, context: dict
    ) -> None:
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
        memory_content = (
            f"{character.name}在{state.get('location')}执行了{decision.action}。"
            f"理由：{decision.reason}"
        )

        # 写入记忆（需要 db session）
        async with db.session() as session:
            mem_repo = MemoryRepository(session)
            ref_repo = ReflectionRepository(session)

            # 创建服务实例
            episode_service = EpisodeService(self.llm, mem_repo)
            reflection_service = ReflectionService(self.llm, mem_repo, ref_repo)

            # 写入记忆片段
            await episode_service.create_episode(
                character_id,
                memory_content,
                action_id=decision.action,
                location=state.get("location"),
                importance=5,  # 默认重要性
            )

            # 检查反思
            await reflection_service.check_and_reflect(character_id)

        logger.debug(
            "memory_created",
            character_id=str(character_id),
            action=decision.action,
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