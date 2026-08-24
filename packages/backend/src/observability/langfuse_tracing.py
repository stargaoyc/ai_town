"""Langfuse 追踪辅助函数 - 为 LLM 调用和角色 Tick 提供轻量级追踪

本模块复用 langfuse_integration 中的 Langfuse 单例，提供：
- start_tick_trace()/end_tick_trace(): 以 Tick 为根 trace 串联全部子观测
- trace_llm_call(): 记录 LLM 生成调用（自动挂到当前 Tick 根 trace 下，
  形成父子层级；无 Tick 上下文时独立成 trace）
- flush_langfuse(): 关闭前刷新缓冲区

所有函数在 Langfuse 未配置时静默降级（no-op）。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from structlog import get_logger

from src.observability.langfuse_integration import get_langfuse

logger = get_logger(__name__)

# 文本截断长度
_MAX_TEXT_LENGTH = 2000

# 当前 Tick 的根 trace id（ContextVar 跨 await 传播，任务间隔离）
_tick_trace_id: ContextVar[str | None] = ContextVar("tick_trace_id", default=None)


def _truncate(text: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"


def _otel_trace_id() -> str | None:
    """读取当前 OTel span 的 trace id（十六进制），用于 Langfuse ↔ Jaeger 互查"""
    try:
        from opentelemetry.trace import get_current_span

        ctx = get_current_span().get_span_context()
        if ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return None


def get_current_tick_trace_id() -> str | None:
    """读取当前任务绑定的 Tick 根 trace id"""
    return _tick_trace_id.get()


def start_tick_trace(character_id: str) -> str | None:
    """创建 Tick 根 trace 并绑定到当前任务上下文

    此后同任务内的 trace_llm_call 自动作为其子 generation，
    形成「Tick -> 多次 LLM 调用」的父子层级（审查清单 #7）。
    """
    client = get_langfuse()
    if client is None:
        return None
    try:
        trace_id = client.create_trace_id()
        client.trace(id=trace_id, name="character_tick", metadata={"character_id": character_id})
        _tick_trace_id.set(trace_id)
        return str(trace_id)
    except Exception:
        logger.error("langfuse_start_tick_trace_failed", exc_info=True)
        return None


def end_tick_trace(action: str, duration_ms: int) -> None:
    """在 Tick 根 trace 上补记执行摘要 span，并解除任务绑定"""
    trace_id = _tick_trace_id.get()
    client = get_langfuse()
    try:
        if trace_id and client is not None:
            client.span(
                trace_id=trace_id,
                name="tick_execution",
                input={"action": action},
                metadata={"duration_ms": duration_ms},
            )
    except Exception:
        logger.error("langfuse_end_tick_trace_failed", exc_info=True)
    finally:
        _tick_trace_id.set(None)


def trace_llm_call(
    *,
    character_id: str | None = None,
    model: str,
    prompt: str,
    response: str,
    tokens: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int,
) -> None:
    """记录一次 LLM 生成调用到 Langfuse

    存在 Tick 根 trace 时作为其子 generation 挂载（父子层级）；
    否则独立成 trace（兼容非 Tick 链路调用，如消息服务）。
    usage 拆分 prompt/completion 并记录 per-call 成本（审查 §八盲区 3）。
    """
    client = get_langfuse()
    if client is None:
        return

    try:
        metadata: dict[str, Any] = {
            "latency_ms": latency_ms,
            "tokens": tokens,
        }
        if cost_usd > 0:
            metadata["cost_usd"] = cost_usd
        if character_id:
            metadata["character_id"] = character_id
        otel_trace_id = _otel_trace_id()
        if otel_trace_id:
            metadata["otel_trace_id"] = otel_trace_id

        usage: dict[str, int | float] | None = None
        if tokens:
            usage = {"total_tokens": tokens}
            if prompt_tokens or completion_tokens:
                usage["prompt_tokens"] = prompt_tokens
                usage["completion_tokens"] = completion_tokens
            if cost_usd > 0:
                usage["cost_usd"] = cost_usd

        parent_trace_id = _tick_trace_id.get()
        if parent_trace_id:
            client.generation(
                trace_id=parent_trace_id,
                name="llm_generation",
                model=model,
                input=_truncate(prompt),
                output=_truncate(response),
                usage=usage,
                metadata=metadata,
            )
            return

        trace = client.trace(
            name="llm_call",
            metadata={"character_id": character_id} if character_id else None,
        )
        trace.generation(
            name="llm_generation",
            model=model,
            input=_truncate(prompt),
            output=_truncate(response),
            usage=usage,
            metadata=metadata,
        )
    except Exception:
        logger.error("langfuse_trace_llm_call_failed", exc_info=True)


def trace_character_tick(
    *,
    character_id: str,
    action: str,
    duration_ms: int,
    trace_id: str | None = None,
) -> None:
    """记录一次角色 Tick 追踪到 Langfuse

    Args:
        character_id: 角色 ID
        action: 执行的 Action ID
        duration_ms: Tick 耗时（毫秒）
        trace_id: 已存在的根 trace id（Tick 父子串联时传入，挂为该 trace 的摘要 span）
    """
    client = get_langfuse()
    if client is None:
        return

    try:
        metadata: dict[str, Any] = {"character_id": character_id}
        otel_trace_id = _otel_trace_id()
        if otel_trace_id:
            metadata["otel_trace_id"] = otel_trace_id
        if trace_id:
            # 挂到既有根 trace（start_tick_trace 创建），保持父子层级
            client.span(
                trace_id=trace_id,
                name="tick_execution",
                input={"character_id": character_id, "action": action},
                output={"action": action, "duration_ms": duration_ms},
                metadata={"duration_ms": duration_ms},
            )
            return
        trace = client.trace(
            name="character_tick",
            metadata=metadata,
        )
        trace.span(
            name="tick_execution",
            input={"character_id": character_id, "action": action},
            output={"action": action, "duration_ms": duration_ms},
            metadata={"duration_ms": duration_ms},
        )
    except Exception:
        logger.error("langfuse_trace_character_tick_failed", exc_info=True)


def flush_langfuse() -> None:
    """关闭前刷新 Langfuse 缓冲区，确保所有追踪数据已发送"""
    client = get_langfuse()
    if client is None:
        return

    try:
        client.flush()
        logger.info("langfuse_flushed")
    except Exception:
        logger.error("langfuse_flush_failed", exc_info=True)
