"""结构化日志配置 - structlog + OpenTelemetry trace_id 注入

实现 Jaeger Span → Logs 联动：
- add_trace_context processor 从 OTel context 读取 trace_id/span_id 注入日志事件
  （只要 SpanContext 有效即注入，不依赖采样决策——头采样未命中的请求同样要能
  从日志跳转到 Trace）
- mask_sensitive_keys processor 对键名命中敏感模式的字段整值打码后进入渲染
- bind_context / clear_context 基于 contextvars 实现请求级上下文绑定（user_id 等）
- 标准库 logging 通过 ProcessorFormatter 也走 structlog 渲染，保证 uvicorn /
  sqlalchemy 等第三方库日志同样携带 trace_id，全链路可关联
- 日志同时输出到 stderr 和轮转文件（data/logs/backend.log），供 Alloy 采集到 Loki
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
import structlog.dev  # noqa: F401 - ConsoleRenderer 位于 structlog.dev

from src.config import settings
from src.observability.sanitizer import is_sensitive_key
from src.paths import find_project_root

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

    只要当前 SpanContext 有效（trace_id != 0）即注入，不检查 is_recording()：
    头采样（TraceIdRatioBased）未命中的 NonRecordingSpan 同样携带有效
    SpanContext，若因此丢弃 trace_id，未被采样的那半流量将永远无法从
    日志跳转回 Trace。opentelemetry 未安装或无 active span 时不添加字段。
    """
    if not _OTEL_AVAILABLE:
        return event_dict

    span = _otel_trace.get_current_span()
    if span is None:
        return event_dict

    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return event_dict

    event_dict["trace_id"] = f"{ctx.trace_id:032x}"
    event_dict["span_id"] = f"{ctx.span_id:016x}"
    return event_dict


def mask_sensitive_keys(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor：键名命中敏感模式的字段整值打码为 ***

    只做键匹配、不做全文扫描：逐值子串搜索会让每条日志都付出 O(n) 文本
    开销，而敏感信息泄漏面几乎总是由字段名（password/token/api_key 等）
    定位的。命中 sanitizer.SENSITIVE_KEY_PATTERNS 即打码，与手动脱敏共用
    同一模式集合。
    """
    for key in list(event_dict):
        if is_sensitive_key(str(key)):
            event_dict[key] = "***"
    return event_dict


def _ensure_log_dir() -> Path:
    """确保日志目录存在

    日志文件路径：{project_root}/data/logs/backend.log
    """
    # 经 find_project_root 兼容仓库/容器两种布局（容器挂载 ./data/logs:/app/data/logs）
    log_dir = find_project_root() / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def build_file_handler(log_file: Path, *, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    """构造轮转文件 Handler（独立工厂，便于单测断言轮转参数）

    长驻进程的日志文件无轮转会无限增长直至写满磁盘；max_bytes/backup_count
    由 LOG_FILE_MAX_BYTES / LOG_BACKUP_COUNT 配置。
    """
    return RotatingFileHandler(
        str(log_file),
        encoding="utf-8",
        maxBytes=max_bytes,
        backupCount=backup_count,
    )


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
    # 确保 trace_id / 敏感键打码 / 上下文变量在两条路径上一致生效
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_trace_context,
        mask_sensitive_keys,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # structlog 配置：完整 processor chain 含 renderer，直接输出到 stderr
    # cache_logger_on_first_use=False 确保模块导入时的 logger 在 setup_logging 后使用最新配置
    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level_num),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        context_class=dict,
        cache_logger_on_first_use=False,
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

    # Handler 1: stderr（控制台输出）
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    # Handler 2: 轮转文件（JSON 格式，供 Alloy 采集到 Loki）
    handlers: list[logging.Handler] = [stderr_handler]
    try:
        log_dir = _ensure_log_dir()
        log_file = log_dir / "backend.log"
        file_handler = build_file_handler(
            log_file,
            max_bytes=settings.log_file_max_bytes,
            backup_count=settings.log_backup_count,
        )
        # 文件始终使用 JSON 格式
        file_formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)
    except Exception:
        # 日志目录创建失败不影响服务启动
        pass

    root = logging.getLogger()
    root.handlers = handlers
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
