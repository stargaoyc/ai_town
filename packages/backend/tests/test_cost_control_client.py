"""P0-5 回归测试：成本控制统一挂载到 LLMClient.chat / structured_output

修复目标（docs/design-improvement-and-fixes.md P0-5）：
- 熔断器 + 日预算检查从 messaging/service.py 手工接入改为 LLMClient 统一挂载
- Tick 等全部 LLM 调用路径（decision/chat_with/reflection/episode/proactive_sharing/diary）
  均受成本控制约束
- 未初始化成本控制单例时（如 embedding worker 独立进程）LLM 调用正常降级
"""

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from prometheus_client import REGISTRY
from pydantic import BaseModel
from pytest import MonkeyPatch
from redis.asyncio import Redis

from src.config import settings
from src.cost_control import BudgetExceeded, CircuitOpen, set_circuit_breaker
from src.cost_control.budget_manager import set_budget_manager
from src.llm.client import LLMClient


@pytest.fixture(autouse=True)
def _reset_cost_control_singletons() -> Iterator[None]:
    # 单例无 unset API，测试间直接重置模块级全局保证隔离
    import src.cost_control.budget_manager as bm
    import src.cost_control.circuit_breaker as cb

    bm._budget_manager = None
    cb._breaker = None
    yield


class FakeRedis:
    """支持成本控制所需操作的假 Redis（hgetall/hset/pipeline）"""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.data.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: Any) -> None:
        self.data.setdefault(key, {}).update(mapping or {})

    def pipeline(self) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self._ops: list[tuple[str, str, str, int | float]] = []

    def hincrby(self, key: str, field: str, amount: int) -> "FakePipeline":
        self._ops.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key: str, field: str, amount: float) -> "FakePipeline":
        self._ops.append(("hincrbyfloat", key, field, amount))
        return self

    def expire(self, key: str, ttl: int) -> "FakePipeline":
        self._ops.append(("expire", key, "", ttl))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for kind, key, field, amount in self._ops:
            store = self.redis.data.setdefault(key, {})
            new: int | float
            if kind == "hincrby":
                new = int(store.get(field, "0")) + int(amount)
                store[field] = str(new)
                results.append(new)
            elif kind == "hincrbyfloat":
                new = float(store.get(field, "0.0")) + float(amount)
                store[field] = str(new)
                results.append(new)
            else:  # expire
                results.append(True)
        return results


class FakeChatResponse:
    def __init__(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        self.content = content
        self.response_metadata = metadata or {}


class FakeStructuredLLM:
    def __init__(self, schema: type[BaseModel], include_raw: bool = False) -> None:
        self.schema = schema
        self.include_raw = include_raw
        self.calls = 0

    async def ainvoke(self, prompt: str) -> Any:
        self.calls += 1
        parsed = self.schema(action="move", reason="test")
        if self.include_raw:
            raw = SimpleNamespace(response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}})
            return {"raw": raw, "parsed": parsed, "parsing_error": None}
        return parsed


class FakeChatLLM:
    def __init__(
        self,
        response: FakeChatResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def ainvoke(self, prompt: str | list[Any]) -> FakeChatResponse:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response

    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> FakeStructuredLLM:
        return FakeStructuredLLM(schema, include_raw=bool(kwargs.get("include_raw")))


_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
}


def _make_client(fake_llm: FakeChatLLM) -> LLMClient:
    client = LLMClient()
    # 替换多模型源池为单源池：build_llm 恒返回注入的 fake，冷却逻辑不触发
    pool = cast(Any, SimpleNamespace())
    pool.ordered_candidates = lambda: [0]
    pool.build_llm = lambda index: fake_llm
    pool.mark_success = lambda index: None
    pool.mark_failure = lambda index: None
    client._source_pool = pool
    return client


def _today_key() -> str:
    return f"llm:cost:{datetime.now(UTC).strftime('%Y-%m-%d')}"


async def test_uninitialized_cost_control_skips_gracefully() -> None:
    # 不调用 set_budget_manager / set_circuit_breaker（模拟 embedding worker 独立进程）
    fake_llm = FakeChatLLM(response=FakeChatResponse("hi"))
    client = _make_client(fake_llm)

    result = await client.chat("hello")

    assert result == "hi"
    assert fake_llm.calls == 1


async def test_chat_circuit_open_raises_and_skips_llm() -> None:
    redis = FakeRedis()
    set_circuit_breaker(cast(Redis, redis), failure_threshold=1, recovery_timeout=60)
    # 预置 OPEN 且未超时（last_failure_time=now），can_execute 返回 False
    await redis.hset(
        "llm:circuit_breaker",
        mapping={
            "state": "OPEN",
            "failure_count": "1",
            "last_failure_time": str(time.time()),
        },
    )
    fake_llm = FakeChatLLM(response=FakeChatResponse("hi"))
    client = _make_client(fake_llm)

    with pytest.raises(CircuitOpen):
        await client.chat("hello")

    assert fake_llm.calls == 0


async def test_chat_budget_exceeded_raises_and_skips_llm() -> None:
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=1.0)
    # 预置已用成本 = 预算（used >= budget）
    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "1.0", "count": "1"})
    fake_llm = FakeChatLLM(response=FakeChatResponse("hi"))
    client = _make_client(fake_llm)

    with pytest.raises(BudgetExceeded):
        await client.chat("hello")

    assert fake_llm.calls == 0


async def test_chat_success_records_usage() -> None:
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=10.0)
    set_circuit_breaker(cast(Redis, redis), failure_threshold=5, recovery_timeout=60)
    fake_llm = FakeChatLLM(
        response=FakeChatResponse(
            "hi",
            metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
    )
    client = _make_client(fake_llm)

    result = await client.chat("hello")

    assert result == "hi"
    usage = await redis.hgetall(_today_key())
    assert usage["tokens"] == "15"
    assert usage["count"] == "1"
    assert float(usage["cost"]) > 0


async def test_chat_failure_records_breaker_failure() -> None:
    redis = FakeRedis()
    set_circuit_breaker(cast(Redis, redis), failure_threshold=2, recovery_timeout=60)
    fake_llm = FakeChatLLM(error=RuntimeError("boom"))
    client = _make_client(fake_llm)

    with pytest.raises(RuntimeError):
        await client.chat("hello")

    state = await redis.hgetall("llm:circuit_breaker")
    assert state["state"] == "CLOSED"
    assert state["failure_count"] == "1"


async def test_chat_failure_reaches_threshold_opens_breaker() -> None:
    redis = FakeRedis()
    set_circuit_breaker(cast(Redis, redis), failure_threshold=1, recovery_timeout=60)
    fake_llm = FakeChatLLM(error=RuntimeError("boom"))
    client = _make_client(fake_llm)

    with pytest.raises(RuntimeError):
        await client.chat("hello")

    state = await redis.hgetall("llm:circuit_breaker")
    assert state["state"] == "OPEN"
    assert state["failure_count"] == "1"


async def test_structured_output_circuit_open_raises() -> None:
    redis = FakeRedis()
    set_circuit_breaker(cast(Redis, redis), failure_threshold=1, recovery_timeout=60)
    await redis.hset(
        "llm:circuit_breaker",
        mapping={
            "state": "OPEN",
            "failure_count": "1",
            "last_failure_time": str(time.time()),
        },
    )
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    with pytest.raises(CircuitOpen):
        await client.structured_output("decide", _DECISION_SCHEMA)

    assert fake_llm.calls == 0


async def test_structured_output_success_records_usage() -> None:
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=10.0)
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    result = await client.structured_output("decide", _DECISION_SCHEMA)

    assert result == {"action": "move", "reason": "test"}
    usage = await redis.hgetall(_today_key())
    assert usage["count"] == "1"
    assert int(usage["tokens"]) > 0


async def test_chat_with_usage_returns_real_tokens() -> None:
    fake_llm = FakeChatLLM(
        response=FakeChatResponse(
            "hi",
            metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
    )
    client = _make_client(fake_llm)

    content, usage = await client.chat_with_usage("hello")

    assert content == "hi"
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 15
    assert usage.cost > 0


async def test_structured_output_with_usage_returns_real_tokens() -> None:
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    result, usage = await client.structured_output_with_usage("decide", _DECISION_SCHEMA)

    assert result == {"action": "move", "reason": "test"}
    assert usage.total_tokens == 15
    assert usage.cost > 0


async def test_structured_output_parse_failure_retries_once(monkeypatch: MonkeyPatch) -> None:
    """R4-M9：解析失败同 prompt 重试一次，二次成功则正常返回"""
    from pydantic import BaseModel

    import src.llm.client as client_module

    class _Parsed(BaseModel):
        action: str = "move"
        reason: str = "test"

    attempts: list[int] = []

    async def fake_invoke(pool: Any, fn: Any) -> tuple[dict[str, Any], int]:
        attempts.append(1)
        if len(attempts) == 1:
            return {"raw": None, "parsed": None, "parsing_error": "bad json"}, 0
        raw = SimpleNamespace(response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        return {"raw": raw, "parsed": _Parsed(), "parsing_error": None}, 0

    monkeypatch.setattr(client_module, "invoke_with_fallback", fake_invoke)
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    result, usage = await client.structured_output_with_usage("decide", _DECISION_SCHEMA)

    assert len(attempts) == 2
    assert result == {"action": "move", "reason": "test"}
    assert usage.total_tokens == 15


async def test_structured_output_second_parse_failure_propagates(monkeypatch: MonkeyPatch) -> None:
    """R4-M9：两次均失败时如实抛出，不无限重试"""
    from pydantic import BaseModel

    import src.llm.client as client_module

    class _Parsed(BaseModel):
        action: str = "move"
        reason: str = "test"

    async def always_fail(pool: Any, fn: Any) -> tuple[dict[str, Any], int]:
        return {"raw": None, "parsed": None, "parsing_error": "bad json"}, 0

    monkeypatch.setattr(client_module, "invoke_with_fallback", always_fail)
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    with pytest.raises(RuntimeError, match="structured_output_parse_failed"):
        await client.structured_output_with_usage("decide", _DECISION_SCHEMA)


def _llm_call_total(model: str, status: str) -> float:
    value = REGISTRY.get_sample_value("ai_town_llm_call_total", {"model": model, "status": status})
    return value or 0.0


def _chat_call_total(status: str) -> float:
    """档位体系收敛后 metrics label 统一为 settings.model_chat"""
    return _llm_call_total(settings.model_chat, status)


async def test_multimodal_structured_budget_exceeded_raises_and_skips_llm() -> None:
    """R5-H4：多模态结构化输出调用前必须过日预算检查"""
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=1.0)
    # 预置已用成本 = 预算（used >= budget）
    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "1.0", "count": "1"})
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    with pytest.raises(BudgetExceeded):
        await client.multimodal_structured_output("看看这张图", _DECISION_SCHEMA)

    assert fake_llm.calls == 0


async def test_multimodal_structured_success_records_usage_and_counters() -> None:
    """R5-H4：成功路径入账预算账本并递增 LLM 调用计数"""
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=10.0)
    calls_before = _chat_call_total("success")
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    result = await client.multimodal_structured_output("看看这张图", _DECISION_SCHEMA)

    assert result == {"action": "move", "reason": "test"}
    usage = await redis.hgetall(_today_key())
    # include_raw 替身回传真实用量 10+5，证明走的是真实用量而非 char//2 估算
    assert usage["tokens"] == "15"
    assert usage["count"] == "1"
    assert float(usage["cost"]) > 0
    assert _chat_call_total("success") == calls_before + 1


async def test_multimodal_structured_failure_traces_langfuse_error(monkeypatch: MonkeyPatch) -> None:
    """R5-H4：失败路径记熔断失败并上报 Langfuse 错误追踪"""
    import src.llm.client as client_module
    import src.observability.langfuse_tracing as tracing_module

    redis = FakeRedis()
    set_circuit_breaker(cast(Redis, redis), failure_threshold=2, recovery_timeout=60)
    errors: list[dict[str, Any]] = []

    def fake_trace_llm_error(**kwargs: Any) -> None:
        errors.append(kwargs)

    async def raise_boom(pool: Any, fn: Any) -> tuple[dict[str, Any], int]:
        raise RuntimeError("boom")

    monkeypatch.setattr(tracing_module, "trace_llm_error", fake_trace_llm_error)
    # 结构化路径经 with_structured_output 调用，FakeChatLLM.error 不生效，直接在源池调用处注入异常
    monkeypatch.setattr(client_module, "invoke_with_fallback", raise_boom)
    failed_before = _chat_call_total("failed")
    fake_llm = FakeChatLLM(error=RuntimeError("boom"))
    client = _make_client(fake_llm)

    with pytest.raises(RuntimeError):
        await client.multimodal_structured_output("看看这张图", _DECISION_SCHEMA)

    assert len(errors) == 1
    assert errors[0]["model"] == settings.model_chat
    assert isinstance(errors[0]["error"], RuntimeError)
    assert _chat_call_total("failed") == failed_before + 1
    state = await redis.hgetall("llm:circuit_breaker")
    assert state["failure_count"] == "1"


async def test_chat_with_usage_essential_bypasses_budget_exceeded() -> None:
    """round-7 P0-2：超预算时用户对话路径（essential=True）放行，小镇不停摆"""
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=1.0)
    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "1.0", "count": "1"})
    fake_llm = FakeChatLLM(
        response=FakeChatResponse(
            "hi",
            metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
    )
    client = _make_client(fake_llm)

    content, usage = await client.chat_with_usage("hello")

    assert content == "hi"
    assert usage.total_tokens == 15
    assert fake_llm.calls == 1


async def test_chat_non_essential_blocks_when_budget_exceeded() -> None:
    """round-7 P0-2：超预算时后台路径（chat, essential=False）仍拒绝"""
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=1.0)
    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "1.0", "count": "1"})
    fake_llm = FakeChatLLM(response=FakeChatResponse("hi"))
    client = _make_client(fake_llm)

    with pytest.raises(BudgetExceeded):
        await client.chat("hello")

    assert fake_llm.calls == 0


async def test_budget_tier_warning_reported() -> None:
    """round-7 P0-2：check_budget 返回分级 tier 供 Tick 循环降频"""
    redis = FakeRedis()
    set_budget_manager(cast(Redis, redis), daily_budget_usd=10.0, warning_threshold=0.8)
    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "8.0", "count": "1"})

    from src.cost_control import get_budget_manager

    status = await get_budget_manager().check_budget()

    assert status["tier"] == "warning"
    assert status["warning"] is True
    assert status["exceeded"] is False

    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "10.0", "count": "1"})
    status = await get_budget_manager().check_budget()
    assert status["tier"] == "exceeded"
