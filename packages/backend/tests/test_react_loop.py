"""ReAct 工具调用循环回归测试（R4-H1）。

修复前缺陷：_decide 的候选校验把 use_tool 改写为 wait，循环守卫永远首轮 break，
工具调用自特性落地起即为死代码。本文件锁定：
- _resolve_action_id 对 use_tool 保留字的豁免
- ToolRegistry 必填参数校验返回失败观察而非抛异常
- _run_react_loop 端到端：执行工具 → 观察回注 → 最终决策落地 / 轮次上限强制降级
- R5-M3：零工具环境不渲染工具说明段，LLM 不再被诱导输出 use_tool
- R5-L12：缺 tool_name 时合成失败观察，后续决策不再零反馈盲猜
- R6-L5：单次工具执行超时（tool_timeout_seconds）返回失败观察且循环继续；
  观察以 <observation> 分隔符包裹，截断不破坏闭合标签
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from pytest import MonkeyPatch
from redis.asyncio import Redis

from src.actions import Action, ActionCategory, DecisionResult
from src.config import settings
from src.core.character.tick import CharacterTickEngine, _resolve_action_id
from src.llm import LLMClient, PromptTemplates
from src.tools import registry as registry_module
from src.tools.registry import TOOL_REGISTRY, ToolRegistry

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


def _action(action_id: str) -> Action:
    return Action(id=action_id, name=action_id, category=ActionCategory.LIFE)


class FakeRedis:
    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        pass


class RecordingLLM:
    """记录 structured_output 收到的 Prompt 并返回预置结果"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.prompts: list[str] = []

    async def structured_output(self, prompt: str, schema: dict[str, Any], model: str = "strong") -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.result


def _make_engine(llm: LLMClient | None = None) -> CharacterTickEngine:
    from src.actions import ActionRegistry

    return CharacterTickEngine(
        redis=cast("Redis", FakeRedis()),
        registry=ActionRegistry(),
        llm=cast(LLMClient, llm),
        prompts=PromptTemplates(),
    )


def _decide_context() -> dict[str, Any]:
    return {
        "character": SimpleNamespace(name="小艾", traits={"personality": []}, backstory=None),
        "state": {"location": "cafe", "stamina": 80, "satiety": 60, "mood": "calm"},
        "world": {"world_time": "2026-08-26T10:00:00+00:00", "weather": "sunny"},
        "memories": [],
        "plans": [],
    }


def _set_enabled_tools(monkeypatch: MonkeyPatch, enabled: set[str]) -> None:
    async def fake_get_enabled_tools() -> set[str]:
        return enabled

    monkeypatch.setattr(registry_module, "get_enabled_tools", fake_get_enabled_tools)


# ---------- _resolve_action_id ----------


def test_use_tool_survives_validation() -> None:
    assert _resolve_action_id("use_tool", [_action("wait")]) == "use_tool"


def test_unknown_action_falls_back_to_wait() -> None:
    assert _resolve_action_id("fly_to_mars", [_action("wait"), _action("move")]) == "wait"


def test_valid_candidate_passes_through() -> None:
    assert _resolve_action_id("move", [_action("wait"), _action("move")]) == "move"


def test_missing_action_defaults_to_wait() -> None:
    assert _resolve_action_id(None, [_action("wait")]) == "wait"


# ---------- ToolRegistry 必填参数校验 ----------


async def test_missing_required_args_return_failure_observation(monkeypatch: MonkeyPatch) -> None:
    """缺参必须返回 success=False 的观察字典，而不是抛异常炸掉整个 Tick"""
    from src.tools import registry as registry_module

    monkeypatch.setattr(registry_module, "is_tool_enabled", _always_enabled)
    engine = _make_engine()
    result = await engine._execute_tool(
        _CHARACTER_ID,
        DecisionResult(action="use_tool", reason="x", params={"tool_name": "shop.buy_item", "tool_args": {}}),
        {"character": type("C", (), {"name": "test"})(), "state": {}, "relations": {}},
    )
    assert result is not None and result["success"] is False
    assert "item_id" in str(result.get("error"))


async def test_unknown_tool_returns_failure_observation(monkeypatch: MonkeyPatch) -> None:
    from src.tools import registry as registry_module

    monkeypatch.setattr(registry_module, "is_tool_enabled", _always_enabled)
    engine = _make_engine()
    result = await engine._execute_tool(
        _CHARACTER_ID,
        DecisionResult(action="use_tool", reason="x", params={"tool_name": "no.such_tool", "tool_args": {}}),
        {"character": type("C", (), {"name": "test"})(), "state": {}, "relations": {}},
    )
    assert result is not None and result["success"] is False


async def _always_enabled(full_name: str) -> bool:
    return True


# ---------- ReAct 循环端到端 ----------


async def test_react_loop_executes_tool_then_lands_final_action(monkeypatch: MonkeyPatch) -> None:
    """use_tool → 执行工具 → 观察回注 → 最终 wait 落地，全链路走通"""
    engine = _make_engine()
    executed_tools: list[str] = []
    observations_seen: list[list[dict[str, Any]]] = []

    decisions: list[DecisionResult] = [DecisionResult(action="wait", reason="done")]

    async def fake_decide(
        cid: UUID, ctx: dict[str, Any], cands: list[Action], obs: list[dict[str, Any]]
    ) -> DecisionResult:
        observations_seen.append([dict(o) for o in obs])
        return decisions.pop(0)

    async def fake_execute_tool(
        cid: UUID, decision: DecisionResult, ctx: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        executed_tools.append(str(decision.params["tool_name"]))
        return {"success": True, "result": {"weather": "sunny"}, "state_mutating": False}

    monkeypatch.setattr(engine, "_decide", fake_decide)
    monkeypatch.setattr(engine, "_execute_tool", fake_execute_tool)

    initial = DecisionResult(
        action="use_tool",
        reason="查天气",
        params={"tool_name": "world.get_world_info", "tool_args": {}},
    )
    final = await engine._run_react_loop(_CHARACTER_ID, {}, [_action("wait")], initial)

    assert executed_tools == ["world.get_world_info"]
    assert final.action == "wait"
    # 唯一一次再决策收到了工具观察
    assert len(observations_seen) == 1
    assert observations_seen[0][0]["tool_name"] == "world.get_world_info"
    assert observations_seen[0][0]["success"] is True


async def test_react_loop_forces_wait_after_max_iterations(monkeypatch: MonkeyPatch) -> None:
    """3 轮后仍停留在 use_tool 时强制降级为 wait"""
    engine = _make_engine()

    async def always_use_tool(
        cid: UUID, ctx: dict[str, Any], cands: list[Action], obs: list[dict[str, Any]]
    ) -> DecisionResult:
        return DecisionResult(action="use_tool", reason="loop", params={"tool_name": "world.list_scenes"})

    async def fake_execute_tool(
        cid: UUID, decision: DecisionResult, ctx: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return {"success": True, "result": {}, "state_mutating": False}

    monkeypatch.setattr(engine, "_decide", always_use_tool)
    monkeypatch.setattr(engine, "_execute_tool", fake_execute_tool)

    final = await engine._run_react_loop(
        _CHARACTER_ID, {}, [_action("wait")], DecisionResult(action="use_tool", reason="loop")
    )

    assert final.action == "wait"


# ---------- R5-M3：零工具环境不渲染工具说明段 ----------


async def test_decide_prompt_skips_tools_section_when_none_enabled(monkeypatch: MonkeyPatch) -> None:
    _set_enabled_tools(monkeypatch, set())
    llm = RecordingLLM({"action": "wait", "reason": "x"})
    engine = _make_engine(cast(LLMClient, llm))

    await engine._decide(_CHARACTER_ID, _decide_context(), [_action("wait")])

    prompt = llm.prompts[0]
    assert "[可用工具]" not in prompt
    assert "use_tool" not in prompt


async def test_decide_prompt_includes_tools_section_when_enabled(monkeypatch: MonkeyPatch) -> None:
    _set_enabled_tools(monkeypatch, {"shop.buy_item"})
    llm = RecordingLLM({"action": "wait", "reason": "x"})
    engine = _make_engine(cast(LLMClient, llm))

    await engine._decide(_CHARACTER_ID, _decide_context(), [_action("wait")])

    prompt = llm.prompts[0]
    assert "[可用工具]" in prompt
    assert "use_tool" in prompt
    assert "shop.buy_item" in prompt


# ---------- R5-L12：缺 tool_name 合成失败观察 ----------


async def test_missing_tool_name_yields_synthetic_failure_observation(monkeypatch: MonkeyPatch) -> None:
    engine = _make_engine()
    observations_seen: list[list[dict[str, Any]]] = []

    async def fake_decide(
        cid: UUID, ctx: dict[str, Any], cands: list[Action], obs: list[dict[str, Any]]
    ) -> DecisionResult:
        observations_seen.append([dict(o) for o in obs])
        return DecisionResult(action="wait", reason="done")

    async def failing_execute_tool(
        cid: UUID, decision: DecisionResult, ctx: dict[str, Any], *, lock_lost: Any = None
    ) -> None:
        return None

    monkeypatch.setattr(engine, "_decide", fake_decide)
    monkeypatch.setattr(engine, "_execute_tool", failing_execute_tool)

    final = await engine._run_react_loop(
        _CHARACTER_ID,
        {},
        [_action("wait")],
        DecisionResult(action="use_tool", reason="x", params={}),
    )

    assert final.action == "wait"
    assert observations_seen[0][0]["success"] is False
    assert observations_seen[0][0]["error"] == "missing tool_name"


async def test_synthetic_failure_observation_renders_error_into_prompt(monkeypatch: MonkeyPatch) -> None:
    """渲染层回退展示 error，下一轮决策能看到失败原因而非空结果"""
    _set_enabled_tools(monkeypatch, {"shop.buy_item"})
    llm = RecordingLLM({"action": "wait", "reason": "工具坏了，直接等待"})
    engine = _make_engine(cast(LLMClient, llm))

    await engine._decide(
        _CHARACTER_ID,
        _decide_context(),
        [_action("wait")],
        tool_observations=[{"tool_name": "", "success": False, "error": "missing tool_name"}],
    )

    assert "missing tool_name" in llm.prompts[0]


async def test_registry_required_params_declared_for_all_tools() -> None:
    """注册表不变量：每个工具都必须显式声明 required_params（缺失即静默放行）"""
    for full_name, meta in TOOL_REGISTRY.items():
        assert "required_params" in meta, f"{full_name} 缺少 required_params 声明"


# ---------- R6-L5：单次工具执行超时 ----------


async def _hang(**kwargs: Any) -> dict[str, Any]:
    """模拟挂死工具：远超任何测试超时地休眠"""
    await asyncio.sleep(60)
    return {"success": True, "result": {"late": True}}


def _register_test_tool(monkeypatch: MonkeyPatch, name: str, func: Any) -> None:
    monkeypatch.setitem(
        registry_module.TOOL_REGISTRY,
        name,
        {
            "func": func,
            "description": "测试工具",
            "llm_params": {},
            "required_params": [],
            "injected_params": {},
            "state_mutating": False,
        },
    )


async def test_tool_timeout_returns_contained_failure(monkeypatch: MonkeyPatch) -> None:
    """挂死工具必须返回 success=False 的失败观察，而不是抛异常炸掉 Tick"""
    monkeypatch.setattr(registry_module, "is_tool_enabled", _always_enabled)
    monkeypatch.setattr(settings, "tool_timeout_seconds", 0.05)
    _register_test_tool(monkeypatch, "test.hang", _hang)

    registry = ToolRegistry()
    result = await registry.call_tool_with_context("test.hang", {}, {"state": {}, "relations": {}})

    assert result["success"] is False
    assert "timeout" in str(result.get("error"))
    assert "test.hang" in str(result.get("error"))


async def test_react_loop_continues_after_tool_timeout(monkeypatch: MonkeyPatch) -> None:
    """工具超时返回失败观察后 ReAct 循环继续下一次决策，而不是中断"""
    monkeypatch.setattr(registry_module, "is_tool_enabled", _always_enabled)
    monkeypatch.setattr(settings, "tool_timeout_seconds", 0.05)
    _register_test_tool(monkeypatch, "test.hang", _hang)

    engine = _make_engine()
    observations_seen: list[list[dict[str, Any]]] = []

    async def fake_decide(
        cid: UUID, ctx: dict[str, Any], cands: list[Action], obs: list[dict[str, Any]]
    ) -> DecisionResult:
        observations_seen.append([dict(o) for o in obs])
        return DecisionResult(action="wait", reason="超时后放弃调用")

    monkeypatch.setattr(engine, "_decide", fake_decide)

    initial = DecisionResult(
        action="use_tool",
        reason="查天气",
        params={"tool_name": "test.hang", "tool_args": {}},
    )
    final = await engine._run_react_loop(
        _CHARACTER_ID,
        {"character": SimpleNamespace(name="小艾"), "state": {}, "relations": {}},
        [_action("wait")],
        initial,
    )

    assert final.action == "wait"
    assert observations_seen[0][0]["success"] is False
    assert "timeout" in str(observations_seen[0][0]["error"])


async def test_tool_without_timeout_returns_unchanged_result(monkeypatch: MonkeyPatch) -> None:
    """未触发超时时返回结构与加超时前一致"""
    monkeypatch.setattr(registry_module, "is_tool_enabled", _always_enabled)
    monkeypatch.setattr(settings, "tool_timeout_seconds", 5.0)

    async def ok_tool(**kwargs: Any) -> dict[str, Any]:
        return {"note": "ok"}

    _register_test_tool(monkeypatch, "test.ok", ok_tool)

    registry = ToolRegistry()
    result = await registry.call_tool_with_context("test.ok", {}, {"state": {}, "relations": {}})

    assert result["success"] is True
    assert result["result"] == {"note": "ok"}
    assert result["error"] is None
    assert result["state_mutating"] is False


async def test_tool_timeout_disabled_zero_allows_completion(monkeypatch: MonkeyPatch) -> None:
    """tool_timeout_seconds=0 时禁用超时，慢工具正常完成不被取消"""
    monkeypatch.setattr(registry_module, "is_tool_enabled", _always_enabled)
    monkeypatch.setattr(settings, "tool_timeout_seconds", 0.0)

    async def slow_ok(**kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"done": True}

    _register_test_tool(monkeypatch, "test.slow", slow_ok)

    registry = ToolRegistry()
    result = await registry.call_tool_with_context("test.slow", {}, {"state": {}, "relations": {}})

    assert result["success"] is True
    assert result["result"] == {"done": True}


# ---------- R6-L5：观察分隔符 ----------


async def test_observation_renders_with_complete_delimiter_after_truncation(monkeypatch: MonkeyPatch) -> None:
    """观察以 <observation> 包裹，截断发生在标签内内容上、闭合标签始终完整"""
    _set_enabled_tools(monkeypatch, {"shop.buy_item"})
    llm = RecordingLLM({"action": "wait", "reason": "x"})
    engine = _make_engine(cast(LLMClient, llm))

    long_result = "内容" * 900
    await engine._decide(
        _CHARACTER_ID,
        _decide_context(),
        [_action("wait")],
        tool_observations=[{"tool_name": "shop.buy_item", "tool_args": {}, "result": long_result, "success": True}],
    )

    prompt = llm.prompts[0]
    assert "<observation>" in prompt
    assert "</observation>" in prompt
    assert prompt.count("<observation>") == prompt.count("</observation>")
    start = prompt.index("<observation>")
    end = prompt.index("</observation>", start)
    between = prompt[start + len("<observation>") : end]
    assert len(between.split("结果: ", 1)[1]) <= 800
