"""src/observability/langfuse_integration.py 单元测试

覆盖：
- get_langfuse() - 获取 Langfuse 客户端（未配置时返回 None）
- record_llm_trace() - 独立记录函数（优雅降级 + 参数正确调用）
- trace_llm_call(name) 装饰器（透传 + 返回值不变）

使用 unittest.mock 模拟 Langfuse 客户端，不连接真实 Langfuse 服务器。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.observability import langfuse_integration
from src.observability.langfuse_integration import (
    get_langfuse,
    record_llm_trace,
    trace_llm_call,
)


@pytest.fixture(autouse=True)
def reset_langfuse_client():
    """每个测试前后重置全局 langfuse 客户端，避免测试间状态泄漏"""
    old = langfuse_integration._langfuse_client
    langfuse_integration._langfuse_client = None
    yield
    langfuse_integration._langfuse_client = old


@pytest.fixture
def unconfigured_langfuse():
    """模拟 Langfuse 未配置（host/public_key/secret_key 均为 None）

    项目 .env 可能已配置 langfuse，此 fixture 通过 patch settings
    强制让 setup_langfuse 返回 None，用于测试优雅降级路径。
    """
    with patch.object(langfuse_integration.settings, "langfuse_host", None):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", None):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", None):
                yield


# ---------------------------------------------------------------------------
# get_langfuse
# ---------------------------------------------------------------------------


def test_get_langfuse_returns_none_when_not_configured(unconfigured_langfuse):
    """未配置时返回 None（settings 中 langfuse_host/public_key/secret_key 可能为 None）"""
    client = get_langfuse()
    assert client is None


def test_get_langfuse_returns_none_when_host_missing():
    """langfuse_host 为 None 时返回 None"""
    with patch.object(langfuse_integration.settings, "langfuse_host", None):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", "pk"):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", "sk"):
                client = get_langfuse()
                assert client is None


def test_get_langfuse_returns_none_when_public_key_missing():
    """langfuse_public_key 为 None 时返回 None"""
    with patch.object(langfuse_integration.settings, "langfuse_host", "http://localhost"):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", None):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", "sk"):
                client = get_langfuse()
                assert client is None


def test_get_langfuse_returns_none_when_secret_key_missing():
    """langfuse_secret_key 为 None 时返回 None"""
    with patch.object(langfuse_integration.settings, "langfuse_host", "http://localhost"):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", "pk"):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", None):
                client = get_langfuse()
                assert client is None


def test_get_langfuse_returns_cached_client():
    """已初始化的客户端被缓存，重复调用返回同一实例"""
    mock_client = MagicMock()
    langfuse_integration._langfuse_client = mock_client
    # 此时不应再调用 setup_langfuse
    with patch.object(langfuse_integration, "setup_langfuse") as mock_setup:
        client = get_langfuse()
        assert client is mock_client
        mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# record_llm_trace
# ---------------------------------------------------------------------------


def test_record_llm_trace_no_error_when_uninitialized(unconfigured_langfuse):
    """Langfuse 未初始化时不报错（优雅降级）"""
    assert get_langfuse() is None
    # 不应抛出异常
    record_llm_trace(
        prompt="test prompt",
        response="test response",
        model="gpt-4o-mini",
        tokens=100,
        cost=0.001,
        duration=1.5,
    )


def test_record_llm_trace_with_error_param_when_uninitialized(unconfigured_langfuse):
    """未初始化时即使传入 error 参数也不报错"""
    assert get_langfuse() is None
    record_llm_trace(
        prompt="test",
        response="",
        model="gpt-4o-mini",
        tokens=0,
        cost=0.0,
        duration=1.0,
        error="ValueError: something",
    )


def test_record_llm_trace_calls_client_with_correct_params():
    """传入参数正确调用 Langfuse 客户端"""
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace
    langfuse_integration._langfuse_client = mock_client

    record_llm_trace(
        prompt="test prompt",
        response="test response",
        model="gpt-4o-mini",
        tokens=100,
        cost=0.001,
        duration=1.5,
    )

    mock_client.trace.assert_called_once_with(name="llm_call")
    mock_trace.generation.assert_called_once()
    call_kwargs = mock_trace.generation.call_args.kwargs
    assert call_kwargs["name"] == "llm_call"
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["input"] == "test prompt"
    assert call_kwargs["output"] == "test response"
    assert call_kwargs["usage"]["total_tokens"] == 100
    assert call_kwargs["metadata"]["cost_usd"] == 0.001
    assert call_kwargs["metadata"]["duration_seconds"] == 1.5


def test_record_llm_trace_with_error_records_error_level():
    """error 参数非 None 时使用 ERROR level 记录"""
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace
    langfuse_integration._langfuse_client = mock_client

    record_llm_trace(
        prompt="test",
        response="",
        model="gpt-4o-mini",
        tokens=0,
        cost=0.0,
        duration=1.0,
        error="ValueError: something went wrong",
    )

    mock_trace.generation.assert_called_once()
    call_kwargs = mock_trace.generation.call_args.kwargs
    assert call_kwargs["level"] == "ERROR"
    assert call_kwargs["output"] is None
    assert call_kwargs["status_message"] == "ValueError: something went wrong"
    assert call_kwargs["metadata"]["error"] == "ValueError: something went wrong"


def test_record_llm_trace_truncates_long_prompt():
    """超长 prompt 被截断"""
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace
    langfuse_integration._langfuse_client = mock_client

    long_prompt = "x" * 3000  # 超过 _MAX_TEXT_LENGTH (2000)
    record_llm_trace(
        prompt=long_prompt,
        response="resp",
        model="m",
        tokens=1,
        cost=0.0,
        duration=0.1,
    )

    call_kwargs = mock_trace.generation.call_args.kwargs
    assert len(call_kwargs["input"]) <= 2000 + len("...[truncated]")
    assert call_kwargs["input"].endswith("...[truncated]")


def test_record_llm_trace_client_exception_swallowed():
    """客户端抛异常时被捕获，不影响调用方（不抛异常）"""
    mock_client = MagicMock()
    mock_client.trace.side_effect = RuntimeError("langfuse down")
    langfuse_integration._langfuse_client = mock_client

    # 不应抛出异常
    record_llm_trace(
        prompt="test",
        response="resp",
        model="m",
        tokens=1,
        cost=0.0,
        duration=0.1,
    )


# ---------------------------------------------------------------------------
# trace_llm_call 装饰器
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_llm_call_passthrough_when_uninitialized(unconfigured_langfuse):
    """Langfuse 未初始化时直接透传（不影响业务）"""
    assert get_langfuse() is None

    @trace_llm_call("test_call")
    async def mock_llm_call(prompt: str) -> str:
        return "response: " + prompt

    result = await mock_llm_call("hello")
    assert result == "response: hello"


@pytest.mark.asyncio
async def test_trace_llm_call_preserves_return_value(unconfigured_langfuse):
    """装饰 async 函数后正常执行，返回值不变"""
    assert get_langfuse() is None

    @trace_llm_call("test_call")
    async def mock_llm_call(prompt: str) -> str:
        return "test_response"

    result = await mock_llm_call("test")
    assert result == "test_response"


@pytest.mark.asyncio
async def test_trace_llm_call_with_mock_client():
    """Langfuse 初始化后正确记录 generation"""
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace
    langfuse_integration._langfuse_client = mock_client

    @trace_llm_call("character_chat")
    async def mock_llm_call(prompt: str, model: str = "chat") -> str:
        return "response"

    result = await mock_llm_call("hello", model="gpt-4o-mini")
    assert result == "response"

    mock_client.trace.assert_called_once_with(name="character_chat")
    mock_trace.generation.assert_called_once()
    call_kwargs = mock_trace.generation.call_args.kwargs
    assert call_kwargs["name"] == "character_chat"
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["input"] == "hello"
    assert call_kwargs["output"] == "response"


@pytest.mark.asyncio
async def test_trace_llm_call_records_exception_and_reraises():
    """被装饰函数抛异常时记录 ERROR 并 re-raise"""
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace
    langfuse_integration._langfuse_client = mock_client

    @trace_llm_call("failing_call")
    async def failing_llm_call(prompt: str) -> str:
        raise RuntimeError("LLM failed")

    with pytest.raises(RuntimeError, match="LLM failed"):
        await failing_llm_call("test")

    mock_trace.generation.assert_called_once()
    call_kwargs = mock_trace.generation.call_args.kwargs
    assert call_kwargs["level"] == "ERROR"
    assert "RuntimeError: LLM failed" in call_kwargs["status_message"]


@pytest.mark.asyncio
async def test_trace_llm_call_preserves_function_metadata(unconfigured_langfuse):
    """装饰器保留原函数元信息（functools.wraps）"""
    assert get_langfuse() is None

    @trace_llm_call("test_call")
    async def documented_llm_call(prompt: str) -> str:
        """This is a docstring."""
        return "ok"

    assert documented_llm_call.__name__ == "documented_llm_call"
    assert documented_llm_call.__doc__ == "This is a docstring."


@pytest.mark.asyncio
async def test_trace_llm_call_extracts_prompt_from_args():
    """装饰器从位置参数提取 prompt"""
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace
    langfuse_integration._langfuse_client = mock_client

    @trace_llm_call("extract_test")
    async def llm_func(prompt: str) -> str:
        return "resp"

    await llm_func("my prompt text")
    call_kwargs = mock_trace.generation.call_args.kwargs
    assert call_kwargs["input"] == "my prompt text"
