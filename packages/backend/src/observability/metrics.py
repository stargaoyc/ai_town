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

# === 媒体生成指标（draw_image/generate_video；round-3 M18 成本盲区：上游 API 不回传
# token 用量、费用不可估，仅以调用量计数兜住可观测性）===
MEDIA_GENERATION_TOTAL = Counter(
    "ai_town_media_generation_total",
    "媒体生成调用次数（图片/视频）",
    ["tool", "outcome"],  # tool: draw_image/generate_video; outcome: success/failed
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
# 日预算上限镜像（无标签）：告警规则用它作分母计算消耗比，
# 预算值改配置即生效，无需改规则表达式
LLM_DAILY_BUDGET_USD = Gauge(
    "ai_town_llm_daily_budget_usd",
    "每日 LLM 成本预算上限（USD），镜像 LLM_DAILY_BUDGET_USD 配置",
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

# === 对账指标（Redis vs PG 定期 diff）===
RECONCILE_DRIFT_TOTAL = Counter(
    "ai_town_reconcile_drift_total",
    "对账发现的漂移次数（含自动修复）",
    ["kind"],  # kind: missing_key / value_drift
)
RECONCILE_REPAIR_TOTAL = Counter(
    "ai_town_reconcile_repair_total",
    "对账自动修复次数",
    ["direction"],  # direction: pg_to_redis / redis_to_pg
)

# === Embedding Worker 指标（此前只进日志，无指标观测，审查 §八盲区 5）===
EMBEDDING_EPISODES_TOTAL = Counter(
    "ai_town_embedding_episodes_total",
    "Embedding Worker 处理的记忆条数",
    ["status"],  # status: success / failed / deduped
)
EMBEDDING_BATCH_DURATION = Histogram(
    "ai_town_embedding_batch_duration_seconds",
    "Embedding 批处理耗时",
)

# === Embedding 实时维度探针（R6-L4）===
EMBEDDING_PROBE_TOTAL = Counter(
    "ai_town_embedding_probe_total",
    "Embedding 实时维度探针结果",
    ["status"],  # status: ok / dimension_mismatch / unavailable
)

# === Redis Streams 队列深度（积压与死信可观测）===
REDIS_STREAM_MESSAGES = Gauge(
    "ai_town_redis_stream_messages",
    "Redis Streams 队列长度（含死信流）",
    ["stream"],
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

# === 工具调用指标（R6-L5：单次工具执行有超时保护；以调用量计数，
# outcome 区分 success/failed/timeout，timeout 用于监控挂死工具）===
TOOL_CALL_TOTAL = Counter(
    "ai_town_tool_call_total",
    "工具调用总次数",
    ["tool", "outcome"],  # tool: 全名; outcome: success/failed/timeout
)

# === Alertmanager 告警接收（R4-M2：告警经 webhook 回流后端，不再只进 UI）===
ALERTS_RECEIVED_TOTAL = Counter(
    "ai_town_alerts_received_total",
    "从 Alertmanager webhook 接收的告警数",
    ["alertname"],
)

# === 认知有效性指标（round-7 P0-1：记忆/反思/去重/治理）===
MEMORY_RETRIEVE_LATENCY = Histogram(
    "ai_town_memory_retrieve_latency_seconds",
    "记忆检索延迟（RetrievalService.search）",
    buckets=[0.01, 0.03, 0.05, 0.1, 0.2, 0.5, 1.0],
)
MEMORY_REFLECTION_RATE = Gauge(
    "ai_town_memory_unreflected_backlog",
    "未反思记忆积压数（每次反思检查时更新；趋近 REFLECTION_THRESHOLD 提示需扩容反思池）",
)
MEMORY_WRITE_TOTAL = Counter(
    "ai_town_memory_write_total",
    "记忆写入次数",
    ["source_type"],  # action / conversation / reflection / gossip / tool
)
MEMORY_DEDUP_TOTAL = Counter(
    "ai_town_memory_dedup_total",
    "记忆去重命中次数",
    ["kind"],  # exact / paraphrase
)
MEMORY_RETENTION_TOTAL = Counter(
    "ai_town_memory_retention_total",
    "记忆治理次数",
    ["kind"],  # compressed / archived / deleted
)
LLM_SCORE_TOTAL = Counter(
    "ai_town_llm_score_total",
    "LLM 记忆评分调用次数",
    ["status"],  # success / failed
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
            # R4-M4：用路由模板替代原始路径——参数化路由的 UUID 与 404 探测
            # 各自生成新序列会把基数打爆；未匹配路由统一记为 "unmatched"
            route = scope.get("route")
            path = getattr(route, "path", None) or "unmatched"
            HTTP_REQUEST_DURATION.labels(method=method, path=path, status=status_code).observe(duration)
            HTTP_REQUEST_TOTAL.labels(method=method, path=path, status=status_code).inc()


def setup_metrics(app: FastAPI) -> None:
    """初始化 Prometheus 指标

    - 注册 Prometheus Middleware（请求耗时/状态码/路径）
    - 挂载 /metrics 端点（prometheus_client.make_asgi_app）
    """
    app.add_middleware(PrometheusMiddleware)
    app.mount("/metrics", make_asgi_app())
    logger.info("prometheus_metrics_initialized", endpoint="/metrics")
