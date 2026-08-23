"""多模型备用源单元测试（任务 23）

验证 ModelSourcePool 的冷却切换语义：
- 配置解析：非法 JSON / 非数组 / 缺字段条目均降级为仅主源
- ordered_candidates：健康源在前（配置顺序），冷却源排末尾兜底
- invoke_with_fallback：首源失败自动切下一源；全部失败抛最后异常
- 冷却恢复：mark_success 清除冷却状态
"""

from __future__ import annotations

from typing import Any

import pytest

from src.config import settings
from src.llm.fallback import (
    SOURCE_FAILURE_COOLDOWN_SECONDS,
    ModelSourcePool,
    invoke_with_fallback,
)


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings 是导入时实例化的单例，monkeypatch.setenv 不影响它——直接改属性。
    # monkeypatch.setattr 会在测试结束后自动恢复原值。
    monkeypatch.setattr(settings, "openai_api_key", "sk-primary", raising=False)
    monkeypatch.setattr(settings, "openai_base_url", "https://primary.example/v1", raising=False)
    monkeypatch.setattr(settings, "model_chat", "test-model", raising=False)
    monkeypatch.setattr(settings, "llm_fallback_sources", "[]", raising=False)


def _make_pool(fallbacks_json: str, monkeypatch: pytest.MonkeyPatch) -> ModelSourcePool:
    monkeypatch.setattr(settings, "llm_fallback_sources", fallbacks_json, raising=False)
    return ModelSourcePool()


class TestParseFallbacks:
    def test_valid_config_appends_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool(
            '[{"api_key":"k2","base_url":"https://b.example/v1"},{"api_key":"k3","base_url":"https://c.example/v1","model":"m3"}]',
            monkeypatch,
        )
        assert len(pool) == 3
        candidates = pool.ordered_candidates()
        assert candidates == [0, 1, 2]

    def test_invalid_json_degrades_to_primary_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool("not-json{", monkeypatch)
        assert len(pool) == 1

    def test_non_list_and_missing_fields_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool(
            '{"api_key":"x"}',
            monkeypatch,
        )
        assert len(pool) == 1

        pool2 = _make_pool('[{"api_key":"","base_url":"https://b.example/v1"}]', monkeypatch)
        assert len(pool2) == 1


class TestCooling:
    def test_cooling_source_moves_to_tail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool('[{"api_key":"k2","base_url":"https://b.example/v1"}]', monkeypatch)
        pool.mark_failure(0)

        assert pool.ordered_candidates() == [1, 0]

    def test_mark_success_clears_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool("[]", monkeypatch)
        pool.mark_failure(0)
        assert pool.ordered_candidates() == [0]  # 仅主源时仍参与候选
        assert pool._states[0].cooling

        # 成功后清除冷却
        pool.mark_success(0)
        assert not pool._states[0].cooling

        # 再次失败进入冷却，模拟冷却过期：失败时间点回拨到冷却窗口之外
        pool.mark_failure(0)
        assert pool._states[0].cooling
        assert pool._states[0].failed_at is not None
        pool._states[0].failed_at -= SOURCE_FAILURE_COOLDOWN_SECONDS + 1
        assert not pool._states[0].cooling

    def test_fresh_source_never_cools_even_with_low_monotonic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从未失败的源不得被判为冷却中——CI 容器启动初期 monotonic 读数很小"""
        pool = _make_pool("[]", monkeypatch)
        assert pool._states[0].failed_at is None
        assert pool.ordered_candidates() == [0]


class TestInvokeWithFallback:
    async def test_first_source_success_no_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool("[]", monkeypatch)

        calls: list[int] = []

        async def run(llm: Any) -> str:
            calls.append(1)
            return "ok"

        result, index = await invoke_with_fallback(pool, run)
        assert (result, index) == ("ok", 0)

    async def test_switches_to_fallback_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool('[{"api_key":"k2","base_url":"https://b.example/v1"}]', monkeypatch)

        async def run(llm: Any) -> str:
            key = (
                llm.openai_api_key.get_secret_value()
                if hasattr(llm.openai_api_key, "get_secret_value")
                else str(llm.openai_api_key)
            )
            if key == "sk-primary":
                raise ConnectionError("primary down")
            return "from-fallback"

        result, index = await invoke_with_fallback(pool, run)
        assert result == "from-fallback"
        assert index == 1
        # 主源进入冷却，下次排末尾
        assert pool.ordered_candidates() == [1, 0]

    async def test_all_failures_raise_last_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool('[{"api_key":"k2","base_url":"https://b.example/v1"}]', monkeypatch)

        errors: list[Exception] = []

        async def run(llm: Any) -> str:
            e = ConnectionError(f"down-{llm.openai_api_key}")
            errors.append(e)
            raise e

        with pytest.raises(ConnectionError) as exc_info:
            await invoke_with_fallback(pool, run)
        assert exc_info.value is errors[-1]

    async def test_cooldown_expiry_restores_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _make_pool('[{"api_key":"k2","base_url":"https://b.example/v1"}]', monkeypatch)
        pool.mark_failure(1)
        assert pool._states[1].cooling
        # 冷却中的源排末尾兜底
        assert pool.ordered_candidates()[-1] == 1

        # 冷却过期后恢复原顺序
        assert pool._states[1].failed_at is not None
        pool._states[1].failed_at -= SOURCE_FAILURE_COOLDOWN_SECONDS + 1
        assert pool.ordered_candidates() == [0, 1]
