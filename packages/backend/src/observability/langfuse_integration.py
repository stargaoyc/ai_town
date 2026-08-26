"""Langfuse 客户端单例与独立 LLM 追踪记录

优雅降级策略：
- langfuse 未安装 → 所有功能透传，不影响业务
- langfuse 未配置（host/key 缺失）→ 跳过初始化，记录函数透传
- 记录失败 → 仅记录 structlog 日志，不抛异常

LLM 生成调用的 Tick 级追踪见 langfuse_tracing.trace_llm_call
（自动挂到当前 Tick 根 trace 下形成父子层级）；本模块只承载
Langfuse 客户端单例与无 Tick 上下文的手动记录入口。

用法（独立记录）::

    from src.observability import record_llm_trace

    record_llm_trace(
        prompt="...",
        response="...",
        model="gpt-4o-mini",
        tokens=120,
        cost=0.0002,
        duration=1.5,
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from structlog import get_logger

from src.config import settings as settings

if TYPE_CHECKING:
    from langfuse import Langfuse

logger = get_logger(__name__)

# 全局 Langfuse 客户端单例
_langfuse_client: Langfuse | None = None

# prompt/response 截断长度
_MAX_TEXT_LENGTH = 2000

# 标记 langfuse 是否可用（未安装时降级）
try:
    from langfuse import Langfuse as _Langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _Langfuse = None
    _LANGFUSE_AVAILABLE = False


def setup_langfuse() -> Langfuse | None:
    """初始化 Langfuse 客户端（全局单例）

    从 settings 读取 langfuse_host / langfuse_public_key / langfuse_secret_key：
    - langfuse 未安装 → 记录 warning，返回 None
    - 任一配置为 None → 记录 warning，返回 None
    - 创建成功 → 存入全局单例并返回

    Returns:
        Langfuse 客户端实例，或 None（未配置/未安装）
    """
    global _langfuse_client

    if not _LANGFUSE_AVAILABLE:
        logger.warning(
            "langfuse_not_installed",
            message="langfuse package not installed, skipping initialization",
        )
        return None

    if not settings.langfuse_host or not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning(
            "langfuse_not_configured",
            message="langfuse host/public_key/secret_key not set, skipping initialization",
        )
        return None

    try:
        _langfuse_client = _Langfuse(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
        logger.info("langfuse_initialized", host=settings.langfuse_host)
    except Exception:
        logger.error("langfuse_init_failed", exc_info=True)
        _langfuse_client = None

    return _langfuse_client


def get_langfuse() -> Langfuse | None:
    """获取全局 Langfuse 单例

    如未初始化则尝试初始化一次。

    Returns:
        Langfuse 客户端实例，或 None
    """
    global _langfuse_client
    if _langfuse_client is None:
        return setup_langfuse()
    return _langfuse_client


def _truncate(text: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    """截断文本到指定长度"""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"


def record_llm_trace(
    prompt: str,
    response: str,
    model: str,
    tokens: int,
    cost: float,
    duration: float,
    error: str | None = None,
) -> None:
    """独立记录 LLM 调用追踪（不使用装饰器的场景）

    用于在无法使用装饰器的代码路径中手动记录 LLM 调用。
    如果 Langfuse 未初始化，直接返回（静默降级）。

    Args:
        prompt: 输入提示
        response: 模型回复
        model: 模型名称
        tokens: 总 token 数
        cost: 花费（USD）
        duration: 耗时（秒）
        error: 错误信息（可选，非 None 表示调用失败）
    """
    client = get_langfuse()
    if client is None:
        return

    try:
        trace = client.trace(name="llm_call")
        if error is not None:
            trace.generation(
                name="llm_call",
                model=model,
                input=_truncate(prompt),
                output=None,
                usage=None,
                metadata={
                    "cost_usd": cost,
                    "duration_seconds": duration,
                    "error": error,
                },
                level="ERROR",
                status_message=error,
            )
        else:
            trace.generation(
                name="llm_call",
                model=model,
                input=_truncate(prompt),
                output=_truncate(response),
                usage=None,
                metadata={
                    "cost_usd": cost,
                    "duration_seconds": duration,
                },
            )
    except Exception:
        logger.error("langfuse_record_trace_failed", exc_info=True)
