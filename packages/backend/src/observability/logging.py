"""结构化日志配置 - structlog + OpenTelemetry trace_id 注入

实现 Jaeger Span → Logs 联动：
- add_trace_context processor 从 OTel context 读取 trace_id/span_id 注入日志事件
- bind_context / clear_context 基于 contextvars 实现请求级上下文绑定（user_id 等）
- 标准库 logging 通过 ProcessorFormatter 也走 structlog 渲染，保证 uvicorn /
  sqlalchemy 等第三方库日志同样携带 trace_id，全链路可关联
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
import structlog.dev  # noqa: F401 - ConsoleRenderer 位于 structlog.dev

try:
    from opentelemetry import trace as _otel_trace

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - opentelemetry 为声明依赖，正常情况不会触发
    _otel_trace = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False


_LOG_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def add_trace_context(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor：注入 OTel trace_id / span_id

    从当前 OTel context 获取 active span，若有正在记录的 span 则将
    trace_id（32 hex）/ span_id（16 hex）写入日志事件，便于在 Jaeger 中
    从 Span 跳转到关联日志。

    opentelemetry 未安装或无 active span 时不添加字段，原样返回 event_dict。
    """
    if not _OTEL_AVAILABLE:
        return event_dict

    span = _otel_trace.get_current_span()
    if span is None or not span.is_recording():
        return event_dict

    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return event_dict

    event_dict["trace_id"] = f"{ctx.trace_id:032x}"
    event_dict["span_id"] = f"{ctx.span_id:016x}"
    return event_dict


def setup_logging(log_level: str = "info", log_format: str = "json") -> None:
    """初始化 structlog 结构化日志 + 标准库 logging 集成

    Args:
        log_level: 日志级别（debug/info/warning/error/critical）
        log_format: 输出格式，"json" 使用 JSONRenderer（生产环境），
                    其他值使用 ConsoleRenderer（开发环境，彩色可读）
    """
    level_num = _LOG_LEVELS.get(log_level.lower(), logging.INFO)

    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # 共享 processor chain：structlog 与标准库 foreign 日志共用，
    # 确保 trace_id / 上下文变量在两条路径上一致注入
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # structlog 配置：完整 processor chain 含 renderer，直接输出到 stderr
    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level_num),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    # 标准库 logging handler 也使用 structlog 渲染
    # uvicorn / sqlalchemy 等第三方库日志经 foreign_pre_chain 注入 trace_id
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level_num)


def bind_context(**kwargs: Any) -> None:
    """绑定请求级上下文变量（如 user_id, character_id, conversation_id）

    基于 structlog.contextvars，在同一线程 / asyncio 任务内所有后续日志
    自动携带这些字段。应在请求中间件中调用，请求结束时调用 clear_context 清理。
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """清除所有通过 bind_context 绑定的上下文变量"""
    structlog.contextvars.clear_contextvars()
