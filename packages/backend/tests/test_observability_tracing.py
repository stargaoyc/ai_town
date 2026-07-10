"""src/observability/tracing.py 单元测试

覆盖：
- get_tracer() 返回 tracer 对象
- trace_span 装饰器（async / sync）
- 异常时 span 记录异常但 re-raise
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.observability.tracing import get_tracer, trace_span


# ---------------------------------------------------------------------------
# get_tracer
# ---------------------------------------------------------------------------


def test_get_tracer_returns_object():
    """返回的 tracer 对象不为 None"""
    tracer = get_tracer()
    assert tracer is not None


def test_get_tracer_with_custom_name():
    """可以指定 tracer 名称"""
    tracer = get_tracer("custom-tracer-name")
    assert tracer is not None


def test_get_tracer_returns_same_tracer_on_repeated_calls():
    """重复调用返回可用的 tracer（不为 None）"""
    tracer1 = get_tracer()
    tracer2 = get_tracer()
    assert tracer1 is not None
    assert tracer2 is not None


# ---------------------------------------------------------------------------
# trace_span - async 函数
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_span_async_executes():
    """装饰 async 函数后正常执行"""

    @trace_span("test-async-span")
    async def async_func(x: int, y: int) -> int:
        return x + y

    result = await async_func(3, 4)
    assert result == 7


@pytest.mark.asyncio
async def test_trace_span_async_preserves_return_value():
    """装饰后函数返回值不变"""

    @trace_span("test-span")
    async def async_func() -> str:
        return "expected_value"

    result = await async_func()
    assert result == "expected_value"


@pytest.mark.asyncio
async def test_trace_span_async_with_kwargs():
    """装饰 async 函数支持 kwargs 透传"""

    @trace_span("test-span")
    async def async_func(a: int, b: int = 10) -> int:
        return a + b

    assert await async_func(5) == 15
    assert await async_func(5, b=20) == 25


@pytest.mark.asyncio
async def test_trace_span_async_records_exception_and_reraises():
    """异常时 span 记录异常但 re-raise"""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch("src.observability.tracing.get_tracer", return_value=mock_tracer):

        @trace_span("failing-span")
        async def failing_func() -> None:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await failing_func()

        mock_span.record_exception.assert_called_once()


@pytest.mark.asyncio
async def test_trace_span_async_no_exception_no_record():
    """正常执行时不调用 record_exception"""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch("src.observability.tracing.get_tracer", return_value=mock_tracer):

        @trace_span("ok-span")
        async def ok_func() -> int:
            return 42

        result = await ok_func()
        assert result == 42
        mock_span.record_exception.assert_not_called()


@pytest.mark.asyncio
async def test_trace_span_async_sets_attributes():
    """装饰 async 函数后 span 设置 code.function 与 result.type 属性"""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch("src.observability.tracing.get_tracer", return_value=mock_tracer):

        @trace_span("attr-span")
        async def my_func(x: int) -> int:
            return x * 2

        await my_func(5)

        mock_span.set_attribute.assert_any_call("code.function", "my_func")
        mock_span.set_attribute.assert_any_call("result.type", "int")


# ---------------------------------------------------------------------------
# trace_span - sync 函数
# ---------------------------------------------------------------------------


def test_trace_span_sync_executes():
    """装饰 sync 函数后正常执行"""

    @trace_span("test-sync-span")
    def sync_func(x: int, y: int) -> int:
        return x + y

    result = sync_func(3, 4)
    assert result == 7


def test_trace_span_sync_preserves_return_value():
    """装饰 sync 函数后返回值不变"""

    @trace_span("test-span")
    def sync_func() -> str:
        return "expected_value"

    result = sync_func()
    assert result == "expected_value"


def test_trace_span_sync_records_exception_and_reraises():
    """sync 函数异常时 span 记录异常但 re-raise"""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch("src.observability.tracing.get_tracer", return_value=mock_tracer):

        @trace_span("failing-sync-span")
        def failing_func() -> None:
            raise RuntimeError("sync error")

        with pytest.raises(RuntimeError, match="sync error"):
            failing_func()

        mock_span.record_exception.assert_called_once()


def test_trace_span_sync_sets_attributes():
    """装饰 sync 函数后 span 设置 code.function 属性"""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    mock_tracer.start_as_current_span.return_value.__exit__.return_value = False

    with patch("src.observability.tracing.get_tracer", return_value=mock_tracer):

        @trace_span("sync-attr-span")
        def my_sync_func(x: int) -> int:
            return x + 1

        result = my_sync_func(10)
        assert result == 11
        mock_span.set_attribute.assert_any_call("code.function", "my_sync_func")
        mock_span.set_attribute.assert_any_call("result.type", "int")


# ---------------------------------------------------------------------------
# trace_span - 保留函数元信息
# ---------------------------------------------------------------------------


def test_trace_span_preserves_function_name():
    """装饰器保留原函数 __name__（functools.wraps）"""

    @trace_span("test-span")
    async def my_named_function() -> None:
        pass

    assert my_named_function.__name__ == "my_named_function"


def test_trace_span_preserves_function_docstring():
    """装饰器保留原函数 __doc__"""

    @trace_span("test-span")
    async def documented_func() -> None:
        """This is a docstring."""

    assert documented_func.__doc__ == "This is a docstring."
