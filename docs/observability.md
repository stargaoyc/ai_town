# 可观测性设计

> 本文档定义 AI Town 的可观测性体系：Traces / Metrics / Logs 三支柱、埋点覆盖矩阵、LLM 专用追踪、告警。核心理念：**埋点即契约，所有关键路径必须有 Trace 覆盖**。

---

## 一、设计目标

| 目标 | 说明 |
|------|------|
| 全链路追踪 | 每个 Tick / Action / LLM 调用 / MCP 调用都有 Trace |
| LLM 专用追踪 | Token / Cost / Prompt / Completion 可审计 |
| 结构化日志 | 全部日志带 `trace_id`，可在 Grafana 与 Trace 联动跳转 |
| 指标告警 | 关键指标超阈值自动告警 |
| 调试友好 | 可基于 trace_id 回放角色决策全过程 |

---

## 二、可观测性架构（三支柱）

```text
┌─────────────────────────────────────────────────────────────────┐
│                      应用 (Python / LangGraph)                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐│
│  │  OTel SDK   │  │  Langfuse   │  │  structlog (结构化日志) ││
│  │  (Traces+   │  │  SDK (LLM   │  │  输出 JSON 到 stdout    ││
│  │   Metrics)  │  │   专用)     │  │                         ││
│  └─────────────┘  └─────────────┘  └─────────────────────────┘│
└─────────────────────────────┬───────────────────────────────────┘
                              │ OTLP / HTTP          │ stdout
┌─────────────────────────────▼─────────┐ ┌──────────▼──────────┐
│        OTel Collector                 │ │   Grafana Alloy     │
│   接收 → 批处理 → 采样 → 导出         │ │  统一采集日志/指标   │
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

| 组件 | 职责 | 版本 |
|------|------|------|
| OTel SDK | 应用层自动/手动埋点，生成 Span | 1.28+ |
| Langfuse SDK | LLM 专用追踪（Prompt/Completion/Token/Cost） | 3.x |
| structlog | 结构化 JSON 日志（含 trace_id） | 最新 |
| OTel Collector | 接收 OTLP，批处理/采样/过滤，导出到后端 | 最新 |
| Jaeger | 分布式链路追踪存储与查询 | 最新 |
| Langfuse | LLM 调用观测（与 Jaeger 互补） | 3.x |
| Prometheus | 指标采集与存储（拉取模式） | 最新 |
| Grafana Alloy | 统一可观测性收集器（取代 Promtail），采集日志/指标/Trace | 最新 |
| **Loki** | **日志聚合存储，LogQL 查询** | **3.x** |
| Grafana | 统一可视化面板，Trace/Metrics/Logs 联动 | 12.x |

---

## 三、埋点覆盖矩阵

| 埋点位置 | Span 名称 | 关键属性 |
|----------|-----------|----------|
| World Tick | `world.tick` | `tick_id`, `weather`, `time_advance` |
| Character Tick | `character.tick` | `character_id`, `tick_duration` |
| 角色感知 | `character.perceive` | `character_id`, `memories_retrieved` |
| 角色决策 | `character.decide` | `character_id`, `candidates_count`, `model` |
| LLM 调用 | `llm.generate` | `model_name`, `tokens`, `temperature`, `cost` |
| Action 决策 | `action.decision` | `character_id`, `action_name`, `reason` |
| Action 执行 | `action.execute` | `action_id`, `duration`, `success`, `tx_id` |
| 记忆写入 | `memory.write` | `character_id`, `importance`, `source_type` |
| 记忆检索 | `memory.retrieve` | `character_id`, `query`, `top_k`, `latency_ms` |
| 反思生成 | `memory.reflect` | `character_id`, `memory_count` |
| MCP 工具调用 | `mcp.tool.call` | `tool_name`, `server_url`, `latency`, `success` |
| 消息处理 | `message.process` | `platform`, `session_id`, `response_time` |
| 消息推送 | `message.push` | `character_id`, `target_user_id`, `reason` |
| 模块操作 | `module.{enable\|disable\|call}` | `module_name`, `status` |
| 模块健康检查 | `module.health_check` | `module_name`, `status` |
| DB 事务 | `db.tx` | `repo`, `op`, `latency_ms`, `rows` |

---

## 四、Span 上下文传播

### 4.1 角色决策链路示例

```text
trace_id: abc123
├── span: character.tick (character_id=7f9c, duration=2.3s)
│   ├── span: character.perceive (memories_retrieved=8)
│   │   └── span: memory.retrieve (top_k=10, latency=18ms)
│   ├── span: character.decide (candidates_count=5, model=gpt-4o)
│   │   ├── span: llm.generate (tokens=850, cost=0.012)
│   │   └── span: mcp.tool.call (tool=search_web, latency=1.2s)
│   └── span: action.execute (action_id=move_to_cafe, tx_id=tx_456)
│       ├── span: db.tx (repo=action_repo, op=insert, rows=1)
│       ├── span: db.tx (repo=memory_repo, op=insert, rows=1)
│       └── span: memory.write (importance=6)
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

| 级别 | 适用 |
|------|------|
| `DEBUG` | 详细调试信息（默认不输出） |
| `INFO` | 正常流程关键节点 |
| `WARN` | 可恢复异常（重试、降级） |
| `ERROR` | 错误（Action 失败、模块异常） |
| `CRITICAL` | 系统级故障（DB 不可用） |

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

| 指标名 | 类型 | 说明 | 告警阈值 |
|--------|------|------|----------|
| `character_tick_duration` | Histogram | 角色 Tick 耗时 | p95 > 5s |
| `llm_call_duration` | Histogram | LLM 调用延迟 | p95 > 10s |
| `llm_token_usage` | Counter | Token 消耗 | 日环比 > 50% |
| `llm_cost_total` | Counter | LLM 成本累计 | 日成本 > 预算 80% |
| `mcp_tool_error_rate` | Gauge | MCP 工具错误率 | > 5% |
| `mcp_tool_latency` | Histogram | MCP 工具延迟 | p95 > 5s |
| `action_execution_failed` | Counter | Action 执行失败 | > 10/h |
| `memory_retrieve_latency` | Histogram | 记忆检索延迟 | p95 > 200ms |
| `db_tx_duration` | Histogram | DB 事务耗时 | p95 > 500ms |
| `db_connection_pool_usage` | Gauge | 连接池占用率 | > 80% |
| `module_unhealthy` | Gauge | 不健康模块数 | > 0 |
| `active_characters` | Gauge | 活跃角色数 | — |
| `message_response_time` | Histogram | 消息回复延迟 | p95 > 15s |
| `redis_ops_per_sec` | Gauge | Redis QPS | — |
| `loki_ingest_rate` | Gauge | Loki 日志摄入速率 | — |

### 6.2 自定义业务指标

| 指标 | 说明 |
|------|------|
| `character_energy_avg` | 角色平均精力（健康度参考） |
| `action_category_distribution` | Action 分类分布（生活/工作/社交占比） |
| `relation_strength_avg` | 平均关系强度 |
| `memory_reflection_rate` | 已反思记忆占比 |

---

## 七、Grafana 面板

### 7.1 预置面板

| 面板 | 内容 | 数据源 |
|------|------|--------|
| Overview | 活跃角色数、Tick QPS、LLM 调用 QPS、错误率 | Prometheus |
| LLM | Token 用量、成本、模型分布、延迟分布 | Prometheus + Langfuse |
| Character Tick | Tick 耗时分布、决策模型分布、Action 分类分布 | Prometheus |
| Memory | 检索延迟、记忆总量、反思触发率 | Prometheus |
| MCP | 工具调用 QPS、错误率、延迟、各 Server 健康 | Prometheus |
| DB | 事务耗时、连接池、慢查询、分区表大小 | Prometheus |
| Message | 消息量、回复延迟、推送量、平台分布 | Prometheus |
| **Logs** | **实时日志流、按 service/level/trace_id 过滤** | **Loki** |
| **Trace Detail** | **Trace 链路 + 关联日志** | **Jaeger + Loki** |

### 7.2 告警通道

| 通道 | 适用 |
|------|------|
| 飞书机器人 | 默认告警通道 |
| 邮件 | 严重告警 |
| PagerDuty | 生产事故升级 |

---

## 八、Langfuse LLM 追踪

### 8.1 追踪内容

| 字段 | 说明 |
|------|------|
| `name` | 调用场景（character.decide / message.reply） |
| `model` | 模型名 |
| `prompt` | 完整 Prompt（含记忆、状态） |
| `completion` | LLM 输出 |
| `tokens` | input / output tokens |
| `cost` | 调用成本 |
| `metadata` | character_id / trace_id / session_id |

### 8.2 集成方式

```python
from langfuse import Langfuse
from langfuse.openai import openai

langfuse = Langfuse()

# 使用 langfuse 包装的 openai 客户端, 自动追踪
response = await openai.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    metadata={"character_id": str(cid), "trace_id": trace_id},
)
```

Langfuse 与 OTel 通过 `trace_id` 关联，可在 Jaeger 中跳转到 Langfuse 查看 LLM 详情。

---

## 九、调试回放

### 9.1 基于 Trace 的回放

给定 `trace_id`，可还原：

1. 角色当时的状态（从 `character.tick` span 属性）；
2. 检索到的记忆（从 `memory.retrieve` span）；
3. LLM 的完整 Prompt 与输出（从 Langfuse）；
4. Action 执行结果（从 `action.execute` span）；
5. 写入的数据库行（从 `db.tx` span + Loki 日志）；
6. 全部相关日志（Loki 按 `trace_id` 过滤）。

### 9.2 基于快照的世界回放

结合 `world_snapshots` 表与 `action_records`，可重放历史某段时间内小镇的演化过程。详见 [世界引擎设计](world-engine.md#暂停--恢复--回放)。

---

## 十、采样策略

| Span 类型 | 采样率 | 说明 |
|-----------|--------|------|
| 错误 Span | 100% | 所有错误必采 |
| LLM 调用 | 100% | 通过 Langfuse 全量记录 |
| World Tick | 10% | 高频，采样足够 |
| Character Tick | 50% | 兼顾性能与可观测 |
| MCP 工具调用 | 100% | 关键路径 |
| DB 事务 | 10% | 高频，按需采样 |
| 日志（Loki） | 100% | 全量采集，按需查询 |

OTel Collector 配置 tail-based sampling，错误与慢请求优先保留。

---

## 十一、相关文档

| 主题 | 文档 |
|------|------|
| 世界引擎埋点 | [world-engine.md](world-engine.md) |
| Action 系统埋点 | [action-system.md](action-system.md) |
| 部署可观测组件 | [deployment.md](deployment.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
