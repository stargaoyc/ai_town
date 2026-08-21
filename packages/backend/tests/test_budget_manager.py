"""src/cost_control/budget_manager.py 单元测试

使用 unittest.mock.AsyncMock 模拟 Redis，不连接真实 Redis。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cost_control.budget_manager import BudgetExceeded, BudgetManager


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.eval = AsyncMock()
    return redis


@pytest.fixture
def manager(mock_redis: AsyncMock) -> BudgetManager:
    return BudgetManager(mock_redis, daily_budget_usd=10.0, warning_threshold=0.8)


# ---------------------------------------------------------------------------
# get_today_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_today_usage_empty_returns_zeros(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {}
    usage = await manager.get_today_usage()
    assert usage == {"tokens": 0, "cost": 0.0, "count": 0}


@pytest.mark.asyncio
async def test_get_today_usage_returns_stored_values(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {
        "tokens": "1500",
        "cost": "0.25",
        "count": "3",
    }
    usage = await manager.get_today_usage()
    assert usage == {"tokens": 1500, "cost": 0.25, "count": 3}


@pytest.mark.asyncio
async def test_get_today_usage_partial_fields(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """缺失字段应回退为 0"""
    mock_redis.hgetall.return_value = {"tokens": "100"}
    usage = await manager.get_today_usage()
    assert usage == {"tokens": 100, "cost": 0.0, "count": 0}


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_usage_returns_updated_totals(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[1500, 0.25, 1, None])
    mock_redis.pipeline = MagicMock(return_value=pipe)

    usage = await manager.record_usage(tokens=1500, cost=0.25)
    assert usage == {"tokens": 1500, "cost": 0.25, "count": 1}
    # 管道命令调用正确
    pipe.hincrby.assert_any_call(manager._today_key(), "tokens", 1500)
    pipe.hincrbyfloat.assert_called_once_with(manager._today_key(), "cost", 0.25)
    pipe.expire.assert_called_once()
    pipe.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_budget_under_warning(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """未超 80%：exceeded=False, warning=False"""
    mock_redis.hgetall.return_value = {"tokens": "1000", "cost": "5.0", "count": "2"}
    status = await manager.check_budget()
    assert status["exceeded"] is False
    assert status["warning"] is False
    assert status["used"] == 5.0
    assert status["budget"] == 10.0
    assert status["remaining"] == 5.0
    assert status["ratio"] == 0.5


@pytest.mark.asyncio
async def test_check_budget_at_warning_threshold(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """达到 80%：warning=True, exceeded=False"""
    mock_redis.hgetall.return_value = {"tokens": "1000", "cost": "8.0", "count": "2"}
    status = await manager.check_budget()
    assert status["warning"] is True
    assert status["exceeded"] is False
    assert status["ratio"] == 0.8


@pytest.mark.asyncio
async def test_check_budget_exceeded(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """超过 100%：exceeded=True"""
    mock_redis.hgetall.return_value = {"tokens": "1000", "cost": "10.0", "count": "2"}
    status = await manager.check_budget()
    assert status["exceeded"] is True
    assert status["warning"] is True


@pytest.mark.asyncio
async def test_check_budget_zero_usage(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """无用量：全部安全"""
    mock_redis.hgetall.return_value = {}
    status = await manager.check_budget()
    assert status["exceeded"] is False
    assert status["warning"] is False
    assert status["used"] == 0.0
    assert status["remaining"] == 10.0


# ---------------------------------------------------------------------------
# check_and_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_record_success(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """未超预算：正常记录（eval 执行），不抛异常"""
    mock_redis.eval.return_value = [0, 1500, 0.25, 1]
    await manager.check_and_record(tokens=1500, cost=0.25)
    mock_redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_and_record_exceeded_raises(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """超预算：抛 BudgetExceeded，不记录"""
    mock_redis.eval.return_value = [1, 1000, 9.5, 5]
    with pytest.raises(BudgetExceeded) as exc_info:
        await manager.check_and_record(tokens=1500, cost=1.0)
    assert exc_info.value.used == 9.5
    assert exc_info.value.budget == 10.0
    assert exc_info.value.remaining == 0.5


@pytest.mark.asyncio
async def test_check_and_record_exceeded_does_not_record(manager: BudgetManager, mock_redis: AsyncMock) -> None:
    """超预算时仅调用 eval（原子检查），不额外写入"""
    mock_redis.eval.return_value = [1, 1000, 9.5, 5]
    with pytest.raises(BudgetExceeded):
        await manager.check_and_record(tokens=1500, cost=1.0)
    # eval 被调用一次（原子检查+记录在脚本内），不应有额外的 pipeline/hincrby
    mock_redis.eval.assert_awaited_once()
    assert not mock_redis.pipeline.called
