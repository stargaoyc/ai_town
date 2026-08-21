"""src/cost_control/circuit_breaker.py 单元测试

使用 unittest.mock.AsyncMock 模拟 Redis，不连接真实 Redis。
"""

import time
from unittest.mock import AsyncMock

import pytest

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
