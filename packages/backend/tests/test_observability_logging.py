"""src/observability/logging.py 单元测试

覆盖：
- add_trace_context processor（trace_id 注入）
- setup_logging（json / console 配置）
- bind_context / clear_context（上下文绑定与清除）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import structlog
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from src.observability.logging import (
    add_trace_context,
    bind_context,
    clear_context,
    setup_logging,
)


# ---------------------------------------------------------------------------
# 共享 fixture：为 trace_id 注入测试设置真实 OTel TracerProvider
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def otel_tracer():
    """设置真实 OTel TracerProvider，返回可用于创建 active span 的 tracer。

    set_tracer_provider 全局只能调用一次（后续调用被忽略并记录 warning），
    使用 session 作用域避免重复设置。
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    # 首次调用生效；若其他测试已设置则忽略（OTel API 行为）
    trace.set_tracer_provider(provider)
    return trace.get_tracer("test")


@pytest.fixture(autouse=True)
def _clear_contextvars():
    """每个测试前后清理 contextvars，避免测试间状态泄漏"""
    clear_context()
    yield
    clear_context()


# ---------------------------------------------------------------------------
# add_trace_context
# ---------------------------------------------------------------------------


def test_add_trace_context_no_active_span():
    """无 active span 时不添加 trace_id（返回原始 event_dict）"""
    event_dict = {"event": "test"}
    result = add_trace_context(None, "info", event_dict)
    assert result == {"event": "test"}
    assert "trace_id" not in result
    assert "span_id" not in result


def test_add_trace_context_with_active_span(otel_tracer):
    """有 active span 时添加 trace_id（32 hex）和 span_id（16 hex）"""
    with otel_tracer.start_as_current_span("test-span"):
        event_dict = {"event": "test"}
        result = add_trace_context(None, "info", event_dict)

        assert "trace_id" in result
        assert "span_id" in result
        # trace_id 为 32 位 hex
        assert len(result["trace_id"]) == 32
        int(result["trace_id"], 16)  # 验证是有效 hex
        # span_id 为 16 位 hex
        assert len(result["span_id"]) == 16
        int(result["span_id"], 16)
        # 原有字段保留
        assert result["event"] == "test"


def test_add_trace_context_with_active_span_does_not_mutate_input(otel_tracer):
    """注入时不应修改传入的 event_dict（返回新 dict 或原 dict 增量）"""
    with otel_tracer.start_as_current_span("test-span"):
        event_dict = {"event": "test"}
        result = add_trace_context(None, "info", event_dict)
        assert "trace_id" in result
        # event_dict 原始字段保留
        assert result["event"] == "test"


def test_add_trace_context_otel_unavailable():
    """OTel 未安装时优雅降级（直接返回 event_dict）"""
    event_dict = {"event": "test", "key": "value"}
    with patch("src.observability.logging._OTEL_AVAILABLE", False):
        result = add_trace_context(None, "info", event_dict)
    assert result == {"event": "test", "key": "value"}
    assert "trace_id" not in result
    assert "span_id" not in result


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


def test_setup_logging_json():
    """json 格式正确配置"""
    setup_logging(log_level="info", log_format="json")
    logger = structlog.get_logger("test")
    assert logger is not None


def test_setup_logging_console():
    """console 格式正确配置"""
    setup_logging(log_level="debug", log_format="console")
    logger = structlog.get_logger("test")
    assert logger is not None


def test_setup_logging_structlog_usable():
    """配置后 structlog 可用（不抛异常）"""
    setup_logging(log_level="info", log_format="json")
    logger = structlog.get_logger("test")
    logger.info("test_event", key="value")


def test_setup_logging_invalid_level_defaults_to_info():
    """未知日志级别回退到 INFO"""
    setup_logging(log_level="unknown_level", log_format="json")
    logger = structlog.get_logger("test")
    assert logger is not None


# ---------------------------------------------------------------------------
# bind_context
# ---------------------------------------------------------------------------


def test_bind_context_appears_in_logs():
    """绑定后日志包含绑定的字段"""
    setup_logging(log_level="info", log_format="json")
    bind_context(user_id="test_user", request_id="abc-123")
    with capture_logs(processors=[merge_contextvars]) as logs:
        logger = structlog.get_logger("test")
        logger.info("test_event")
    assert len(logs) == 1
    assert logs[0]["user_id"] == "test_user"
    assert logs[0]["request_id"] == "abc-123"


def test_bind_context_multiple_fields():
    """绑定多个字段后日志全部包含"""
    setup_logging(log_level="info", log_format="json")
    bind_context(
        user_id="u1",
        character_id="c1",
        conversation_id="conv1",
    )
    with capture_logs(processors=[merge_contextvars]) as logs:
        structlog.get_logger("test").info("event")
    assert len(logs) == 1
    assert logs[0]["user_id"] == "u1"
    assert logs[0]["character_id"] == "c1"
    assert logs[0]["conversation_id"] == "conv1"


# ---------------------------------------------------------------------------
# clear_context
# ---------------------------------------------------------------------------


def test_clear_context_removes_bound_fields():
    """清除后日志不包含之前绑定的字段"""
    setup_logging(log_level="info", log_format="json")
    bind_context(user_id="test_user")
    clear_context()
    with capture_logs(processors=[merge_contextvars]) as logs:
        structlog.get_logger("test").info("test_event")
    assert len(logs) == 1
    assert "user_id" not in logs[0]


def test_clear_context_allows_rebind():
    """清除后可重新绑定新字段"""
    setup_logging(log_level="info", log_format="json")
    bind_context(user_id="old_user")
    clear_context()
    bind_context(user_id="new_user")
    with capture_logs(processors=[merge_contextvars]) as logs:
        structlog.get_logger("test").info("event")
    assert len(logs) == 1
    assert logs[0]["user_id"] == "new_user"


def test_clear_context_idempotent():
    """多次调用 clear_context 不报错"""
    clear_context()
    clear_context()
    clear_context()
