"""src/observability/logging.py 单元测试

覆盖：
- add_trace_context processor（trace_id 注入，不依赖采样决策）
- mask_sensitive_keys processor（敏感键打码）
- build_file_handler / setup_logging（轮转参数生效）
- setup_logging（json / console 配置）
- bind_context / clear_context（上下文绑定与清除）
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from src.config import settings
from src.observability.logging import (
    add_trace_context,
    bind_context,
    build_file_handler,
    clear_context,
    mask_sensitive_keys,
    setup_logging,
)

# ---------------------------------------------------------------------------
# 共享 fixture：为 trace_id 注入测试设置真实 OTel TracerProvider
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def otel_tracer() -> Any:
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
def _clear_contextvars() -> Iterator[None]:
    """每个测试前后清理 contextvars，避免测试间状态泄漏"""
    clear_context()
    yield
    clear_context()


# ---------------------------------------------------------------------------
# add_trace_context
# ---------------------------------------------------------------------------


def test_add_trace_context_no_active_span() -> None:
    """无 active span 时不添加 trace_id（返回原始 event_dict）"""
    event_dict = {"event": "test"}
    result = add_trace_context(None, "info", event_dict)
    assert result == {"event": "test"}
    assert "trace_id" not in result
    assert "span_id" not in result


def test_add_trace_context_with_active_span(otel_tracer: Any) -> None:
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


def test_add_trace_context_with_active_span_does_not_mutate_input(otel_tracer: Any) -> None:
    """注入时不应修改传入的 event_dict（返回新 dict 或原 dict 增量）"""
    with otel_tracer.start_as_current_span("test-span"):
        event_dict = {"event": "test"}
        result = add_trace_context(None, "info", event_dict)
        assert "trace_id" in result
        # event_dict 原始字段保留
        assert result["event"] == "test"


def test_add_trace_context_otel_unavailable() -> None:
    """OTel 未安装时优雅降级（直接返回 event_dict）"""
    event_dict = {"event": "test", "key": "value"}
    with patch("src.observability.logging._OTEL_AVAILABLE", False):
        result = add_trace_context(None, "info", event_dict)
    assert result == {"event": "test", "key": "value"}
    assert "trace_id" not in result
    assert "span_id" not in result


# ---------------------------------------------------------------------------
# add_trace_context - 采样决策无关注入（R5-M17）
# ---------------------------------------------------------------------------


def _non_recording_span_with_valid_context() -> Any:
    """构造携带有效 SpanContext 的 NonRecordingSpan（模拟头采样未命中）"""
    from opentelemetry.trace import NonRecordingSpan, SpanContext

    ctx = SpanContext(trace_id=0x1234567890ABCDEF1234567890ABCDEF, span_id=0x1234567890ABCDEF, is_remote=False)
    return NonRecordingSpan(ctx)


def test_add_trace_context_non_recording_span_still_injects() -> None:
    """头采样未命中（NonRecordingSpan）但 SpanContext 有效时仍注入 trace_id"""
    span = _non_recording_span_with_valid_context()
    with patch("src.observability.logging._otel_trace.get_current_span", return_value=span):
        event_dict = {"event": "sampled_out"}
        result = add_trace_context(None, "info", event_dict)

    assert result["trace_id"] == "1234567890abcdef1234567890abcdef"
    assert result["span_id"] == "1234567890abcdef"
    # 原有字段保留
    assert result["event"] == "sampled_out"


def test_add_trace_context_invalid_span_context_skipped() -> None:
    """SpanContext 无效（trace_id=0）时不注入，避免产出全零假 trace_id"""
    from opentelemetry.trace import NonRecordingSpan, SpanContext

    invalid_ctx = SpanContext(trace_id=0, span_id=0, is_remote=False)
    span = NonRecordingSpan(invalid_ctx)
    with patch("src.observability.logging._otel_trace.get_current_span", return_value=span):
        event_dict = {"event": "test"}
        result = add_trace_context(None, "info", event_dict)

    assert "trace_id" not in result
    assert "span_id" not in result


# ---------------------------------------------------------------------------
# mask_sensitive_keys
# ---------------------------------------------------------------------------


def test_mask_sensitive_keys_masks_sensitive_key_values() -> None:
    """键名命中敏感模式的字段整值打码为 ***"""
    event_dict = {
        "event": "redis_connected",
        "password": "hunter2",
        "api_key": "sk-abc123",
        "access_token": "jwt-value",
        "Authorization": "Bearer xyz",
        "db_secret": "topsecret",
    }
    result = mask_sensitive_keys(None, "info", event_dict)
    assert result["password"] == "***"
    assert result["api_key"] == "***"
    assert result["access_token"] == "***"
    assert result["Authorization"] == "***"
    assert result["db_secret"] == "***"
    assert result["event"] == "redis_connected"


def test_mask_sensitive_keys_keeps_normal_fields() -> None:
    """普通字段原样保留（不做全文扫描）"""
    long_text = "redis://:pw@host:6379 " * 100
    event_dict = {"user_id": "u1", "content": long_text}
    result = mask_sensitive_keys(None, "info", event_dict)
    assert result["user_id"] == "u1"
    assert result["content"] == long_text


# ---------------------------------------------------------------------------
# 日志文件轮转（R5-L17）
# ---------------------------------------------------------------------------


def test_build_file_handler_applies_rotation_config(tmp_path: Path) -> None:
    """工厂按传入参数生成 RotatingFileHandler"""
    log_file = tmp_path / "backend.log"
    handler = build_file_handler(log_file, max_bytes=1024, backup_count=3)
    try:
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == 1024
        assert handler.backupCount == 3
    finally:
        handler.close()


def test_setup_logging_installs_rotating_file_handler() -> None:
    """setup_logging 在 root logger 上挂载使用配置旋钮的轮转 Handler"""
    setup_logging(log_level="info", log_format="json")
    rotating = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == settings.log_file_max_bytes
    assert rotating[0].backupCount == settings.log_backup_count


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


def test_setup_logging_json() -> None:
    """json 格式正确配置"""
    setup_logging(log_level="info", log_format="json")
    logger = structlog.get_logger("test")
    assert logger is not None


def test_setup_logging_console() -> None:
    """console 格式正确配置"""
    setup_logging(log_level="debug", log_format="console")
    logger = structlog.get_logger("test")
    assert logger is not None


def test_setup_logging_structlog_usable() -> None:
    """配置后 structlog 可用（不抛异常）"""
    setup_logging(log_level="info", log_format="json")
    logger = structlog.get_logger("test")
    logger.info("test_event", key="value")


def test_setup_logging_invalid_level_defaults_to_info() -> None:
    """未知日志级别回退到 INFO"""
    setup_logging(log_level="unknown_level", log_format="json")
    logger = structlog.get_logger("test")
    assert logger is not None


# ---------------------------------------------------------------------------
# bind_context
# ---------------------------------------------------------------------------


def test_bind_context_appears_in_logs() -> None:
    """绑定后日志包含绑定的字段"""
    setup_logging(log_level="info", log_format="json")
    bind_context(user_id="test_user", request_id="abc-123")
    with capture_logs(processors=[merge_contextvars]) as logs:
        logger = structlog.get_logger("test")
        logger.info("test_event")
    assert len(logs) == 1
    assert logs[0]["user_id"] == "test_user"
    assert logs[0]["request_id"] == "abc-123"


def test_bind_context_multiple_fields() -> None:
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


def test_clear_context_removes_bound_fields() -> None:
    """清除后日志不包含之前绑定的字段"""
    setup_logging(log_level="info", log_format="json")
    bind_context(user_id="test_user")
    clear_context()
    with capture_logs(processors=[merge_contextvars]) as logs:
        structlog.get_logger("test").info("test_event")
    assert len(logs) == 1
    assert "user_id" not in logs[0]


def test_clear_context_allows_rebind() -> None:
    """清除后可重新绑定新字段"""
    setup_logging(log_level="info", log_format="json")
    bind_context(user_id="old_user")
    clear_context()
    bind_context(user_id="new_user")
    with capture_logs(processors=[merge_contextvars]) as logs:
        structlog.get_logger("test").info("event")
    assert len(logs) == 1
    assert logs[0]["user_id"] == "new_user"


def test_clear_context_idempotent() -> None:
    """多次调用 clear_context 不报错"""
    clear_context()
    clear_context()
    clear_context()
