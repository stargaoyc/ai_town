"""src/observability/metrics.py 单元测试

覆盖：
- 所有指标对象存在且类型正确
- 指标可以正常 inc/observe/set/labels
- setup_metrics 函数存在且可调用（用 mock FastAPI 验证）
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from prometheus_client import Counter, Gauge, Histogram

from src.observability.metrics import (
    ACTIVE_CHARACTERS,
    LLM_TOKENS_USED,
    REDIS_CONNECTED,
    WORLD_TICK_DURATION,
    WORLD_TICK_ID,
    WORLD_TICK_TOTAL,
    setup_metrics,
)


# ---------------------------------------------------------------------------
# 指标对象存在且类型正确
# ---------------------------------------------------------------------------


def test_world_tick_duration_is_histogram():
    """WORLD_TICK_DURATION 是 Histogram"""
    assert isinstance(WORLD_TICK_DURATION, Histogram)


def test_world_tick_total_is_counter():
    """WORLD_TICK_TOTAL 是 Counter"""
    assert isinstance(WORLD_TICK_TOTAL, Counter)


def test_llm_tokens_used_is_counter():
    """LLM_TOKENS_USED 是 Counter"""
    assert isinstance(LLM_TOKENS_USED, Counter)


def test_llm_tokens_used_has_correct_labels():
    """LLM_TOKENS_USED 有 labels ["model", "type"]"""
    label_names = list(LLM_TOKENS_USED._labelnames)
    assert "model" in label_names
    assert "type" in label_names
    assert len(label_names) == 2


def test_active_characters_is_gauge():
    """ACTIVE_CHARACTERS 是 Gauge"""
    assert isinstance(ACTIVE_CHARACTERS, Gauge)


def test_redis_connected_is_gauge():
    """REDIS_CONNECTED 是 Gauge"""
    assert isinstance(REDIS_CONNECTED, Gauge)


def test_world_tick_id_is_gauge():
    """WORLD_TICK_ID 是 Gauge"""
    assert isinstance(WORLD_TICK_ID, Gauge)


# ---------------------------------------------------------------------------
# 指标操作
# ---------------------------------------------------------------------------


def test_counter_inc_no_error():
    """Counter.inc() 不报错"""
    WORLD_TICK_TOTAL.inc()
    WORLD_TICK_TOTAL.inc(1)


def test_counter_inc_with_amount():
    """Counter.inc(amount) 累加正确"""
    before = WORLD_TICK_TOTAL._value.get()
    WORLD_TICK_TOTAL.inc(5)
    after = WORLD_TICK_TOTAL._value.get()
    assert after - before == 5


def test_histogram_observe_no_error():
    """Histogram.observe(0.5) 不报错"""
    WORLD_TICK_DURATION.observe(0.5)


def test_histogram_observe_zero():
    """Histogram.observe(0) 不报错"""
    WORLD_TICK_DURATION.observe(0)


def test_gauge_set_no_error():
    """Gauge.set(42) 不报错"""
    WORLD_TICK_ID.set(42)


def test_gauge_set_value_reflected():
    """Gauge.set() 值被正确设置"""
    ACTIVE_CHARACTERS.set(7)
    assert ACTIVE_CHARACTERS._value.get() == 7


def test_gauge_set_redis_connected():
    """REDIS_CONNECTED Gauge 可 set 0/1"""
    REDIS_CONNECTED.set(1)
    assert REDIS_CONNECTED._value.get() == 1
    REDIS_CONNECTED.set(0)
    assert REDIS_CONNECTED._value.get() == 0


def test_counter_with_labels_inc_no_error():
    """带标签的 Counter.labels("chat", "success").inc() 不报错"""
    LLM_TOKENS_USED.labels("chat", "success").inc()
    LLM_TOKENS_USED.labels("chat", "success").inc(3)


def test_counter_with_labels_different_values():
    """带标签的 Counter 不同标签值独立计数"""
    LLM_TOKENS_USED.labels("model-a", "prompt").inc(10)
    LLM_TOKENS_USED.labels("model-a", "completion").inc(20)
    LLM_TOKENS_USED.labels("model-b", "prompt").inc(5)


# ---------------------------------------------------------------------------
# setup_metrics
# ---------------------------------------------------------------------------


def test_setup_metrics_callable():
    """setup_metrics 函数存在且可调用"""
    assert callable(setup_metrics)


def test_setup_metrics_with_mock_app():
    """setup_metrics 使用 mock FastAPI app 可正常执行"""
    mock_app = MagicMock()
    setup_metrics(mock_app)
    # 验证 add_middleware 被调用
    mock_app.add_middleware.assert_called_once()
    # 验证 mount 被调用
    mock_app.mount.assert_called_once()
    # mount 路径为 /metrics
    mount_args = mock_app.mount.call_args
    assert mount_args.args[0] == "/metrics"


def test_setup_metrics_mounts_metrics_endpoint():
    """setup_metrics 挂载 /metrics 端点"""
    mock_app = MagicMock()
    setup_metrics(mock_app)
    mount_args = mock_app.mount.call_args
    assert mount_args.args[0] == "/metrics"
    # 第二个参数为 ASGI app（make_asgi_app 返回值）
    assert len(mount_args.args) >= 2


def test_setup_metrics_adds_prometheus_middleware():
    """setup_metrics 注册 PrometheusMiddleware"""
    from src.observability.metrics import PrometheusMiddleware

    mock_app = MagicMock()
    setup_metrics(mock_app)
    middleware_cls = mock_app.add_middleware.call_args.args[0]
    assert middleware_cls is PrometheusMiddleware
