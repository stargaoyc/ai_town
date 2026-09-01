"""src/cost_control/circuit_breaker.py 单元测试

使用 unittest.mock.AsyncMock 模拟 Redis，不连接真实 Redis。
"""

import time
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from src.cost_control.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    return redis


@pytest.fixture
def breaker(mock_redis: AsyncMock) -> CircuitBreaker:
    return CircuitBreaker(mock_redis, failure_threshold=5, recovery_timeout=60)


# ---------------------------------------------------------------------------
# can_execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_can_execute_closed_returns_true(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {}
    assert await breaker.can_execute() is True


@pytest.mark.asyncio
async def test_can_execute_open_not_timed_out_returns_false(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "OPEN",
        "failure_count": "5",
        "last_failure_time": str(time.time()),  # 刚刚失败，未超时
    }
    assert await breaker.can_execute() is False
    # 未超时不应写状态
    mock_redis.hset.assert_not_awaited()


@pytest.mark.asyncio
async def test_can_execute_open_timed_out_transitions_to_half_open(
    breaker: CircuitBreaker, mock_redis: AsyncMock
) -> None:
    old_time = time.time() - 120  # 120s > recovery_timeout(60s)
    mock_redis.hgetall.return_value = {
        "state": "OPEN",
        "failure_count": "5",
        "last_failure_time": str(old_time),
    }
    result = await breaker.can_execute()
    assert result is True
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.HALF_OPEN.value


@pytest.mark.asyncio
async def test_can_execute_half_open_returns_true(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "HALF_OPEN",
        "failure_count": "5",
        "last_failure_time": "0.0",
    }
    assert await breaker.can_execute() is True


# ---------------------------------------------------------------------------
# record_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_success_half_open_to_closed(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "HALF_OPEN",
        "failure_count": "5",
        "last_failure_time": "100.0",
    }
    await breaker.record_success()
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.CLOSED.value
    assert mapping["failure_count"] == "0"


@pytest.mark.asyncio
async def test_record_success_closed_resets_failure_count(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "CLOSED",
        "failure_count": "3",
        "last_failure_time": "100.0",
    }
    await breaker.record_success()
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.CLOSED.value
    assert mapping["failure_count"] == "0"


@pytest.mark.asyncio
async def test_record_success_closed_zero_count_no_write(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    """CLOSED 且 failure_count=0 时无需写入"""
    mock_redis.hgetall.return_value = {
        "state": "CLOSED",
        "failure_count": "0",
        "last_failure_time": "0.0",
    }
    await breaker.record_success()
    mock_redis.hset.assert_not_awaited()


# ---------------------------------------------------------------------------
# record_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_failure_closed_accumulates(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "CLOSED",
        "failure_count": "2",
        "last_failure_time": "100.0",
    }
    await breaker.record_failure()
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.CLOSED.value
    assert mapping["failure_count"] == "3"


@pytest.mark.asyncio
async def test_record_failure_reaches_threshold_opens(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "CLOSED",
        "failure_count": "4",  # +1 = 5 达阈值
        "last_failure_time": "100.0",
    }
    await breaker.record_failure()
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.OPEN.value
    assert mapping["failure_count"] == "5"


@pytest.mark.asyncio
async def test_record_failure_half_open_to_open(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "HALF_OPEN",
        "failure_count": "5",
        "last_failure_time": "100.0",
    }
    await breaker.record_failure()
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.OPEN.value


@pytest.mark.asyncio
async def test_record_failure_open_refreshes_last_failure_time(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    old_time = 100.0
    mock_redis.hgetall.return_value = {
        "state": "OPEN",
        "failure_count": "5",
        "last_failure_time": str(old_time),
    }
    await breaker.record_failure()
    mock_redis.hset.assert_awaited_once()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.OPEN.value
    assert mapping["failure_count"] == "6"
    assert float(mapping["last_failure_time"]) > old_time


@pytest.mark.asyncio
async def test_record_failure_below_threshold_stays_closed(breaker: CircuitBreaker, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "state": "CLOSED",
        "failure_count": "0",
        "last_failure_time": "0.0",
    }
    await breaker.record_failure()
    mapping = mock_redis.hset.call_args.kwargs["mapping"]
    assert mapping["state"] == CircuitState.CLOSED.value
    assert mapping["failure_count"] == "1"


# ---------------------------------------------------------------------------
# probe 自过期（R9 死锁修复：HALF_OPEN 名额持有者失联后自动释放，熔断器自愈）
# ---------------------------------------------------------------------------


class ProbeLuaRedis:
    """还原 _HALF_OPEN_PROBE_LUA 语义的假 Redis：名额带时间戳，未过期拒绝、过期可重占"""

    def __init__(self, state: CircuitState, failure_count: int = 5, last_failure_time: float = 0.0) -> None:
        self.state = state
        self.failure_count = failure_count
        self.last_failure_time = last_failure_time
        self.probe_ts: float | None = None
        self.write_count = 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return {
            "state": self.state.value,
            "failure_count": str(self.failure_count),
            "last_failure_time": str(self.last_failure_time),
        }

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: Any) -> None:
        del kwargs
        self.write_count += 1
        assert mapping is not None
        if mapping.get("state") is not None:
            self.state = CircuitState(mapping["state"])
            self.failure_count = int(mapping.get("failure_count", self.failure_count))
            self.last_failure_time = float(mapping.get("last_failure_time", self.last_failure_time))

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        del script, numkeys
        key, now_raw, timeout_raw = args
        del key
        now = float(now_raw)
        timeout = float(timeout_raw)
        if self.probe_ts is not None and (now - self.probe_ts) < timeout:
            return 0  # 名额仍被持有
        self.probe_ts = now  # 过期或空闲 → 重新抢占
        return 1

    async def hdel(self, key: str, field: str) -> int:
        del key
        if field == "half_open_probe":
            self.probe_ts = None
            return 1
        return 0


@pytest.mark.asyncio
async def test_half_open_probe_fresh_rejects_concurrent_calls() -> None:
    """名额未过期时并发调用被拒绝（保持单试探语义）"""
    redis = ProbeLuaRedis(CircuitState.HALF_OPEN, last_failure_time=time.time())
    redis.probe_ts = time.time() - 10  # 10s 前抢占，未超 probe_timeout(120s)
    cb = CircuitBreaker(cast(Redis, redis), failure_threshold=5, recovery_timeout=60, probe_timeout=120)

    assert await cb.can_execute() is False


@pytest.mark.asyncio
async def test_half_open_probe_expired_reacquires_and_recovers() -> None:
    """名额过期后重新抢占成功——持有者失联（取消/崩溃）后熔断器可自愈"""
    redis = ProbeLuaRedis(CircuitState.HALF_OPEN, last_failure_time=time.time())
    redis.probe_ts = time.time() - 300  # 5 分钟前抢占，远超 probe_timeout
    cb = CircuitBreaker(cast(Redis, redis), failure_threshold=5, recovery_timeout=60, probe_timeout=120)

    assert await cb.can_execute() is True

    # 试探成功 → 熔断器恢复 CLOSED
    await cb.record_success()
    assert redis.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_probe_expired_probe_failure_reopens() -> None:
    """过期重占后试探失败 → 回到 OPEN 等待下个恢复窗口（名额已释放，不会死锁）"""
    redis = ProbeLuaRedis(CircuitState.HALF_OPEN, last_failure_time=time.time())
    redis.probe_ts = time.time() - 300
    cb = CircuitBreaker(cast(Redis, redis), failure_threshold=5, recovery_timeout=60, probe_timeout=120)

    assert await cb.can_execute() is True

    await cb.record_failure()
    assert redis.state == CircuitState.OPEN
    # 名额已释放：下个恢复窗口仍可重新抢占（关键断言：probe_ts 不再残留）
    assert redis.probe_ts is None
