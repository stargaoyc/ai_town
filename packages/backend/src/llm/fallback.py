"""多模型备用源 - 按序尝试 + 失败冷却

对标 yuiju 的 models.ts 机制，适配 Python/LangChain 栈：

- 每类调用可配置多个 OpenAI-compatible source（LLM_FALLBACK_SOURCES JSON 数组）
- ModelSourcePool 管理每个源的冷却状态：调用失败进入 5 分钟冷却
- 候选顺序 = 未冷却源按配置顺序在前，冷却中源排末尾（仍可作为最后兜底）
- 全部源失败时抛出最后一个异常，由上层熔断/预算逻辑统一处理

配置示例（.env）：
    LLM_FALLBACK_SOURCES=[{"api_key":"sk-x","base_url":"https://a.com/v1"},
                          {"api_key":"sk-y","base_url":"https://b.com/v1","model":"other-model"}]
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from langchain_openai import ChatOpenAI
from structlog import get_logger

from src.config import settings

logger = get_logger(__name__)

# 单源失败后的冷却时长（秒）：期间该源排到候选列表末尾
SOURCE_FAILURE_COOLDOWN_SECONDS = 5 * 60

T = TypeVar("T")


@dataclass(frozen=True)
class ModelSource:
    """一个 OpenAI-compatible 模型源"""

    api_key: str
    base_url: str
    model: str


class _SourceState:
    """单个源的运行时状态（最近失败时间戳）"""

    __slots__ = ("failed_at",)

    def __init__(self) -> None:
        self.failed_at: float = 0.0

    @property
    def cooling(self) -> bool:
        return (time.monotonic() - self.failed_at) < SOURCE_FAILURE_COOLDOWN_SECONDS

    def mark_failed(self) -> None:
        self.failed_at = time.monotonic()


class ModelSourcePool:
    """多模型源池：主源（settings）+ 备用源（llm_fallback_sources），带失败冷却"""

    def __init__(self) -> None:
        primary = ModelSource(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.model_chat,
        )
        self._sources: list[ModelSource] = [primary]
        self._states: list[_SourceState] = [_SourceState()]
        for raw in self._parse_fallbacks(settings.llm_fallback_sources):
            self._sources.append(
                ModelSource(
                    api_key=raw["api_key"],
                    base_url=raw["base_url"],
                    model=raw.get("model") or settings.model_chat,
                )
            )
            self._states.append(_SourceState())

    @staticmethod
    def _parse_fallbacks(raw: str) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("llm_fallback_sources_invalid_json")
            return []
        if not isinstance(parsed, list):
            logger.warning("llm_fallback_sources_not_a_list")
            return []
        valid: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict) and item.get("api_key") and item.get("base_url"):
                valid.append(item)
            else:
                logger.warning("llm_fallback_source_entry_invalid", keys=list(item) if isinstance(item, dict) else None)
        return valid

    def __len__(self) -> int:
        return len(self._sources)

    def ordered_candidates(self) -> list[int]:
        """候选索引：可用源按配置顺序在前，冷却中源排末尾兜底"""
        healthy = [i for i, st in enumerate(self._states) if not st.cooling]
        cooling = [i for i, st in enumerate(self._states) if st.cooling]
        return healthy + cooling

    def build_llm(self, index: int) -> ChatOpenAI:
        """为指定源构造 ChatOpenAI 实例"""
        src = self._sources[index]
        return ChatOpenAI(
            model=src.model,
            api_key=src.api_key,  # type: ignore[arg-type]
            base_url=src.base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

    def mark_success(self, index: int) -> None:
        self._states[index].failed_at = 0.0

    def mark_failure(self, index: int) -> None:
        self._states[index].mark_failed()
        remaining_until = int(SOURCE_FAILURE_COOLDOWN_SECONDS - (time.monotonic() - self._states[index].failed_at))
        logger.warning(
            "llm_source_cooldown_started",
            source_index=index,
            base_url=self._sources[index].base_url,
            cooldown_seconds=max(remaining_until, 0),
        )


async def invoke_with_fallback(pool: ModelSourcePool, run: Any) -> tuple[Any, int]:
    """按候选顺序执行 run(llm)，失败切换下一源

    Args:
        pool: 模型源池
        run: 接收 ChatOpenAI 实例并返回 awaitable 的回调（如 lambda llm: llm.ainvoke(msgs)）

    Returns:
        (run 的返回值, 成功的源索引)
    """
    last_error: Exception | None = None
    for index in pool.ordered_candidates():
        llm = pool.build_llm(index)
        try:
            result = await run(llm)
        except Exception as e:  # noqa: BLE001 —— 切换语义要求捕获任意调用异常后尝试下一源
            last_error = e
            pool.mark_failure(index)
            continue
        pool.mark_success(index)
        return result, index

    assert last_error is not None
    raise last_error
