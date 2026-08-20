"""P0-5 回归测试：成本控制统一挂载到 LLMClient.chat / structured_output

修复目标（docs/design-improvement-and-fixes.md P0-5）：
- 熔断器 + 日预算检查从 messaging/service.py 手工接入改为 LLMClient 统一挂载
- Tick 等全部 LLM 调用路径（decision/chat_with/reflection/episode/proactive_sharing/diary）
  均受成本控制约束
- 未初始化成本控制单例时（如 embedding worker 独立进程）LLM 调用正常降级
"""

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from src.cost_control import BudgetExceeded, CircuitOpen, set_circuit_breaker
from src.cost_control.budget_manager import set_budget_manager
from src.llm.client import LLMClient


@pytest.fixture(autouse=True)
def _reset_cost_control_singletons():
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

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs) -> None:
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
    def __init__(self, content: str, metadata: dict | None = None) -> None:
        self.content = content
        self.response_metadata = metadata or {}


class FakeStructuredLLM:
    def __init__(self, schema: type[BaseModel]) -> None:
        self.schema = schema
        self.calls = 0

    async def ainvoke(self, prompt: str) -> BaseModel:
        self.calls += 1
        return self.schema(action="move", reason="test")


class FakeChatLLM:
    def __init__(
        self,
        response: FakeChatResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def ainvoke(self, prompt: str | list) -> FakeChatResponse:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response

    def with_structured_output(self, schema: type[BaseModel]) -> FakeStructuredLLM:
        return FakeStructuredLLM(schema)


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
    client.chat_llm = fake_llm  # type: ignore[assignment]
    return client


def _today_key() -> str:
    return f"llm:cost:{datetime.now(UTC).strftime('%Y-%m-%d')}"


async def test_uninitialized_cost_control_skips_gracefully():
    # 不调用 set_budget_manager / set_circuit_breaker（模拟 embedding worker 独立进程）
    fake_llm = FakeChatLLM(response=FakeChatResponse("hi"))
    client = _make_client(fake_llm)

    result = await client.chat("hello")

    assert result == "hi"
    assert fake_llm.calls == 1


async def test_chat_circuit_open_raises_and_skips_llm():
    redis = FakeRedis()
    set_circuit_breaker(redis, failure_threshold=1, recovery_timeout=60)
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


async def test_chat_budget_exceeded_raises_and_skips_llm():
    redis = FakeRedis()
    set_budget_manager(redis, daily_budget_usd=1.0)
    # 预置已用成本 = 预算（used >= budget）
    await redis.hset(_today_key(), mapping={"tokens": "1000", "cost": "1.0", "count": "1"})
    fake_llm = FakeChatLLM(response=FakeChatResponse("hi"))
    client = _make_client(fake_llm)

    with pytest.raises(BudgetExceeded):
        await client.chat("hello")

    assert fake_llm.calls == 0


async def test_chat_success_records_usage():
    redis = FakeRedis()
    set_budget_manager(redis, daily_budget_usd=10.0)
    set_circuit_breaker(redis, failure_threshold=5, recovery_timeout=60)
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


async def test_chat_failure_records_breaker_failure():
    redis = FakeRedis()
    set_circuit_breaker(redis, failure_threshold=2, recovery_timeout=60)
    fake_llm = FakeChatLLM(error=RuntimeError("boom"))
    client = _make_client(fake_llm)

    with pytest.raises(RuntimeError):
        await client.chat("hello")

    state = await redis.hgetall("llm:circuit_breaker")
    assert state["state"] == "CLOSED"
    assert state["failure_count"] == "1"


async def test_chat_failure_reaches_threshold_opens_breaker():
    redis = FakeRedis()
    set_circuit_breaker(redis, failure_threshold=1, recovery_timeout=60)
    fake_llm = FakeChatLLM(error=RuntimeError("boom"))
    client = _make_client(fake_llm)

    with pytest.raises(RuntimeError):
        await client.chat("hello")

    state = await redis.hgetall("llm:circuit_breaker")
    assert state["state"] == "OPEN"
    assert state["failure_count"] == "1"


async def test_structured_output_circuit_open_raises():
    redis = FakeRedis()
    set_circuit_breaker(redis, failure_threshold=1, recovery_timeout=60)
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


async def test_structured_output_success_records_usage():
    redis = FakeRedis()
    set_budget_manager(redis, daily_budget_usd=10.0)
    fake_llm = FakeChatLLM()
    client = _make_client(fake_llm)

    result = await client.structured_output("decide", _DECISION_SCHEMA)

    assert result == {"action": "move", "reason": "test"}
    usage = await redis.hgetall(_today_key())
    assert usage["count"] == "1"
    assert int(usage["tokens"]) > 0