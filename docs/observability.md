# 可观测性设计

> 本文档定义 AI Town 的可观测性体系：Traces / Metrics / Logs 三支柱、埋点覆盖矩阵、LLM 专用追踪、告警。核心理念：**埋点即契约，所有关键路径必须有 Trace 覆盖**。

---

## 一、设计目标

| 目标         | 说明                                                   |
| ------------ | ------------------------------------------------------ |
| 全链路追踪   | 每个 Tick / Action / LLM 调用 / 本地工具调用都有 Trace |
| LLM 专用追踪 | Token / Cost / Prompt / Completion 可审计              |
| 结构化日志   | 全部日志带 `trace_id`，可在 Grafana 与 Trace 联动跳转  |
| 指标告警     | 关键指标超阈值自动告警                                 |
| 调试友好     | 可基于 trace_id 回放角色决策全过程                     |

---

## 二、可观测性架构（三支柱）

```text
┌─────────────────────────────────────────────────────────────────┐
│                      应用 (Python / LangChain)                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐│
│  │  OTel SDK   │  │  Langfuse   │  │  structlog (结构化日志) ││
│  │  (Traces+   │  │  SDK (LLM   │  │  输出 JSON 到 stdout    ││
│  │   Metrics)  │  │   专用)     │  │                         ││
│  └─────────────┘  └─────────────┘  └─────────────────────────┘│
└─────────────────────────────┬───────────────────────────────────┘
                              │ OTLP / HTTP          │ stdout
┌─────────────────────────────▼─────────┐ ┌──────────▼──────────┐
│  OTLP HTTP 直推 Jaeger（无 Collector）│ │   Grafana Alloy     │
│  BatchSpanProcessor + 头采样          │ │  统一采集日志/指标   │
└─────────────────────────────┬─────────┘ └──────────┬──────────┘
                              │                      │
        ┌─────────────────────┼──────────┐           │
        │                     │          │           │
┌───────▼───────┐    ┌────────▼───────┐ │   ┌───────▼───────┐
│   Traces      │    │   Metrics      │ │   │    Logs       │
│   Jaeger      │    │  Prometheus    │ │   │   Loki        │
│   / Langfuse  │    │  (拉取)        │ │   │  (推送到)     │
└───────────────┘    └────────┬───────┘ │   └───────┬───────┘
                              │         │           │
                              └────┬────┘           │
                                   │                │
                            ┌──────▼────────────────▼──────┐
                            │        Grafana 统一面板       │
                            │  Trace / Metrics / Logs 联动  │
                            └─────────────────────────────┘
```

### 各组件职责

| 组件           | 职责                                                     | 版本    |
| -------------- | -------------------------------------------------------- | ------- |
| OTel SDK       | 应用层自动/手动埋点，生成 Span                           | 1.28+   |
| Langfuse SDK   | LLM 专用追踪（Prompt/Completion/Token/Cost）             | 2.x（锁 `>=2,<3`） |
| structlog      | 结构化 JSON 日志（含 trace_id）                          | 最新    |
| （无 Collector） | 后端经 OTLP HTTP 直推 Jaeger；tail sampling 需先引入 Collector | —   |
| Jaeger         | 分布式链路追踪存储与查询                                 | 最新    |
| Langfuse       | LLM 调用观测（与 Jaeger 互补）                           | 2.x（锁 `>=2,<3`） |
| Prometheus     | 指标采集与存储（拉取模式）                               | 最新    |
| Grafana Alloy  | 统一可观测性收集器（取代 Promtail），采集日志/指标/Trace | 最新    |
| **Loki**       | **日志聚合存储，LogQL 查询**                             | **3.x** |
| Grafana        | 统一可视化面板，Trace/Metrics/Logs 联动                  | 12.x    |

---

## 三、埋点覆盖矩阵

手动 span 统一由 `src/observability/tracing.py` 的 `@trace_span` 装饰器创建，
属性集固定为 `code.function`、`args.*`（repr 截断 200 字符）、`result.type`，
异常自动 `record_exception` 后原样上抛；span 名称即下表契约：

| 埋点位置         | Span 名称          | 装饰目标                                          |
| ---------------- | ------------------ | ------------------------------------------------- |
| World Tick       | `world.tick`       | `WorldEngine._execute_tick`                       |
| Character Tick   | `character.tick`   | `CharacterTickEngine.tick_character`              |
| 角色感知         | `character.perceive` | `CharacterTickEngine._perceive`                 |
| 角色决策         | `character.decide` | `CharacterTickEngine._decide`                     |
| Action 执行      | `action.execute`   | `CharacterTickEngine._execute_action`             |
| 记忆写入         | `memory.write`     | `CharacterTickEngine._memorize`                   |
| 本地工具调用     | `tool.call`        | `CharacterTickEngine._execute_tool`               |
| 消息处理         | `message.process`  | `MessageService.handle_user_message` 入口         |
| 消息推送         | `message.push`     | `WebSocketManager.send_to_user`（Web 通道唯一埋点；QQ 通道经 OneBot 反向 WS 由外部实现发送，不加手动 span） |
| LLM 调用         | `llm.generate`     | `LLMClient.chat_with_usage`                       |
| Embedding 批处理 | `embedding.batch`  | `EmbeddingWorker._process_batch`                  |

除上述手动 span 外：

- **FastAPI 自动 instrumentation** 为每个 HTTP 请求生成 server span；
- **AsyncPG 自动 instrumentation** 为每条 SQL 生成 client span——因此不再手写
  `db.tx` span（避免双层重复）；
- 模块系统无运行时 hook 点，`module.*` span 不存在，矩阵中不再列出。

---

## 四、Span 上下文传播

### 4.1 角色决策链路示例

```text
trace_id: abc123
├── span: character.tick
│   ├── span: character.perceive
│   │   └── AsyncPG 自动 span（状态/计划/记忆检索查询）
│   ├── span: character.decide
│   │   └── span: llm.generate（结构化决策调用）
│   ├── span: tool.call（ReAct 循环命中工具时，内部可再嵌 llm.generate）
│   ├── span: action.execute
│   │   └── AsyncPG 自动 span（ActionRecord / 状态更新事务）
│   └── span: memory.write
```

### 4.2 trace_id 注入日志

所有日志强制带 `trace_id` 与 `span_id`，便于从 Trace 跳转到 Loki 日志：

```python
import structlog
logger = structlog.get_logger()

logger.info("action_executed",
            character_id=str(cid),
            action_id=action.id,
            trace_id=current_trace_id(),
            tx_id=tx_id)
```

---

## 五、日志体系（Loki + Grafana Alloy）

> **Grafana Alloy** 是新一代可观测性收集器，统一取代 Promtail、Prometheus Agent、OTel Collector，支持日志/指标/Trace 统一采集与推送。

### 5.1 结构化 JSON 日志

应用输出到 stdout，Alloy 采集后推送 Loki：

```json
{
  "timestamp": "2026-07-06T08:00:00.123Z",
  "level": "info",
  "logger": "core.action_system",
  "message": "action_executed",
  "trace_id": "abc123",
  "span_id": "def456",
  "character_id": "7f9c...e3",
  "action_id": "move_to_cafe",
  "tx_id": "tx_456",
  "duration_ms": 230
}
```

### 5.2 日志级别

| 级别       | 适用                          |
| ---------- | ----------------------------- |
| `DEBUG`    | 详细调试信息（默认不输出）    |
| `INFO`     | 正常流程关键节点              |
| `WARN`     | 可恢复异常（重试、降级）      |
| `ERROR`    | 错误（Action 失败、模块异常） |
| `CRITICAL` | 系统级故障（DB 不可用）       |

### 5.3 Grafana Alloy 采集配置

```yaml
# alloy.config.alloy (Alloy 使用 .alloy 扩展名)
// 日志采集 → Loki
loki.write "default" {
endpoint {
url = "http://loki:3100/loki/api/v1/push"
}
}

// Docker 容器日志发现
discovery.docker "backend" {
host = "unix:///var/run/docker.sock"
filter {
name = "label"
values = ["com.docker.compose.service=backend"]
}
}

// 日志管道：JSON 解析 → 标签提取 → Loki 推送
loki.source.docker "backend_logs" {
forward_to = [loki.process.backend_pipeline.receiver]

discovery = discovery.docker.backend
}

loki.process "backend_pipeline" {
forward_to = [loki.write.default.receiver]

// JSON 解析
stage.json {
expressions = {
level = "level"
trace_id = "trace_id"
logger = "logger"
character_id = "character_id"
}
}

// 提取为标签
stage.labels {
values = {
level = "level"
logger = "logger"
}
}

// 结构化元数据（Loki 3.x）
stage.static_labels {
values = {
service = "backend"
}
}

// 结构化元数据写入
stage.loki_metadata {
values = {
trace_id = "trace_id"
character_id = "character_id"
}
}
}
```

> Alloy 配置使用 HCL-like 语法（`.alloy` 文件），相比 Promtail YAML 更模块化，支持管道组合与多输出。详见 Grafana Alloy 官方文档。

### 5.4 LogQL 查询示例

```logql
# 查某次 Trace 的全部日志
{service="backend"} | trace_id="abc123"

# 查某角色最近 5 分钟错误日志
{service="backend", level="ERROR"} | character_id="7f9c..." | logfmt

# 统计各模块错误日志速率
sum by (logger) (rate({service="backend", level="ERROR"}[5m]))

# 慢事务日志
{service="backend"} | json | duration_ms > 1000
```

### 5.5 Grafana Trace ↔ Logs 联动

Grafana 数据源配置：

- Jaeger 数据源关联 Loki（`Trace to Logs`），点 Span 可直接跳转 Loki 查该 trace_id 日志；
- Loki 数据源关联 Jaeger（`Logs to Trace`），日志中 `trace_id` 字段可跳转 Trace。

---

## 六、关键指标（Prometheus）

### 6.1 指标清单

| 指标名                     | 类型      | 说明              | 告警阈值          |
| -------------------------- | --------- | ----------------- | ----------------- |
| `character_tick_duration`  | Histogram | 角色 Tick 耗时    | p95 > 5s          |
| `llm_call_duration`        | Histogram | LLM 调用延迟      | p95 > 10s         |
| `llm_token_usage`          | Counter   | Token 消耗        | 日环比 > 50%      |
| `llm_cost_total`           | Counter   | LLM 成本累计      | 日成本 > 预算 80% |
| `tool_error_rate`          | Gauge     | 本地工具错误率    | > 5%              |
| `tool_latency`             | Histogram | 本地工具延迟      | p95 > 1s          |
| `action_execution_failed`  | Counter   | Action 执行失败   | > 10/h            |
| `llm_daily_budget_usd`     | Gauge     | 日预算上限（镜像 `LLM_DAILY_BUDGET_USD`，启动时设置） | 与 `llm_cost_total_usd` 组合计算消耗比 |
| `memory_retrieve_latency`  | Histogram | 记忆检索延迟      | p95 > 200ms       |
| `db_tx_duration`           | Histogram | DB 事务耗时       | p95 > 500ms       |
| `db_connection_pool_usage` | Gauge     | 连接池占用率      | > 80%             |
| `module_unhealthy`         | Gauge     | 不健康模块数      | > 0               |
| `active_characters`        | Gauge     | 活跃角色数        | —                 |
| `message_response_time`    | Histogram | 消息回复延迟      | p95 > 15s         |
| `redis_ops_per_sec`        | Gauge     | Redis QPS         | —                 |
| `loki_ingest_rate`         | Gauge     | Loki 日志摄入速率 | —                 |

### 6.2 自定义业务指标

| 指标                           | 说明                                  |
| ------------------------------ | ------------------------------------- |
| `character_energy_avg`         | 角色平均精力（健康度参考）            |
| `action_category_distribution` | Action 分类分布（生活/工作/社交占比） |
| `relation_strength_avg`        | 平均关系强度                          |
| `memory_reflection_rate`       | 已反思记忆占比                        |

---

## 七、Grafana 面板

### 7.1 预置面板

| 面板             | 内容                                           | 数据源                |
| ---------------- | ---------------------------------------------- | --------------------- |
| Overview         | 活跃角色数、Tick QPS、LLM 调用 QPS、错误率     | Prometheus            |
| LLM              | Token 用量、成本、模型分布、延迟分布           | Prometheus + Langfuse |
| Character Tick   | Tick 耗时分布、决策模型分布、Action 分类分布   | Prometheus            |
| Memory           | 检索延迟、记忆总量、反思触发率                 | Prometheus            |
| 工具             | 本地工具调用 QPS、错误率、延迟、命名空间健康   | Prometheus            |
| DB               | 事务耗时、连接池、慢查询、分区表大小           | Prometheus            |
| Message          | 消息量、回复延迟、推送量、平台分布             | Prometheus            |
| **Logs**         | **实时日志流、按 service/level/trace_id 过滤** | **Loki**              |
| **Trace Detail** | **Trace 链路 + 关联日志**                      | **Jaeger + Loki**     |

### 7.2 告警通道

| 通道       | 适用         |
| ---------- | ------------ |
| 飞书机器人 | 默认告警通道 |
| 邮件       | 严重告警     |
| PagerDuty  | 生产事故升级 |

---

## 八、Langfuse LLM 追踪

### 8.1 追踪内容

| 字段         | 说明                                         |
| ------------ | -------------------------------------------- |
| `name`       | 调用场景（character.decide / message.reply） |
| `model`      | 模型名                                       |
| `prompt`     | 完整 Prompt（含记忆、状态）                  |
| `completion` | LLM 输出                                     |
| `tokens`     | input / output tokens                        |
| `cost`       | 调用成本                                     |
| `metadata`   | character_id / trace_id / session_id         |

### 8.2 集成方式

实际实现（`src/observability/langfuse_tracing.py`）在每次 LLM 调用完成后
手动上报 generation，并自动挂到当前 Tick 的根 trace 下：

```python
from src.observability.langfuse_tracing import trace_llm_call

trace_llm_call(
    model=model,
    prompt=prompt,
    response=content,
    tokens=total_tokens,
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
    cost_usd=estimated_cost,
    latency_ms=int(elapsed * 1000),
)
```

Langfuse 与 OTel 通过 metadata 中的 `otel_trace_id` 关联，可在 Jaeger 与 Langfuse 间互查。

---

## 九、调试回放

### 9.1 基于 Trace 的回放

给定 `trace_id`，可还原：

1. 角色当时的状态（从 `character.tick` / `character.perceive` span 属性与 Loki 日志）；
2. 检索到的记忆（`character.perceive` 阶段的 AsyncPG 自动 span + PG 复查）；
3. LLM 的完整 Prompt 与输出（从 Langfuse）；
4. Action 执行结果（从 `action.execute` span）；
5. 写入的数据库行（AsyncPG 自动 span + Loki 日志）；
6. 全部相关日志（Loki 按 `trace_id` 过滤）。

### 9.2 基于快照的世界回放

结合 `world_events` 差分事件表与 `action_records`，可重放历史某段时间内小镇的演化过程。详见 [世界引擎设计](world-engine.md#暂停--恢复--回放)。

---

## 十、采样策略

**头部采样已改为 always-on，是否保留链路由 OTel Collector 尾采样统一决定：**

- 后端采样器为 `ParentBased(ALWAYS_ON)`：本地全量生成 span 并发往 Collector，
  错误 Span 不可能再因头部采样被提前丢弃；
- `ParentBased` 尊重跨服务传播的上游采样决定，避免同一链路各段判定不一致；
- 真正的保留决策在 Collector 的 `tail_sampling`：**错误必采 / >2s 必采 / 其余 20% 基线**，
  决策等待 5s 聚合整条 trace 后统一判定（见 `docker/observability/otel-collector.yml`）；
- 日志不受采样影响——structlog 只要 SpanContext 有效即注入 `trace_id`/`span_id`
  （不检查 `is_recording()`，R5-M17），未被采样的流量仍可从 Loki 按 trace_id
  还原事件序列，只是 Jaeger 中没有对应链路。

> 注意：未部署 observability profile（无 Collector）时，全量 span 会发往
> `OTEL_ENDPOINT` 指向的后端存储，请确保该端点自身具备容量或显式关闭
> （`OTEL_ENDPOINT` 留空即禁用 tracing）。

---

## 十一、部署实现（Docker Compose）

> 以下配置已落地，位于 `docker/observability/` 目录与根目录 `docker-compose.yml`（`--profile observability`）。

### 11.1 文件清单

| 文件                                                                  | 说明                                                                     |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `docker/observability/prometheus.yml`                                 | Prometheus 采集配置（后端 /metrics + 自身 + Loki + Jaeger + Alloy）      |
| `docker/observability/loki-config.yml`                                | Loki 单节点配置（TSDB v13 schema，7 天保留，文件系统存储）               |
| `docker/observability/alloy.config.alloy`                             | Grafana Alloy 采集管道（Docker 日志 → JSON 解析 → Loki 推送）            |
| `docker/observability/grafana/datasources/datasources.yml`            | Grafana 数据源自动配置（Prometheus + Loki + Jaeger，含 Trace↔Logs 联动） |
| `docker/observability/grafana/dashboards.yml`                         | Grafana Dashboard Provider 配置                                          |
| `docker/observability/grafana/dashboards/ai-town-overview.json`       | 总览面板（指标 + 实时日志流）                                            |
| `docker/observability/grafana/dashboards/ai-town-llm.json`            | LLM 监控面板（Token / Cost / 延迟 / 错误率）                             |
| `docker/observability/grafana/dashboards/ai-town-character-tick.json` | 角色 Tick 面板（耗时 / 成败 / Action 分布）                              |

### 11.2 Docker Compose 服务

| 服务         | 镜像                            | 端口                    | 说明                                     |
| ------------ | ------------------------------- | ----------------------- | ---------------------------------------- |
| `prometheus` | `prom/prometheus:latest`        | 9090                    | 指标采集与存储                           |
| `loki`       | `grafana/loki:3.0.0`            | 3100                    | 日志聚合存储                             |
| `jaeger`     | `jaegertracing/all-in-one:1.60` | 16686 (UI), 4318 (OTLP) | 链路追踪存储与查询                       |
| `alloy`      | `grafana/alloy:latest`          | 12345                   | 统一日志采集器（Docker 容器日志 → Loki） |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.104.0` | 4317/4318 (OTLP) | 尾采样（错误必采 / >2s / 20% 基线）后入 Jaeger |
| `langfuse-db` | `postgres:18-alpine`           | —（仅内网）             | Langfuse 专用 PostgreSQL                 |
| `langfuse-web` | `langfuse/langfuse:2`         | 3001                    | Langfuse UI / LLM Trace 查询             |
| `langfuse-worker` | `langfuse/langfuse-worker:2` | —                     | Langfuse 异步任务处理                    |
| `grafana`    | `grafana/grafana:12.0.0`        | 3000                    | 统一可视化面板                           |

### 11.3 启动方式

```bash
# 统一入口（所有平台）
docker compose --profile observability up -d
```

### 11.4 访问地址

| 服务       | URL                    | 账号             |
| ---------- | ---------------------- | ---------------- |
| Grafana    | http://localhost:3000  | admin / admin123 |
| Prometheus | http://localhost:9090  | —                |
| Jaeger UI  | http://localhost:16686 | —                |
| Loki API   | http://localhost:3100  | —                |
| Alloy UI   | http://localhost:12345 | —                |
| Langfuse   | http://localhost:3001  | 首次访问自行注册 |

### 11.5 后端接入

`.env` 中配置 OTel endpoint 指向 Jaeger 的 OTLP HTTP 接收端口：

```env
# 本地裸机运行（Jaeger 宿主映射 14318:4318 时也可用 4318 直连容器端口映射）
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

> Docker Compose 部署时，backend 容器内应填服务名直连：`http://jaeger:4318`。
> 照抄 `localhost:4318` 会导致 Trace 发送静默失败。

后端启动后，OTel SDK 自动将 Trace 发送到 Jaeger，Prometheus 采集 `/metrics` 端点，Alloy 采集后端 stdout 日志推送到 Loki。

### 11.6 Grafana 面板预览

**AI Town Overview**（默认面板）包含：

- 活跃角色数 / World Tick ID / Redis 状态 / LLM 费用 / HTTP QPS / 5xx 错误率（6 个 Stat）
- World Tick 耗时 p50/p95/p99（Timeseries）
- Character Tick 耗时 p50/p95/p99（Timeseries）
- LLM 调用耗时 by model（Timeseries）
- LLM Token 消耗速率 by model+type（Timeseries）
- Action 成功率 / 消息处理速率（Stat）
- DB 查询耗时 p95（Timeseries）
- HTTP 请求耗时 by path（Timeseries）
- 实时日志流（Logs panel，Loki 数据源）

**AI Town - LLM Monitor** 包含：

- LLM QPS / 成本累计 / Token 速率 / 延迟分布 / 错误率 / LLM 日志流

**AI Town - Character Tick** 包含：

- Tick 耗时分布 / Tick 频率 / 错误次数 / Action 执行统计 / Tick 日志流

### 11.7 Trace ↔ Logs 联动

Grafana 数据源已配置双向联动：

1. **Trace → Logs**：在 Jaeger Trace 视图中点击任意 Span，自动跳转 Loki 查询该 `trace_id` 的所有日志
2. **Logs → Trace**：在 Loki 日志视图中，日志行的 `trace_id` 字段显示为可点击链接，跳转 Jaeger 查看完整链路

配置位于 `datasources.yml`：

- Loki 数据源 `derivedFields` 提取 `trace_id` 并关联 Jaeger（uid: `jaeger`）
- Jaeger 数据源 `tracesToLogs` 关联 Loki（uid: `loki`），按 `trace_id` 标签过滤

### 11.8 Langfuse（LLM Trace 自托管）

Langfuse 属于 observability profile（自带专用 PG，数据落 `./data/langfuse-db/`，
不暴露宿主端口）。部署与接入步骤：

1. `.env` 填入三个 Langfuse 容器密钥（`openssl rand -base64 32` 生成）：
   `LANGFUSE_NEXTAUTH_SECRET` / `LANGFUSE_SALT_KEY` / `LANGFUSE_ENCRYPTION_KEY`
   （compose 以 `${VAR:?}` 强制必填）；
2. `docker compose --profile observability up -d`；
3. 打开 http://localhost:3001 注册首个账号，在 UI 创建 API Key；
4. 将 pk/sk 填入 `.env` 的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
   （`LANGFUSE_HOST=http://localhost:3001`），重启 backend 生效。

容器三密钥（加密 Langfuse 自身数据）与 API Key（backend 上报凭据）相互独立。

---

## 十二、前端监控页面集成

后端提供两个轻量级端点，让前端 Dashboard 的 `/monitoring` 页面无需对接 Grafana 即可直接消费日志与指标：

### 12.1 `/api/v1/admin/logs` — 日志读取

读取 `data/logs/backend.log` 文件最后 N 行，支持级别过滤：

```http
GET /api/v1/admin/logs?lines=200&level=ERROR
```

### 12.2 `/api/v1/admin/metrics-detail` — 指标结构化

解析 `/metrics` Prometheus 文本格式，转换为按类别分组的 JSON：

| 类别        | 包含指标                                 |
| ----------- | ---------------------------------------- |
| `world`     | tick_total / tick_duration_p95           |
| `character` | active_count / tick_duration_p95         |
| `action`    | success_rate / total                     |
| `llm`       | call_total / cost_total_usd / error_rate |
| `message`   | processed_total / response_time_p95      |
| `http`      | request_total / error_5xx_rate           |

### 12.3 前端 `/monitoring` 页面

- 集成 Grafana iframe（如已部署可观测性栈）；
- 同时提供原生日志面板与指标卡片，直接消费后端 `/admin/logs` 与 `/admin/metrics-detail`；
- 采用与全站一致的 Glassmorphism 风格（樱花粉/天空蓝/暮光紫主题）。

---

## 十四、日志级别使用规范

### 14.1 级别定义

| 级别     | 使用场景                   | 示例                                                                         |
| -------- | -------------------------- | ---------------------------------------------------------------------------- |
| DEBUG    | 开发调试信息，生产环境关闭 | `logger.debug("world_state_loaded", tick_id=self.tick_id)`                   |
| INFO     | 正常业务流程关键节点       | `logger.info("character_tick_completed", character_id=..., duration_ms=...)` |
| WARNING  | 异常但可恢复，需关注       | `logger.warning("rate_limit_exceeded", key=..., remaining=0)`                |
| ERROR    | 异常且不可恢复，需立即处理 | `logger.error("redis_connection_failed", error=str(e), exc_info=True)`       |
| CRITICAL | 系统级故障，需紧急处理     | `logger.critical("all_llm_sources_unavailable")`                             |

### 14.2 强制规范

1. **ERROR 级别必须包含 exc_info=True**：确保堆栈信息被记录
2. **结构化日志必须使用 key=value 格式**：`logger.info("event_name", key1=value1, key2=value2)`
3. **禁止使用 f-string 拼接日志内容**：错误做法 `logger.info(f"Tick {tick_id} completed")`，正确做法 `logger.info("tick_completed", tick_id=tick_id)`
4. **敏感信息脱敏**：URL 中的密码、API Key、JWT Token 不得出现在日志中
5. **事件名使用 snake_case**：`logger.info("character_tick_started")` 而非 `logger.info("CharacterTickStarted")`

---

## 十三、相关文档

| 主题            | 文档                                         |
| --------------- | -------------------------------------------- |
| 世界引擎埋点    | [world-engine.md](world-engine.md)           |
| Action 系统埋点 | [action-system.md](action-system.md)         |
| 部署可观测组件  | [deployment.md](deployment.md)               |
| Docker 部署     | [docker-deployment.md](docker-deployment.md) |
| 配置参考        | [config-reference.md](config-reference.md)   |
| API 端点        | [api-spec.md](api-spec.md)                   |
