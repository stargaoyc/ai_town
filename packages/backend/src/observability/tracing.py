"""OpenTelemetry Tracing 集成

提供：
- setup_tracing: 初始化 OTel SDK（FastAPI + Asyncpg 自动 instrument）
- get_tracer: 获取 tracer
- trace_span: 装饰器，为 async 函数添加 span（contextvars 自动传播 trace context）
"""

from __future__ import annotations

import contextlib
import functools
import inspect
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from structlog import get_logger

from src.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.trace import Tracer

logger = get_logger(__name__)

# === OpenTelemetry 可选导入（优雅降级） ===
# API（opentelemetry-api）：提供 trace 模块与 NoOp tracer
try:
    from opentelemetry import trace

    _OTEL_API_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_API_AVAILABLE = False
    trace = None  # type: ignore[assignment]

# SDK（opentelemetry-sdk + exporter）：setup_tracing 需要
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    _OTEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_SDK_AVAILABLE = False
    OTLPSpanExporter = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]
    ConsoleSpanExporter = None  # type: ignore[assignment]
    TraceIdRatioBased = None  # type: ignore[assignment]

# 自动 instrument：FastAPI
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _FASTAPI_INSTRUMENTOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_INSTRUMENTOR_AVAILABLE = False
    FastAPIInstrumentor = None  # type: ignore[assignment]

# 自动 instrument：asyncpg
try:
    from opentelemetry.instrumentation.asyncpg import AsyncpgInstrumentor

    _ASYNCPG_INSTRUMENTOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ASYNCPG_INSTRUMENTOR_AVAILABLE = False
    AsyncpgInstrumentor = None  # type: ignore[assignment]


class _NoOpTracer:
    """OTel API 不可用时的兜底 tracer，确保 trace_span 装饰器不崩溃。"""

    @contextlib.contextmanager
    def start_as_current_span(self, name: str, *args: Any, **kwargs: Any):  # noqa: ARG002
        yield None


_initialized = False


def setup_tracing(app: FastAPI) -> None:
    """初始化 OpenTelemetry SDK。

    - 从 settings 读取 otel_endpoint / otel_service_name / otel_traces_sampler_rate
    - otel_endpoint 为 None 时使用 ConsoleSpanExporter（开发环境）
    - 否则使用 OTLPSpanExporter（HTTP 协议，发送到 otel_endpoint）
    - 使用 BatchSpanProcessor + TraceIdRatioBased 采样
    - 注册 FastAPIInstrumentor / AsyncpgInstrumentor 自动 instrument

    Args:
        app: FastAPI 应用实例
    """
    global _initialized

    if not _OTEL_SDK_AVAILABLE:
        logger.warning(
            "otel_sdk_not_available",
            message="OpenTelemetry SDK not installed, tracing disabled",
        )
        return

    if _initialized:
        logger.debug("otel_tracing_already_initialized")
        return

    # 资源：标识服务
    resource = Resource.create({"service.name": settings.otel_service_name})

    # 采样器
    sampler = TraceIdRatioBased(rate=settings.otel_traces_sampler_rate)

    # TracerProvider
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)

    # Exporter + SpanProcessor
    if settings.otel_endpoint is None:
        # 开发环境：输出到控制台
        exporter = ConsoleSpanExporter()
        logger.info(
            "otel_tracing_console_exporter",
            service_name=settings.otel_service_name,
            sampler_rate=settings.otel_traces_sampler_rate,
        )
    else:
        # 生产环境：OTLP HTTP
        exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint)
        logger.info(
            "otel_tracing_otlp_exporter",
            endpoint=settings.otel_endpoint,
            service_name=settings.otel_service_name,
            sampler_rate=settings.otel_traces_sampler_rate,
        )

    tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    # 注册为全局 TracerProvider
    trace.set_tracer_provider(tracer_provider)

    # 自动 instrument FastAPI 路由
    if _FASTAPI_INSTRUMENTOR_AVAILABLE:
        try:
            FastAPIInstrumentor.instrument_app(app)
            logger.info("otel_fastapi_instrumented")
        except Exception as e:
            logger.warning(
                "otel_fastapi_instrument_failed",
                error=str(e),
                exc_info=True,
            )
    else:
        logger.warning(
            "otel_fastapi_instrumentor_not_available",
            message="opentelemetry-instrumentation-fastapi not installed",
        )

    # 自动 instrument 数据库查询
    if _ASYNCPG_INSTRUMENTOR_AVAILABLE:
        try:
            AsyncpgInstrumentor().instrument()
            logger.info("otel_asyncpg_instrumented")
        except Exception as e:
            logger.warning(
                "otel_asyncpg_instrument_failed",
                error=str(e),
                exc_info=True,
            )
    else:
        logger.warning(
            "otel_asyncpg_instrumentor_not_available",
            message="opentelemetry-instrumentation-asyncpg not installed",
        )

    _initialized = True


def get_tracer(name: str = "ai-town") -> Tracer:
    """获取 tracer。

    OTel SDK 未安装 / 未初始化时返回兜底 NoOp tracer（不崩溃）。

    Args:
        name: tracer 名称（通常为模块/服务名）

    Returns:
        Tracer 实例
    """
    if _OTEL_API_AVAILABLE and trace is not None:
        return trace.get_tracer(name)
    return _NoOpTracer()  # type: ignore[return-value]


F = TypeVar("F", bound=Callable[..., Any])


def _truncate(value: Any, max_len: int = 200) -> str:
    """值转字符串并截断，避免 span 属性过长。"""
    try:
        s = repr(value)
    except Exception:  # noqa: BLE001
        s = "<unrepresentable>"
    if len(s) > max_len:
        return s[:max_len] + "...(truncated)"
    return s


def trace_span(name: str) -> Callable[[F], F]:
    """装饰器：为 async 函数添加 span。

    记录：
    - 函数名（code.function）
    - 参数（args.<name>，截断）
    - 返回值类型（result.type）
    - 异常（record_exception）

    通过 ``start_as_current_span`` + contextvars 自动传播 trace context，
    async 函数中跨 await 自动继承父 span。

    Args:
        name: span 名称
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                with tracer.start_as_current_span(name) as span:
                    if span is not None:
                        span.set_attribute("code.function", func.__name__)
                        try:
                            bound = inspect.signature(func).bind(*args, **kwargs)
                            bound.apply_defaults()
                            for arg_name, arg_value in bound.arguments.items():
                                span.set_attribute(f"args.{arg_name}", _truncate(arg_value))
                        except (TypeError, ValueError):
                            pass
                    try:
                        result = await func(*args, **kwargs)
                    except Exception as e:
                        if span is not None:
                            span.record_exception(e)
                        raise
                    if span is not None:
                        span.set_attribute("result.type", type(result).__name__)
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(name) as span:
                if span is not None:
                    span.set_attribute("code.function", func.__name__)
                    try:
                        bound = inspect.signature(func).bind(*args, **kwargs)
                        bound.apply_defaults()
                        for arg_name, arg_value in bound.arguments.items():
                            span.set_attribute(f"args.{arg_name}", _truncate(arg_value))
                    except (TypeError, ValueError):
                        pass
                try:
                    result = func(*args, **kwargs)
                except Exception as e:
                    if span is not None:
                        span.record_exception(e)
                    raise
                if span is not None:
                    span.set_attribute("result.type", type(result).__name__)
                return result

        return sync_wrapper  # type: ignore[return-value]

    return decorator
