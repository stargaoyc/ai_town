"""LLM 单价表单元测试（审查 §八：成本追踪与实际模型错配）

覆盖：
- get_model_price：按模型覆盖优先，未命中回退全局默认
- estimate_cost：按模型单价计算；未传模型用全局默认
- LLM_MODEL_PRICES 非法输入降级为空表
"""

import pytest

from src.config import settings
from src.llm.client import _parse_model_prices, estimate_cost, get_model_price

# 全局默认单价（USD / 1M tokens），与 config.py 默认值一致
_DEFAULT_IN = 0.5 / 1_000_000
_DEFAULT_OUT = 1.5 / 1_000_000


@pytest.fixture(autouse=True)
def _clean_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_model_prices", "", raising=False)
    monkeypatch.setattr(settings, "llm_price_input_per_mtoken", 0.5, raising=False)
    monkeypatch.setattr(settings, "llm_price_output_per_mtoken", 1.5, raising=False)


def test_fallback_to_global_default() -> None:
    assert get_model_price("gpt-4o") == (_DEFAULT_IN, _DEFAULT_OUT)
    assert get_model_price(None) == (_DEFAULT_IN, _DEFAULT_OUT)


def test_model_override_wins() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        settings,
        "llm_model_prices",
        '{"gpt-4o": {"input": 2.5, "output": 10.0}}',
        raising=False,
    )
    try:
        assert get_model_price("gpt-4o") == (2.5 / 1_000_000, 10.0 / 1_000_000)
        assert get_model_price("other-model") == (_DEFAULT_IN, _DEFAULT_OUT)
    finally:
        monkeypatch.undo()


def test_estimate_cost_uses_model_price() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "llm_model_prices", '{"m": {"input": 1.0, "output": 2.0}}', raising=False)
    try:
        # 1M input + 1M output → $3.0
        assert estimate_cost(1_000_000, 1_000_000, model="m") == pytest.approx(3.0)
        # 未传模型走全局默认 → $2.0
        assert estimate_cost(1_000_000, 1_000_000) == pytest.approx(2.0)
    finally:
        monkeypatch.undo()


def test_parse_malformed_json_returns_empty() -> None:
    assert _parse_model_prices("not-json") == {}
    assert _parse_model_prices("[1,2]") == {}


def test_parse_skips_invalid_entries() -> None:
    raw = '{"good": {"input": 1, "output": 2}, "bad": {"input": 1}, "worse": "x"}'
    assert _parse_model_prices(raw) == {"good": {"input": 1.0, "output": 2.0}}
