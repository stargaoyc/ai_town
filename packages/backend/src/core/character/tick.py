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
import json
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.actions import Action, ActionRegistry, DecisionResult
from src.actions.base import apply_cost_fields
from src.config import settings
from src.core.character.perception import (
    _DECISION_ITEM_MAX_CHARS,
    PerceptionMixin,
    _parse_world_hour,
    _world_hour,
)
from src.core.character.plan_applier import PlanChangeApplier
from src.core.character.social import SocialMixin
from src.core.locks import release_lock, watch_locks
from src.core.state_codec import encode_state_mapping
from src.core.world.evolutions.scene_evolution import VISITORS_KEY
from src.core.world.evolutions.weather_evolution import WEATHER_IMPACT
from src.cost_control import CircuitOpen
from src.db.models import ActionRecord, Character, CharacterStateHistory
from src.db.repositories import (
    ActionRepository,
    CharacterRepository,
    MemoryRepository,
    PlanRepository,
    ReflectionRepository,
    RelationRepository,
)
from src.db.session import db
from src.llm import LLMClient, PromptTemplates
from src.memory import EpisodeService, ReflectionService
from src.modules.town.schema import SceneType
from src.observability.langfuse_tracing import end_tick_trace, start_tick_trace, trace_character_tick
from src.observability.metrics import (
    ACTION_EXECUTION_DURATION,
    ACTION_EXECUTION_TOTAL,
    CHARACTER_TICK_DURATION,
    CHARACTER_TICK_TOTAL,
)
from src.observability.tracing import trace_span
from src.runtime import get_duration_calculator, get_movement_system, get_scene_loader, get_schedule_system
from src.tools import ToolRegistry

logger = get_logger(__name__)


# LLM 动态耗时的绝对上限（虚拟分钟）。实际生效仍受 Action.allow_dynamic_duration
# 门控，但 schema 层不设上限会放行 10^9 级极端值（R4-L4）
_MAX_DYNAMIC_DURATION = 480


def _resolve_action_id(raw_action: Any, candidates: list[Action]) -> str:
    """LLM 返回 action 的落地规则。

    use_tool 是 ReAct 循环的保留字而非注册 Action——它必须原样放行给循环守卫，
    否则工具调用自特性落地起即为死代码（R4-H1 出生缺陷）；
    其余未命中候选列表的值一律回退 wait，保证 LLM 无法绕过 precondition 过滤。
    """
    action_id = raw_action if raw_action is not None else "wait"
    if action_id == "use_tool":
        return action_id
    valid_action_ids = [a.id for a in candidates]
    if action_id not in valid_action_ids:
        logger.warn("invalid_action", action=action_id, fallback="wait")
        if "wait" in valid_action_ids:
            return "wait"
        return valid_action_ids[0] if valid_action_ids else "wait"
    return str(action_id)


def _clamp_dynamic_duration(raw: Any) -> int | None:
    """钳制 LLM 返回的动态耗时：非法值归 None，合法值收敛到 [1, _MAX_DYNAMIC_DURATION]"""
    try:
        value = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    if value is None:
        return None
    return max(1, min(_MAX_DYNAMIC_DURATION, value))


def _action_param_hint(action: Action) -> str:
    """从 params_schema 提取必填参数提示（R4-M10：参数契约进 Prompt）。

    此前 move 的 target_scene 契约只存在于执行器代码里，LLM 只能靠猜，
    缺参决策 fail-safe 到 wait 白白浪费整个 Tick。
    """
    schema = action.params_schema
    if not schema:
        return ""
    required = schema.get("required") or []
    props = schema.get("properties") or {}
    parts: list[str] = []
    for name in required:
        prop = props.get(name)
        desc = str(prop.get("description", "") or "") if isinstance(prop, dict) else ""
        parts.append(f"{name}（{desc}）" if desc else name)
    if not parts:
        return ""
    return f"；需在 params 填写: {'、'.join(parts)}"


_ACTIVITY_LABELS = {
    "sleeping": "睡眠",
    "drowsy": "低耗（准备入睡）",
    "active": "活跃",
    "peak": "高峰",
}


def _schema_example(schema: dict[str, Any]) -> str:
    """从决策 JSON Schema 生成输出格式示例（单一真相源，审查 P3）

    decision.yaml 不再手写 JSON 骨架——schema 变更时示例自动同步。
    """

    def example(prop: dict[str, Any]) -> Any:
        t = prop.get("type")
        if t == "string":
            desc = str(prop.get("description", "")).strip()
            enum = prop.get("enum")
            return f"<{enum[0]}|...>" if enum else f"<{desc}>" if desc else "<字符串>"
        if t == "integer":
            return 0
        if t == "number":
            return 0
        if t == "boolean":
            return False
        if t == "array":
            items = prop.get("items", {})
            return [example(items)] if items else []
        if t == "object":
            return {k: example(v) for k, v in prop.get("properties", {}).items()}
        return None

    stub = {k: example(v) for k, v in schema.get("properties", {}).items()}
    return json.dumps(stub, ensure_ascii=False)


# 工具调用记忆的重要性档位。
# 必须低于 7：保留策略对 importance>=7 永久不清理，而工具调用高频发生，
# 若记为 7 会使这类记忆线性膨胀且永久占据 HNSW 索引（审查 §11.2）
_TOOL_MEMORY_IMPORTANCE = 6


def _build_schedule_text(character: Any, world: dict[str, Any]) -> str:
    """作息节奏文本（ScheduleSystem 桥接）：时段档位 + 睡眠约束提示"""
    system = get_schedule_system()
    if system is None:
        return "（无作息档案）"
    traits = character.traits or {}
    schedule_name = system.get_schedule_from_traits(traits)
    hour = _world_hour(world)
    level = str(system.get_activity_level(schedule_name, hour))
    label = _ACTIVITY_LABELS.get(level, level)
    text = f"当前为「{label}」时段"
    if system.is_sleeping(schedule_name, hour):
        text += "（睡眠时间：应休息/睡眠，不宜外出或社交）"
    return text


class CharacterTickEngine(PerceptionMixin, SocialMixin):
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

    @trace_span("character.tick")
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

        # 看门狗：单次 Tick 含多次 LLM 调用，耗时可能超过 LOCK_TTL，定期续租防止锁过期易主。
        # 续租失败 → lock_lost 置位，Tick 在写入闸口自查中止，防止跨实例 double-tick（round-3 review H10）
        renew_stop = asyncio.Event()
        lock_lost = asyncio.Event()
        watchdog = asyncio.create_task(
            watch_locks(self.redis, renew_stop, {lock_key: lock_token}, self.LOCK_TTL, lock_lost=lock_lost)
        )

        try:
            self._ensure_semaphore()
            semaphore = CharacterTickEngine.SEMAPHORE
            assert semaphore is not None
            async with semaphore:
                await self._execute_tick(character_id, lock_lost=lock_lost)
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

    async def _execute_tick(self, character_id: UUID, *, lock_lost: asyncio.Event) -> None:
        """五阶段闭环核心逻辑

        Args:
            character_id: 角色 ID
            lock_lost: 看门狗失锁信号；置位后本 Tick 禁止任何 PG/Redis 状态写入（H10）
        """
        logger.info("character_tick_start", character_id=str(character_id))

        start_perf = time.perf_counter()
        cid = str(character_id)

        # Langfuse 根 trace：本 Tick 内全部 LLM 调用自动挂为其子 generation
        tick_trace_id = start_tick_trace(cid)

        # 1. 感知环境
        context = await self._perceive(character_id)

        # 2. 候选过滤
        candidates = self.registry.get_candidates(context["state"], scene=context["state"].get("location"))

        # 群活动人数门槛：同场景至少 2 名其他角色（含自己 >=3 人）才保留候选
        if any(a.id == "group_activity" for a in candidates):
            if len(context.get("nearby_characters") or []) < 2:
                candidates = [a for a in candidates if a.id != "group_activity"]

        if not candidates:
            logger.warn("no_candidates", character_id=str(character_id))
            return

        # 3. LLM 决策（ReAct 循环：工具调用 → 观察结果 → 再次决策）
        decision = await self._decide(character_id, context, candidates, [])
        decision = await self._run_react_loop(character_id, context, candidates, decision, lock_lost=lock_lost)

        # H10 闸口：失锁后禁止执行 Action 与记忆沉淀——另一实例可能已接管该角色，
        # 此处继续写入即构成跨实例 double-tick
        if lock_lost.is_set():
            logger.warning("character_tick_aborted_lock_lost", character_id=str(character_id))
            return

        # 4. 执行 Action
        await self._execute_action(character_id, decision, context, lock_lost=lock_lost)

        # 5. 记忆沉淀
        await self._memorize(character_id, decision, context)

        # 5.5 群体动力学·传闻传播（好友的显著经历 -> 第二手记忆）
        try:
            await self._propagate_gossip(character_id)
        except Exception as e:
            # 传闻失败不影响 Tick 主流程
            logger.warning(
                "gossip_tick_failed",
                character_id=str(character_id),
                error=str(e),
                exc_info=True,
            )

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

        tick_elapsed = time.perf_counter() - start_perf
        CHARACTER_TICK_DURATION.observe(tick_elapsed)
        CHARACTER_TICK_TOTAL.labels(character_id=cid).inc()

        trace_character_tick(
            character_id=str(character_id),
            action=decision.action,
            duration_ms=int(tick_elapsed * 1000),
            trace_id=tick_trace_id,
        )
        end_tick_trace(action=decision.action, duration_ms=int(tick_elapsed * 1000))

        logger.info(
            "character_tick_end",
            character_id=str(character_id),
            action=decision.action,
        )

    @trace_span("character.decide")
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

        # 构建候选 Action 列表文本（附必填参数契约，见 _action_param_hint）
        candidates_text = "\n".join(
            [
                f"- {a.id}: {a.name}（耗时{a.duration_minutes}分钟，体力消耗{a.energy_cost}）{_action_param_hint(a)}"
                for a in candidates
            ]
        )

        # 构建工具列表文本（角色可调用本地工具获取信息或执行操作）
        # R5-M3：无启用工具时 formatter 返回 None，调用方整段跳过工具说明；
        # 此前无条件追加会让 LLM 在零工具环境下仍尝试 use_tool，烧掉最多 3 轮 ReAct
        tool_registry = ToolRegistry()
        tools_text = await tool_registry.format_tools_for_prompt()

        # 构建记忆文本（单条截断，见 _DECISION_ITEM_MAX_CHARS）
        memories_text = (
            "\n".join(
                [
                    m.get("content", str(m))[:_DECISION_ITEM_MAX_CHARS]
                    if isinstance(m, dict)
                    else str(m)[:_DECISION_ITEM_MAX_CHARS]
                    for m in context["memories"]
                ]
            )
            if context["memories"]
            else "暂无相关记忆"
        )

        # 构建计划文本（类型/优先级/截止日全量注入，供 LLM 权衡取舍）
        _TYPE_LABEL = {"long_term": "长期", "short_term": "短期", "daily": "今日"}
        plan_lines = []
        for p in context["plans"][:6]:
            type_label = _TYPE_LABEL.get(p.type, p.type)
            deadline_tag = f"，截止 {p.deadline:%m-%d}" if p.deadline else ""
            plan_lines.append(f"- [{type_label}] {p.title}（进度{p.progress}%，优先级{p.priority}{deadline_tag}）")
        plans_text = "\n".join(plan_lines) if plan_lines else "暂无计划"

        # 构建作息节奏文本（ScheduleSystem 桥接：让 LLM 感知当前时段档位与睡眠约束）
        schedule_text = _build_schedule_text(character, world)

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

        # 构建当前场景描述（容量/开放时段/可做活动），供 LLM 校验行为合理性
        scenes_text = "（场景信息不可用）"
        scene_loader = get_scene_loader()
        if scene_loader is not None:
            scene = scene_loader.get_scene(str(state.get("location") or ""))
            if scene is not None:
                open_hours = f"{scene.open_hours[0]}:00-{scene.open_hours[1]}:00"
                activities_text = "、".join(scene.activities) if scene.activities else "无"
                scenes_text = f"{scene.name}（容量{scene.capacity}人，开放{open_hours}，可做：{activities_text}）"

        # 定义决策结果 schema（先于 Prompt 渲染：输出格式示例由 schema 派生，单一真相源）
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "reason": {"type": "string"},
                "params": {"type": "object"},
                "duration": {"type": "integer"},
                # R5-H2：必须在 schema 中显式声明——structured_output 按 schema 属性
                # 生成 pydantic 模型，未声明的键会被静默丢弃，分享意图将永远为 False
                "proactiveShareIntent": {"type": "boolean"},
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
                "createPlanChanges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "type": {"type": "string", "enum": ["long_term", "short_term", "daily"]},
                            "priority": {"type": "integer"},
                            "deadline": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["action", "reason"],
        }

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
            scenes=scenes_text,
            schedule=schedule_text,
            memories=memories_text,
            reflections=context.get("reflections", "暂无高层认知"),
            diary=context.get("diary", "暂无日记"),
            gossips=context.get("recent_gossip", "暂无听说的消息"),
            world_events=context.get("world_events", "暂无近期世界动态"),
            plans=plans_text,
            candidates=candidates_text,
            nearby_characters=nearby_text,
            person_memory=context.get("known_users", "（暂无认识的用户）"),
            output_json_example=_schema_example(schema),
        )

        # 工具说明仅在确有可用工具时追加（R5-M3）：整段跳过而非渲染占位，
        # 避免 Prompt 指示 LLM 输出 use_tool 却没有任何工具可执行
        if tools_text is not None:
            prompt += self.prompts.render("decision_tools", tools_text=tools_text)

        # ReAct 模式：如果有前序工具调用结果，加入 Prompt 让 LLM 基于结果推理
        if tool_observations:
            obs_lines = []
            for i, obs in enumerate(tool_observations, 1):
                success_tag = "成功" if obs.get("success") else "失败"
                # 失败观察可能只有 error（如缺 tool_name 的合成观察），回退展示原因
                result_str = str(obs.get("result") or obs.get("error") or "")[:800]
                obs_lines.append(
                    f"<observation>{i}. 调用 {obs['tool_name']}({obs.get('tool_args', {})}) "
                    f"[{success_tag}]\n   结果: {result_str}</observation>"
                )
            prompt += self.prompts.render("decision_react", observations=chr(10).join(obs_lines))

        # 调用 LLM
        result = await self.llm.structured_output(prompt, schema, model="chat")

        # 验证 Action ID 合法性（use_tool 保留字豁免逻辑收敛在 _resolve_action_id）
        action_id = _resolve_action_id(result.get("action"), candidates)

        # 防御性处理 LLM 返回值类型
        # 注意：LLM 可能返回 "planChanges": null，此时 dict.get() 返回 None 而非默认值 []
        raw_plan_changes = result.get("planChanges") or []
        plan_changes = [pc if isinstance(pc, dict) else {"description": str(pc)} for pc in raw_plan_changes]

        raw_creates = result.get("createPlanChanges") or []
        create_plan_changes = [pc for pc in raw_creates if isinstance(pc, dict)]

        raw_share_intent = result.get("proactiveShareIntent", False)
        proactive_share_intent = bool(raw_share_intent) if raw_share_intent is not None else False

        clamped_duration = _clamp_dynamic_duration(result.get("duration"))

        return DecisionResult(
            action=action_id,
            reason=result.get("reason", ""),
            params=result.get("params") or {},
            duration=clamped_duration,
            plan_changes=plan_changes,
            create_plan_changes=create_plan_changes,
            proactive_share_intent=proactive_share_intent,
        )

    async def _run_react_loop(
        self,
        character_id: UUID,
        context: dict[str, Any],
        candidates: list[Action],
        decision: DecisionResult,
        *,
        lock_lost: asyncio.Event | None = None,
    ) -> DecisionResult:
        """ReAct 循环：工具调用 → 观察结果 → 再次决策（最多 3 轮，防止无限循环）。

        循环退出后若仍停留在 use_tool（LLM 反复要求调工具），强制降级为 wait。
        """
        lost = lock_lost if lock_lost is not None else asyncio.Event()
        tool_observations: list[dict[str, Any]] = []

        for _react_iter in range(3):
            if decision.action != "use_tool":
                break

            # 执行工具调用
            tool_result = await self._execute_tool(character_id, decision, context, lock_lost=lost)
            if tool_result:
                tool_observations.append(
                    {
                        "tool_name": decision.params.get("tool_name", ""),
                        "tool_args": decision.params.get("tool_args", {}),
                        "result": tool_result.get("result", tool_result),
                        "success": tool_result.get("success", False),
                        "error": tool_result.get("error"),
                    }
                )
            else:
                # R5-L12：缺 tool_name 是唯一返回 None 的路径——不补观察会让
                # 后续轮次在零反馈下盲猜，白白烧完剩余轮数
                tool_observations.append(
                    {
                        "tool_name": decision.params.get("tool_name", ""),
                        "tool_args": decision.params.get("tool_args", {}),
                        "success": False,
                        "error": "missing tool_name",
                    }
                )

            # 对状态变更类工具，应用 deltas
            if tool_result and tool_result.get("state_mutating"):
                await self._apply_tool_deltas(character_id, tool_result.get("result", {}), context)

            # 再次决策（带工具观察结果）
            decision = await self._decide(character_id, context, candidates, tool_observations)

        if decision.action == "use_tool":
            logger.warning(
                "react_max_iterations_reached",
                character_id=str(character_id),
                tool_observations=tool_observations,
            )
            decision.action = "wait"

        return decision

    @trace_span("tool.call")
    async def _execute_tool(
        self,
        character_id: UUID,
        decision: DecisionResult,
        context: dict[str, Any],
        *,
        lock_lost: asyncio.Event | None = None,
    ) -> dict[str, Any] | None:
        """执行工具调用

        当 LLM 决定使用工具时，通过 ToolRegistry 直接调用本地 async 函数，
        状态变更类工具的 deltas 交由 ReAct 循环应用到内存 state。

        Args:
            character_id: 角色 ID
            decision: 决策结果（params 中包含 tool_name 和 tool_args）
            context: 感知环境结果（含 state、relations）
            lock_lost: 看门狗失锁信号；置位时跳过工具记忆暂存，
                与主事务闸口共同保证失锁后不产生任何持久化痕迹（R5-M6）

        Returns:
            工具返回结果字典；params 缺 tool_name 时返回 None
            （ReAct 循环据此合成失败观察，R5-L12）
        """
        lost = lock_lost if lock_lost is not None else asyncio.Event()

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

            # 工具记忆不再在循环内独立提交（R5-L11）：暂存到 context，
            # 由 _execute_action 主事务与 ActionRecord 同事务落库——
            # 主事务回滚时一并回滚，杜绝「记忆描述了从未持久化的效果」
            if lost.is_set():
                logger.warning(
                    "tool_memory_staging_skipped_lock_lost",
                    character_id=str(character_id),
                    tool_name=tool_name,
                )
            else:
                context.setdefault("pending_tool_memories", []).append(
                    {
                        "content": f"[工具调用] {tool_name}({tool_args}) → {str(tool_result)[:500]}",
                        "location": context["state"].get("location"),
                        "reason": f"使用工具 {tool_name}",
                        "mood": context["state"].get("mood"),
                    }
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

        # 关系强度变化（R4-M11：不再即时写 PG，暂存到 context，
        # 由 _execute_action 的主事务统一落库，消除「关系已写、行为记录回滚」的部分提交窗口）
        relation_delta = tool_result.get("relation_strength_delta")
        target_id = tool_result.get("target_id")
        if relation_delta and target_id:
            pending = context.setdefault("pending_relation_deltas", [])
            pending.append({"target_id": str(target_id), "delta": int(relation_delta)})
            logger.info(
                "tool_delta_relation_pending",
                character_id=str(character_id),
                target_id=str(target_id),
                delta=relation_delta,
            )

    async def _apply_pending_artifacts(
        self,
        session: AsyncSession,
        character_id: UUID,
        context: dict[str, Any],
    ) -> None:
        """将 ReAct 阶段暂存的工具产物落入 _execute_action 主事务（单一事务语义）

        - pending_relation_deltas：工具产生的关系增量（R4-M11）
        - pending_tool_memories：工具调用记忆（R5-L11），经 EpisodeService
          复用 exists_recent_duplicate 近邻去重，与原独立提交路径语义一致

        任一写入失败随主事务整体回滚，杜绝部分提交。
        """
        rel_repo = RelationRepository(session)
        for item in context.get("pending_relation_deltas") or []:
            rel_target = UUID(str(item["target_id"]))
            rel = await rel_repo.get_or_create(character_id, rel_target)
            new_strength = max(0, min(100, rel.strength + int(item["delta"])))
            await rel_repo.update_relation(character_id, rel_target, strength=new_strength)
            context.setdefault("relations", {})[str(rel_target)] = new_strength
            logger.info(
                "tool_delta_relation_applied",
                character_id=str(character_id),
                target_id=str(rel_target),
                delta=item["delta"],
                new_strength=new_strength,
            )

        pending_memories = context.get("pending_tool_memories") or []
        if not pending_memories:
            return
        mem_repo = MemoryRepository(session)
        episode_service = EpisodeService(self.llm, mem_repo, prompts=self.prompts)
        for item in pending_memories:
            await episode_service.create_episode(
                character_id,
                item["content"],
                action_id="use_tool",
                location=item["location"],
                importance=_TOOL_MEMORY_IMPORTANCE,
                character_name=context["character"].name,
                reason=item["reason"],
                mood=item["mood"],
            )

    @staticmethod
    def _current_world_hour(context: dict[str, Any]) -> int | None:
        """从世界状态解析当前虚拟小时；解析失败返回 None（移动校验跳过开放时间检查）"""
        return _parse_world_hour(str(context.get("world", {}).get("world_time") or ""))

    @staticmethod
    def _current_is_workday(context: dict[str, Any]) -> bool:
        """从世界时钟推导是否工作日（周一至五），供 workday_only 场景开放判断（round-6 M9）

        world_time 缺失/非法（冷启动早期）按工作日处理——与 is_workday 缺省值一致，
        不因时钟未就绪而额外收紧场景准入。
        """
        raw = str(context.get("world", {}).get("world_time") or "")
        try:
            return datetime.fromisoformat(raw).weekday() < 5
        except ValueError:
            return True

    @staticmethod
    def _weather_move_multiplier(context: dict[str, Any]) -> float:
        """当前天气的移动耗时倍率；无天气记录时 1.0，保持原行为（round-6 M9a）"""
        weather = str(context.get("world", {}).get("weather") or "")
        impact = WEATHER_IMPACT.get(weather)
        return impact["move_multiplier"] if impact else 1.0

    async def _apply_duration_modifiers(self, context: dict[str, Any], base_duration: int) -> int:
        """非移动 Action 的动态耗时修正：天气/拥挤度/体力/情绪（round-6 M9c）

        DurationCalculator 此前只被 API demo 端点触达；Tick 主路径统一在此接入。
        结果仍受 [1, _MAX_DYNAMIC_DURATION] 全局钳制。模块降级（lifespan 初始化
        失败）时跳过修正保持基础耗时，与 move 路径对 MovementSystem 缺失的处理同哲学。

        未知位置（如初始 "unknown"）按室内处理——天气修正只对确认的户外场景生效，
        避免对未知地点误加天气惩罚。
        """
        calculator = get_duration_calculator()
        scene_loader = get_scene_loader()
        if calculator is None or scene_loader is None:
            return base_duration

        state = context["state"]
        location = str(state.get("location") or "")
        scene = scene_loader.get_scene(location)
        adjusted = calculator.calculate_duration(
            base_duration,
            weather=str(context.get("world", {}).get("weather") or "sunny"),
            is_outdoor=scene is not None and scene.type == SceneType.OUTDOOR,
            crowdedness=await scene_loader.get_crowdedness(location),
            stamina=int(state.get("stamina", 100)),
            mood=str(state.get("mood") or "calm"),
        )
        return max(1, min(_MAX_DYNAMIC_DURATION, adjusted))

    @trace_span("action.execute")
    async def _execute_action(
        self,
        character_id: UUID,
        decision: DecisionResult,
        context: dict[str, Any],
        *,
        lock_lost: asyncio.Event | None = None,
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
            lock_lost: 看门狗失锁信号；缺省时视为未启用失锁闸口（保持既有直接调用方兼容）。
                置位后在入口、chat_with 关系/记忆写入前、PG 事务前与 Redis 镜像写入前
                四处中止，保证失锁后不再发生任何 PG/Redis 状态写入——含 chat_with 的
                关系/记忆写入与工具暂存记忆（H10/R5-M6/R5-L11）
        """
        lost = lock_lost if lock_lost is not None else asyncio.Event()
        # H10 闸口（入口）：失锁后不生成对话、不做移动校验，直接放弃本次执行
        if lost.is_set():
            logger.warning("character_tick_aborted_lock_lost", character_id=str(character_id))
            return

        start_perf = time.perf_counter()
        action_def = self.registry.get(decision.action)
        if not action_def:
            logger.error("action_not_found", action=decision.action)

            ACTION_EXECUTION_TOTAL.labels(action_id=decision.action, status="failed").inc()
            return

        # 多智能体交互：chat_with 需要生成对话、更新关系、为双方写记忆
        # 在状态变更前执行，确保对话内容能写入 ActionRecord.result
        chat_dialogue: str | None = None
        if decision.action == "chat_with":
            chat_dialogue = await self._handle_character_chat(character_id, decision, context, lock_lost=lost)
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

                    ACTION_EXECUTION_TOTAL.labels(action_id="chat_with", status="failed").inc()
                    return

        # 群体动力学·群活动：生成集体叙事并为所有参与者写共同经历记忆
        group_narrative: str | None = None
        if decision.action == "group_activity":
            group_narrative = await self._handle_group_activity(character_id, decision, context)
            if group_narrative is None:
                logger.warning(
                    "group_activity_failed_fallback_to_wait",
                    character_id=str(character_id),
                )
                decision = decision.model_copy(update={"action": "wait", "params": {}})
                action_def = self.registry.get(decision.action)
                if action_def is None:
                    logger.error("fallback_wait_action_not_found", character_id=str(character_id))

                    ACTION_EXECUTION_TOTAL.labels(action_id="group_activity", status="failed").inc()
                    return

        # move 决策先经 MovementSystem 校验（目标存在且连通、场景开放），失败降级为 wait。
        # LLM 幻觉的不存在场景在此被拦截，不再直接写入位置
        move_total_minutes: int | None = None
        if decision.action == "move":
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

                    ACTION_EXECUTION_TOTAL.labels(action_id="move", status="failed").inc()
                    return
            else:
                target = str(decision.params.get("target_scene") or "")
                current_location = str(context["state"].get("location") or "")
                move_result = await movement_system.calculate_move(
                    current_location,
                    target,
                    hour=self._current_world_hour(context),
                    is_workday=self._current_is_workday(context),
                    weather_move_multiplier=self._weather_move_multiplier(context),
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

                        ACTION_EXECUTION_TOTAL.labels(action_id="move", status="failed").inc()
                        return
                else:
                    move_total_minutes = move_result.total_minutes

        # 计算状态变更：
        # - move 使用移动矩阵的真实耗时（已在 MovementSystem 内乘天气移动倍率，
        #   不再经 DurationCalculator 二次修正——天气对移动只计费一次）
        # - 其余动作在基础/动态耗时之上叠加天气/拥挤/体力/情绪修正（round-6 M9c）
        # - LLM 动态时长仅在 Action 声明 allow_dynamic_duration 时生效，防止任意改时长
        if move_total_minutes is not None:
            duration = move_total_minutes
        else:
            if action_def.allow_dynamic_duration and decision.duration:
                duration = decision.duration
            else:
                duration = action_def.duration_minutes
            duration = await self._apply_duration_modifiers(context, duration)
        new_state = context["state"].copy()

        # 应用资源变更（使用 apply_cost_fields 辅助函数）

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
        # 动作集合与恢复量外置为配置（R6-L16b），无需改码即可调整
        if decision.action in settings.solo_recovery_actions:
            cur_se = int(new_state.get("social_energy", 0) or 0)
            new_state["social_energy"] = min(100, cur_se + settings.solo_recovery_social_energy_boost)

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

        # H10 闸口（PG 事务前）：决策/对话生成耗时可能横跨整个锁 TTL，
        # 期间锁可能已被他实例接走，此时写入即 double-tick
        if lost.is_set():
            logger.warning("character_tick_aborted_lock_lost", character_id=str(character_id))
            return

        # 事务化执行
        try:
            async with db.session() as session:
                action_repo = ActionRepository(session)
                char_repo = CharacterRepository(session)

                # 写入行为记录
                # chat_with 时附带对话内容与对方角色 ID（供回放与关系溯源）
                related_ids: list[UUID] = []
                if decision.action == "chat_with":
                    target_id = decision.params.get("target_character_id")
                    if target_id:
                        related_ids = [UUID(str(target_id))]

                record = ActionRecord(
                    character_id=character_id,
                    action_id=action_def.id,
                    action_name=action_def.name,
                    params=decision.params,
                    reason=decision.reason,
                    result=chat_dialogue or group_narrative,
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

                # 应用 LLM 计划变更（planChanges 落库，审查 §五-P1 死功能修复）
                if decision.plan_changes:
                    plan_repo = PlanRepository(session)
                    await PlanChangeApplier.apply_changes(plan_repo, character_id, decision.plan_changes)

                # P1-13：计划-行动对账——LLM 未显式汇报进度时，按标题与本次
                # 行为的字符重叠启发式推进，抑制「计划进度与实际行为无限漂移」。
                # wait 不是有效行动证据；推进为尽力而为，失败仅告警不回滚主事务
                # （与 _apply_plan_changes 的容错哲学一致）
                if settings.plan_auto_progress_enabled and decision.action != "wait":
                    try:
                        plan_repo = PlanRepository(session)
                        await PlanChangeApplier.auto_progress(plan_repo, character_id, decision)
                    except Exception as e:
                        logger.warning(
                            "plan_auto_progress_failed",
                            character_id=str(character_id),
                            error=str(e),
                        )

                # 应用 LLM 新建计划（层级体系 B3：character_id 服务端绑定，天然防越权）
                if decision.create_plan_changes:
                    plan_repo = PlanRepository(session)
                    await PlanChangeApplier.create_plans(plan_repo, character_id, decision.create_plan_changes)

                # 应用 ReAct 阶段暂存的工具产物（关系增量 R4-M11 / 工具记忆 R5-L11）：
                # 与 ActionRecord 同事务提交，任一失败整体回滚
                await self._apply_pending_artifacts(session, character_id, context)

            # H10 闸口（Redis 镜像写入前）：PG 已提交但此刻失锁则停止写 Redis，
            # 漂移交由 reconcile 的 pg_advanced 仲裁修复，避免覆盖接管者刚写入的新状态。
            # 此闸口同时挡住其后的场景在场人数记账
            if lost.is_set():
                logger.warning("character_tick_aborted_lock_lost", character_id=str(character_id))
                return

            # 更新 Redis 实时状态（P1-2：失败立即重试一次并进优先对账队列，
            # 不再被动等待最长一个全量对账周期）
            await self._write_redis_state_with_repair(self.redis, character_id, new_state)

            # 位置变化时经 SceneLoader 单一入口记账（成员名单 + 在场计数缓存）
            old_location = context["state"].get("location")
            new_location = new_state.get("location")
            scene_loader = get_scene_loader()
            if decision.action == "move" and new_location and old_location != new_location:
                if scene_loader is not None:
                    await scene_loader.record_movement(str(character_id), str(old_location), str(new_location))
                else:
                    # loader 缺失时退化为仅维护计数缓存（拥挤度数据源不能断）
                    if old_location:
                        await self.redis.hincrby(VISITORS_KEY, str(old_location), -1)
                    await self.redis.hincrby(VISITORS_KEY, str(new_location), 1)

            ACTION_EXECUTION_TOTAL.labels(action_id=decision.action, status="success").inc()
            ACTION_EXECUTION_DURATION.labels(action_id=decision.action).observe(time.perf_counter() - start_perf)

            logger.info(
                "action_executed",
                character_id=str(character_id),
                action=decision.action,
                duration=duration,
            )
        except Exception:
            ACTION_EXECUTION_TOTAL.labels(action_id=decision.action, status="failed").inc()
            raise

    @staticmethod
    @staticmethod
    async def _write_redis_state_with_repair(redis: Any, character_id: UUID, new_state: dict[str, Any]) -> None:
        """写 Redis 镜像；失败重试一次后把角色送入优先对账队列（P1-2）"""
        from src.core.reconcile import request_character_repair

        mapping = encode_state_mapping(new_state)
        try:
            await redis.hset(f"char:{character_id}:state", mapping=mapping)
            return
        except Exception as first_error:
            logger.warning(
                "redis_state_write_failed_retrying",
                character_id=str(character_id),
                error=str(first_error),
            )
        await asyncio.sleep(1.0)
        try:
            await redis.hset(f"char:{character_id}:state", mapping=mapping)
            logger.info("redis_state_write_recovered", character_id=str(character_id))
        except Exception as second_error:
            logger.error(
                "redis_state_write_failed_enqueued_repair",
                character_id=str(character_id),
                error=str(second_error),
                exc_info=True,
            )
            await request_character_repair(redis, character_id)

    @staticmethod
    def _build_memory_content(character_name: str, state: dict[str, Any], decision: DecisionResult) -> str:
        """生成叙事化记忆正文（P1-5）

        此前固定句式「{name}在{location}执行了{action}。理由：{reason}」
        让所有记忆向量语义雷同、检索区分度低。按动作类别套用差异化
        叙事骨架，理由自然融入句中而非标签式拼接。
        """
        location = state.get("location") or "路上"
        action = decision.action
        reason = (decision.reason or "").strip().rstrip("。.")

        _NARRATIVE_SKELETONS = {
            "move": "{name}动身前往{location}。{reason}",
            "chat": "{name}在{location}和人聊了会儿天。{reason}",
            "social": "{name}在{location}参与了一次社交互动。{reason}",
            "eat": "{name}在{location}吃了点东西。{reason}",
            "sleep": "{name}回到{location}休息了。{reason}",
            "rest": "{name}在{location}放松了一会儿。{reason}",
            "work": "{name}在{location}忙工作。{reason}",
            "study": "{name}在{location}认真学习。{reason}",
            "shop": "{name}在{location}逛了逛，买了些东西。{reason}",
            "play": "{name}在{location}玩得很开心。{reason}",
        }
        skeleton = _NARRATIVE_SKELETONS.get(action)
        if skeleton:
            return skeleton.format(name=character_name, location=location, reason=reason)
        if reason:
            return f"{character_name}在{location}{action}。起因是：{reason}"
        return f"{character_name}在{location}{action}。"

    @trace_span("memory.write")
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

        memory_content = self._build_memory_content(character.name, state, decision)

        # 写入记忆（需要 db session）
        async with db.session() as session:
            mem_repo = MemoryRepository(session)
            ref_repo = ReflectionRepository(session)

            # 创建服务实例（reflection 带 redis 供重大事件冷却，round-7 F1）
            episode_service = EpisodeService(self.llm, mem_repo, prompts=self.prompts)
            reflection_service = ReflectionService(self.llm, mem_repo, ref_repo, prompts=self.prompts, redis=self.redis)

            # P1-7：社交类基础分从 7 降到 5——此前撞上「importance>=7 永久保留」
            # 策略导致全部社交记忆不可清理；强情绪仍可经下方关键词修正回升，
            # LLM 评分开启时由其给出更精准的分值；基础分值外置为配置（R6-L16c）
            base_importance = settings.action_base_importance.get(decision.action, 5)
            # 如果理由中包含情绪关键词，提升重要性
            # 情绪关键词列表保持为模块常量（数据而非魔法数），提升值外置为配置
            _EMOTION_KEYWORDS = ["开心", "兴奋", "生气", "难过", "惊讶", "重要", "特别"]
            reason_lower = (decision.reason or "").lower()
            if any(kw in reason_lower for kw in _EMOTION_KEYWORDS):
                base_importance = min(10, base_importance + settings.action_emotion_importance_boost)
            importance = max(1, min(10, base_importance))

            # 群体动力学·共同经历：同场景在场者写入 related_characters，
            # 激活预留字段供「共同经历查询/传闻溯源」使用
            nearby = context.get("nearby_characters") or []
            related_ids = [UUID(n["id"]) for n in nearby if n.get("id")]

            await episode_service.create_episode(
                character_id,
                memory_content,
                action_id=decision.action,
                location=state.get("location"),
                importance=importance,
                character_name=character.name,
                reason=decision.reason,
                mood=state.get("mood"),
                related_characters=related_ids,
            )

            # 检查反思（数量阈值 + 重大事件即时触发，round-7 F1）
            await reflection_service.check_and_reflect_if_major(character_id, importance)
            await reflection_service.check_and_reflect(character_id)

        logger.debug(
            "memory_created",
            character_id=str(character_id),
            action=decision.action,
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
