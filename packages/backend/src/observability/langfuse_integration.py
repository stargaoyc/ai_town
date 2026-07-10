"""Langfuse LLM 追踪集成 - 记录每次 LLM 调用的 prompt/response/token/cost

优雅降级策略：
- langfuse 未安装 → 所有功能透传，不影响业务
- langfuse 未配置（host/key 缺失）→ 跳过初始化，装饰器透传
- 记录失败 → 仅记录 structlog 日志，不抛异常

用法（装饰器）::

    from src.observability import trace_llm_call

    @trace_llm_call("character_chat")
    async def call_llm(prompt: str, model: str = "chat") -> str:
        ...
        return response

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

import functools
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from structlog import get_logger

from src.config import settings

if TYPE_CHECKING:
    from langfuse import Langfuse

logger = get_logger(__name__)

T = TypeVar("T")

# 全局 Langfuse 客户端单例
_langfuse_client: Langfuse | None = None

# prompt/response 截断长度
_MAX_TEXT_LENGTH = 2000

# 标记 langfuse 是否可用（未安装时降级）
try:
    from langfuse import Langfuse as _Langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _Langfuse = None  # type: ignore[assignment,misc]
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

    if (
        not settings.langfuse_host
        or not settings.langfuse_public_key
        or not settings.langfuse_secret_key
    ):
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


def _extract_prompt(args: tuple, kwargs: dict) -> str:
    """从函数参数中提取 prompt 文本

    优先从 kwargs 中查找 prompt/content/message/text/input 键，
    其次从位置参数中取第一个字符串。
    """
    for key in ("prompt", "content", "message", "text", "input"):
        val = kwargs.get(key)
        if isinstance(val, str):
            return val
    for arg in args:
        if isinstance(arg, str):
            return arg
    return ""


def _extract_model(kwargs: dict) -> str:
    """从函数参数中提取 model 名称"""
    return str(kwargs.get("model", "unknown"))


def _extract_result_info(result: Any) -> tuple[str, int, float]:
    """从返回值中提取 response / tokens / cost

    支持的返回值形式：
    - str：response=result，tokens=0，cost=0.0
    - dict：response 从 content/response/output 键取，
            tokens 从 tokens 键取，cost 从 cost 键取
    - tuple/list（len>=1）：response=result[0]，
            tokens/cost 从索引 1/2 取（len>=3 时）

    Returns:
        (response_text, tokens, cost)
    """
    if isinstance(result, str):
        return result, 0, 0.0
    if isinstance(result, dict):
        response = result.get("content") or result.get("response") or result.get("output") or ""
        if not isinstance(response, str):
            response = str(response)
        tokens = int(result.get("tokens") or 0)
        cost = float(result.get("cost") or 0.0)
        return response, tokens, cost
    if isinstance(result, (tuple, list)) and len(result) >= 1:
        response = result[0]
        if not isinstance(response, str):
            response = str(response) if response is not None else ""
        tokens = 0
        cost = 0.0
        if len(result) >= 3:
            try:
                tokens = int(result[1])
                cost = float(result[2])
            except (TypeError, ValueError):
                pass
        return response, tokens, cost
    return str(result) if result is not None else "", 0, 0.0


def trace_llm_call(name: str) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """装饰器：记录 LLM 调用到 Langfuse

    记录内容：
    - prompt（截断 2000 字符）
    - response（截断 2000 字符）
    - model 名称
    - tokens（prompt_tokens / completion_tokens / total_tokens）
    - cost（USD）
    - 耗时（秒）
    - error（失败时记录异常信息）

    装饰器仅支持 async 函数。如果 Langfuse 未初始化，装饰器直接透传调用。

    Args:
        name: 追踪名称（用于 Langfuse trace 标识）

    Returns:
        装饰器函数
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            client = get_langfuse()
            # Langfuse 未初始化 → 透传，不影响业务
            if client is None:
                return await func(*args, **kwargs)

            prompt = _extract_prompt(args, kwargs)
            model = _extract_model(kwargs)
            start_time = time.time()

            trace = client.trace(name=name)

            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                duration = time.time() - start_time
                error_msg = f"{type(e).__name__}: {e}"
                try:
                    trace.generation(
                        name=name,
                        model=model,
                        input=_truncate(prompt),
                        output=None,
                        usage={
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                        metadata={
                            "cost_usd": 0.0,
                            "duration_seconds": duration,
                            "error": error_msg,
                        },
                        level="ERROR",
                        status_message=error_msg,
                    )
                except Exception:
                    logger.error("langfuse_record_error_failed", exc_info=True)
                raise

            # 成功：记录 generation
            duration = time.time() - start_time
            response, tokens, cost = _extract_result_info(result)

            try:
                trace.generation(
                    name=name,
                    model=model,
                    input=_truncate(prompt),
                    output=_truncate(response),
                    usage={
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": tokens,
                    },
                    metadata={
                        "cost_usd": cost,
                        "duration_seconds": duration,
                    },
                )
            except Exception:
                logger.error("langfuse_record_failed", exc_info=True)

            return result

        return wrapper

    return decorator


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
                usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": tokens,
                },
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
                usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": tokens,
                },
                metadata={
                    "cost_usd": cost,
                    "duration_seconds": duration,
                },
            )
    except Exception:
        logger.error("langfuse_record_trace_failed", exc_info=True)
