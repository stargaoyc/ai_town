"""Prometheus 指标定义与 FastAPI 集成

使用 prometheus_client 暴露指标端点 /metrics，监控：
- World Tick 耗时与成败
- Character Tick 耗时与成败
- Action 执行耗时与成败
- LLM 调用耗时/Token/费用
- 消息处理耗时与成败
- 数据库查询耗时
- 系统状态（活跃角色/Redis/Tick ID）
- HTTP 请求耗时/状态码/路径

集成方式（在 main.py 中调用）：
    from src.observability import setup_metrics
    setup_metrics(app)
"""

from __future__ import annotations

import time

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog import get_logger

logger = get_logger(__name__)

# === World Tick 指标 ===
WORLD_TICK_DURATION = Histogram(
    "ai_town_world_tick_duration_seconds",
    "World Tick 执行耗时",
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)
WORLD_TICK_TOTAL = Counter(
    "ai_town_world_tick_total",
    "World Tick 总执行次数",
)
WORLD_TICK_ERRORS = Counter(
    "ai_town_world_tick_errors_total",
    "World Tick 错误次数",
)

# === Character Tick 指标 ===
CHARACTER_TICK_DURATION = Histogram(
    "ai_town_character_tick_duration_seconds",
    "单个角色 Tick 执行耗时",
    buckets=[0.1, 0.5, 1, 2, 5, 10],
)
CHARACTER_TICK_TOTAL = Counter(
    "ai_town_character_tick_total",
    "角色 Tick 总执行次数",
    ["character_id"],
)
CHARACTER_TICK_ERRORS = Counter(
    "ai_town_character_tick_errors_total",
    "角色 Tick 错误次数",
    ["character_id"],
)

# === Action 指标 ===
ACTION_EXECUTION_TOTAL = Counter(
    "ai_town_action_execution_total",
    "Action 执行总次数",
    ["action_id", "status"],  # status: success/failed
)
ACTION_EXECUTION_DURATION = Histogram(
    "ai_town_action_execution_duration_seconds",
    "Action 执行耗时",
    ["action_id"],
    buckets=[0.1, 0.5, 1, 2, 5, 10],
)

# === LLM 指标 ===
LLM_CALL_TOTAL = Counter(
    "ai_town_llm_call_total",
    "LLM 调用总次数",
    ["model", "status"],  # status: success/failed
)
LLM_CALL_DURATION = Histogram(
    "ai_town_llm_call_duration_seconds",
    "LLM 调用耗时",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)
LLM_TOKENS_USED = Counter(
    "ai_town_llm_tokens_total",
    "LLM token 消耗",
    ["model", "type"],
)
LLM_COST_TOTAL = Counter(
    "ai_town_llm_cost_total_usd",
    "LLM 总费用（USD）",
)

# === 消息指标 ===
MESSAGE_PROCESSED_TOTAL = Counter(
    "ai_town_message_processed_total",
    "消息处理总次数",
    ["platform", "status"],  # status: success/failed
)
MESSAGE_PROCESSING_DURATION = Histogram(
    "ai_town_message_processing_duration_seconds",
    "消息处理耗时",
    buckets=[0.5, 1, 2, 5, 10, 30],
)

# === 数据库指标 ===
DB_QUERY_DURATION = Histogram(
    "ai_town_db_query_duration_seconds",
    "数据库查询耗时",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1],
)

# === 系统状态指标 ===
ACTIVE_CHARACTERS = Gauge(
    "ai_town_active_characters",
    "活跃角色数量",
)
REDIS_CONNECTED = Gauge(
    "ai_town_redis_connected",
    "Redis 连接状态（1=连接, 0=断开）",
)
WORLD_TICK_ID = Gauge(
    "ai_town_world_tick_id",
    "当前 World Tick ID",
)

# === HTTP 请求指标（供 PrometheusMiddleware 使用） ===
HTTP_REQUEST_DURATION = Histogram(
    "ai_town_http_request_duration_seconds",
    "HTTP 请求耗时",
    ["method", "path", "status"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
)
HTTP_REQUEST_TOTAL = Counter(
    "ai_town_http_request_total",
    "HTTP 请求总次数",
    ["method", "path", "status"],
)


class PrometheusMiddleware:
    """纯 ASGI 中间件：记录 HTTP 请求耗时、状态码、路径

    使用纯 ASGI 实现（而非 BaseHTTPMiddleware），兼容 WebSocket 连接。
    WebSocket 请求（scope["type"] == "websocket"）直接透传，不记录指标。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan 等非 HTTP 请求直接透传
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except Exception:
            raise
        finally:
            duration = time.perf_counter() - start_time
            method = scope.get("method", "UNKNOWN")
            path = scope.get("path", "/")
            HTTP_REQUEST_DURATION.labels(
                method=method, path=path, status=status_code
            ).observe(duration)
            HTTP_REQUEST_TOTAL.labels(
                method=method, path=path, status=status_code
            ).inc()


def setup_metrics(app: FastAPI) -> None:
    """初始化 Prometheus 指标

    - 注册 Prometheus Middleware（请求耗时/状态码/路径）
    - 挂载 /metrics 端点（prometheus_client.make_asgi_app）
    """
    app.add_middleware(PrometheusMiddleware)
    app.mount("/metrics", make_asgi_app())
    logger.info("prometheus_metrics_initialized", endpoint="/metrics")
