"""Langfuse 追踪辅助函数 - 为 LLM 调用和角色 Tick 提供轻量级追踪

本模块复用 langfuse_integration 中的 Langfuse 单例，提供：
- trace_llm_call(): 记录 LLM 生成调用
- trace_character_tick(): 记录角色 Tick 追踪
- flush_langfuse(): 关闭前刷新缓冲区

所有函数在 Langfuse 未配置时静默降级（no-op）。
"""
from __future__ import annotations

from structlog import get_logger

from src.observability.langfuse_integration import get_langfuse

logger = get_logger(__name__)

# 文本截断长度
_MAX_TEXT_LENGTH = 2000


def _truncate(text: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"


def trace_llm_call(
    *,
    character_id: str | None = None,
    model: str,
    prompt: str,
    response: str,
    tokens: int = 0,
    latency_ms: int,
) -> None:
    """记录一次 LLM 生成调用到 Langfuse

    Args:
        character_id: 关联角色 ID（可选）
        model: 模型名称
        prompt: 输入提示
        response: 模型回复
        tokens: 总 token 数
        latency_ms: 调用耗时（毫秒）
    """
    client = get_langfuse()
    if client is None:
        return

    try:
        metadata: dict = {
            "latency_ms": latency_ms,
            "tokens": tokens,
        }
        if character_id:
            metadata["character_id"] = character_id

        trace = client.trace(
            name="llm_call",
            metadata={"character_id": character_id} if character_id else None,
        )
        trace.generation(
            name="llm_generation",
            model=model,
            input=_truncate(prompt),
            output=_truncate(response),
            usage={"total_tokens": tokens} if tokens else None,
            metadata=metadata,
        )
    except Exception:
        logger.error("langfuse_trace_llm_call_failed", exc_info=True)


def trace_character_tick(
    *,
    character_id: str,
    action: str,
    duration_ms: int,
) -> None:
    """记录一次角色 Tick 追踪到 Langfuse

    Args:
        character_id: 角色 ID
        action: 执行的 Action ID
        duration_ms: Tick 耗时（毫秒）
    """
    client = get_langfuse()
    if client is None:
        return

    try:
        trace = client.trace(
            name="character_tick",
            metadata={"character_id": character_id},
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
