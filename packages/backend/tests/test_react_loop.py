"""ReAct 工具调用循环回归测试（R4-H1）。

修复前缺陷：_decide 的候选校验把 use_tool 改写为 wait，循环守卫永远首轮 break，
工具调用自特性落地起即为死代码。本文件锁定：
- _resolve_action_id 对 use_tool 保留字的豁免
- ToolRegistry 必填参数校验返回失败观察而非抛异常
- _run_react_loop 端到端：执行工具 → 观察回注 → 最终决策落地 / 轮次上限强制降级
"""

from typing import Any, cast
from uuid import UUID

from pytest import MonkeyPatch
from redis.asyncio import Redis

from src.actions import Action, ActionCategory, DecisionResult
from src.core.character.tick import CharacterTickEngine, _resolve_action_id
from src.llm import LLMClient, PromptTemplates
from src.tools.registry import TOOL_REGISTRY

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


def _action(action_id: str) -> Action:
    return Action(id=action_id, name=action_id, category=ActionCategory.LIFE)


class FakeRedis:
    async def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        pass


def _make_engine() -> CharacterTickEngine:
    from src.actions import ActionRegistry

    return CharacterTickEngine(
        redis=cast("Redis", FakeRedis()),
        registry=ActionRegistry(),
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
    )


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

    async def fake_execute_tool(cid: UUID, decision: DecisionResult, ctx: dict[str, Any]) -> dict[str, Any]:
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

    async def fake_execute_tool(cid: UUID, decision: DecisionResult, ctx: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "result": {}, "state_mutating": False}

    monkeypatch.setattr(engine, "_decide", always_use_tool)
    monkeypatch.setattr(engine, "_execute_tool", fake_execute_tool)

    final = await engine._run_react_loop(
        _CHARACTER_ID, {}, [_action("wait")], DecisionResult(action="use_tool", reason="loop")
    )

    assert final.action == "wait"


async def test_registry_required_params_declared_for_all_tools() -> None:
    """注册表不变量：每个工具都必须显式声明 required_params（缺失即静默放行）"""
    for full_name, meta in TOOL_REGISTRY.items():
        assert "required_params" in meta, f"{full_name} 缺少 required_params 声明"
