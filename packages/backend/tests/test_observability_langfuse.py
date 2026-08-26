"""src/observability/langfuse_integration.py 单元测试

覆盖：
- get_langfuse() - 获取 Langfuse 客户端（未配置时返回 None）
- record_llm_trace() - 独立记录函数（优雅降级 + 参数正确调用）

使用 unittest.mock 模拟 Langfuse 客户端，不连接真实 Langfuse 服务器。
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from src.observability import langfuse_integration
from src.observability.langfuse_integration import (
    get_langfuse,
    record_llm_trace,
)


@pytest.fixture(autouse=True)
def reset_langfuse_client() -> Iterator[None]:
    """每个测试前后重置全局 langfuse 客户端，避免测试间状态泄漏"""
    old = langfuse_integration._langfuse_client
    langfuse_integration._langfuse_client = None
    yield
    langfuse_integration._langfuse_client = old


@pytest.fixture
def unconfigured_langfuse() -> Iterator[None]:
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


def test_get_langfuse_returns_none_when_not_configured(unconfigured_langfuse: None) -> None:
    """未配置时返回 None（settings 中 langfuse_host/public_key/secret_key 可能为 None）"""
    client = get_langfuse()
    assert client is None


def test_get_langfuse_returns_none_when_host_missing() -> None:
    """langfuse_host 为 None 时返回 None"""
    with patch.object(langfuse_integration.settings, "langfuse_host", None):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", "pk"):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", "sk"):
                client = get_langfuse()
                assert client is None


def test_get_langfuse_returns_none_when_public_key_missing() -> None:
    """langfuse_public_key 为 None 时返回 None"""
    with patch.object(langfuse_integration.settings, "langfuse_host", "http://localhost"):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", None):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", "sk"):
                client = get_langfuse()
                assert client is None


def test_get_langfuse_returns_none_when_secret_key_missing() -> None:
    """langfuse_secret_key 为 None 时返回 None"""
    with patch.object(langfuse_integration.settings, "langfuse_host", "http://localhost"):
        with patch.object(langfuse_integration.settings, "langfuse_public_key", "pk"):
            with patch.object(langfuse_integration.settings, "langfuse_secret_key", None):
                client = get_langfuse()
                assert client is None


def test_get_langfuse_returns_cached_client() -> None:
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


def test_record_llm_trace_no_error_when_uninitialized(unconfigured_langfuse: None) -> None:
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


def test_record_llm_trace_with_error_param_when_uninitialized(unconfigured_langfuse: None) -> None:
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


def test_record_llm_trace_calls_client_with_correct_params() -> None:
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
    assert call_kwargs["usage"] is None
    assert call_kwargs["metadata"]["cost_usd"] == 0.001
    assert call_kwargs["metadata"]["duration_seconds"] == 1.5


def test_record_llm_trace_with_error_records_error_level() -> None:
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


def test_record_llm_trace_truncates_long_prompt() -> None:
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


def test_record_llm_trace_client_exception_swallowed() -> None:
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
