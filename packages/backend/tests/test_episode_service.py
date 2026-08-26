"""EpisodeService 单元测试 - LLM 重要性评分的回退契约（R6-L3）

纯逻辑验证（不依赖 DB）：
- 评分失败（prompts 缺失 / 解析失败 / LLM 异常）时返回调用方规则分，
  而非硬编码字面量——规则分含 tick.py 的情绪加权，丢弃即失真
- 有效评分钳制到 [1, 10]
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from src.db.repositories.memory_repo import MemoryRepository
from src.llm import LLMClient, PromptTemplates
from src.memory.episode_service import EpisodeService


class StubPrompts:
    def render(self, name: str, **kwargs: Any) -> str:
        return f"[{name}]"


class StubLLM:
    def __init__(self, reply: str | Exception) -> None:
        self._reply = reply

    async def chat(self, prompt: str, model: str = "chat", system_prompt: str | None = None) -> str:
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _service(llm: StubLLM) -> EpisodeService:
    # 评分路径只依赖 chat 协议与 prompts；repo 不参与（测试规范 §5.2 cast 模式）
    return EpisodeService(
        cast(LLMClient, llm), cast(MemoryRepository, object()), prompts=cast(PromptTemplates, StubPrompts())
    )


async def _score(llm: StubLLM, rule_importance: int = 7) -> int:
    return await _service(llm).score_importance_with_llm(
        character_name="小艾",
        content="在广场遇到了老朋友",
        action_id="social",
        reason="开心地聊了很久",
        mood="happy",
        location="plaza",
        fallback_importance=rule_importance,
    )


class TestScoreImportanceFallback:
    async def test_llm_failure_returns_rule_importance(self) -> None:
        assert await _score(StubLLM(RuntimeError("llm down")), rule_importance=7) == 7

    async def test_parse_failure_returns_rule_importance(self) -> None:
        assert await _score(StubLLM("无法给出评分"), rule_importance=8) == 8

    async def test_prompts_unavailable_returns_rule_importance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.runtime.get_prompts", lambda: None)
        service = EpisodeService(cast(LLMClient, StubLLM("9")), cast(MemoryRepository, object()), prompts=None)
        score = await service.score_importance_with_llm(
            character_name="小艾",
            content="内容",
            action_id=None,
            reason=None,
            mood=None,
            location=None,
            fallback_importance=6,
        )
        assert score == 6

    async def test_valid_score_clamped_to_range(self) -> None:
        # 生产解析用 \b(\d+)\b，而 Python 的 \b 把 CJK 视为词字符，「12分」无法命中；
        # 带空格边界的数字形式才可解析
        assert await _score(StubLLM("12 分")) == 10
        assert await _score(StubLLM("0")) == 1

    async def test_valid_score_returned_over_rule_importance(self) -> None:
        assert await _score(StubLLM("重要性：3"), rule_importance=7) == 3
