# 可观测性设计

> 本文档定义 AI Town 的可观测性体系：埋点覆盖矩阵、链路追踪、指标、日志、告警。核心理念：**埋点即契约，所有关键路径必须有 Trace 覆盖**。

---

## 一、设计目标

| 目标 | 说明 |
|------|------|
| 全链路追踪 | 每个 Tick / Action / LLM 调用 / MCP 调用都有 Trace |
| LLM 专用追踪 | Token / Cost / Prompt / Completion 可审计 |
| 指标告警 | 关键指标超阈值自动告警 |
| 调试友好 | 可基于 trace_id 回放角色决策全过程 |

---

## 二、埋点覆盖矩阵

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

## 三、可观测性架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                      应用 (Python/LangGraph)                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐│
│  │  OTel SDK   │  │  Langfuse   │  │  Python Logging         ││
│  │  (自动埋点)  │  │  SDK (LLM)  │  │  (结构化日志)           ││
│  └─────────────┘  └─────────────┘  └─────────────────────────┘│
└─────────────────────────────┬───────────────────────────────────┘
                              │ OTLP / HTTP
┌─────────────────────────────▼───────────────────────────────────┐
│                        收集器层                                   │
│              OpenTelemetry Collector (OTel Col)                  │
│         接收 → 处理 → 导出 (批处理/采样/过滤)                    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼───────┐    ┌───────▼───────┐    ┌───────▼───────┐
│   Traces      │    │   Metrics     │    │    Logs       │
│   Jaeger      │    │  Prometheus   │    │   Loki        │
│   / Langfuse  │    │  + Grafana   │    │   (可选)      │
└───────────────┘    └───────────────┘    └───────────────┘
```

### 各组件职责

| 组件 | 职责 |
|------|------|
| OTel SDK | 应用层自动/手动埋点，生成 Span |
| Langfuse SDK | LLM 专用追踪（Prompt/Completion/Token/Cost） |
| Python Logging | 结构化 JSON 日志（含 trace_id） |
| OTel Collector | 接收 OTLP，批处理/采样/过滤，导出到后端 |
| Jaeger | 分布式链路追踪存储与查询 |
| Langfuse | LLM 调用观测（与 Jaeger 互补） |
| Prometheus | 指标采集与存储 |
| Grafana | 指标可视化与告警 |
| Loki | 日志聚合（可选） |

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

所有日志强制带 `trace_id` 与 `span_id`，便于从 Trace 跳转到日志：

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

## 五、关键指标

### 5.1 指标清单

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

### 5.2 自定义业务指标

| 指标 | 说明 |
|------|------|
| `character_energy_avg` | 角色平均精力（健康度参考） |
| `action_category_distribution` | Action 分类分布（生活/工作/社交占比） |
| `relation_strength_avg` | 平均关系强度 |
| `memory_reflection_rate` | 已反思记忆占比 |

---

## 六、Grafana 面板

### 6.1 预置面板

| 面板 | 内容 |
|------|------|
| Overview | 活跃角色数、Tick QPS、LLM 调用 QPS、错误率 |
| LLM | Token 用量、成本、模型分布、延迟分布 |
| Character Tick | Tick 耗时分布、决策模型分布、Action 分类分布 |
| Memory | 检索延迟、记忆总量、反思触发率 |
| MCP | 工具调用 QPS、错误率、延迟、各 Server 健康 |
| DB | 事务耗时、连接池、慢查询、分区表大小 |
| Message | 消息量、回复延迟、推送量、平台分布 |

### 6.2 告警通道

| 通道 | 适用 |
|------|------|
| 飞书机器人 | 默认告警通道 |
| 邮件 | 严重告警 |
| PagerDuty | 生产事故升级 |

---

## 七、Langfuse LLM 追踪

### 7.1 追踪内容

| 字段 | 说明 |
|------|------|
| `name` | 调用场景（character.decide / message.reply） |
| `model` | 模型名 |
| `prompt` | 完整 Prompt（含记忆、状态） |
| `completion` | LLM 输出 |
| `tokens` | input / output tokens |
| `cost` | 调用成本 |
| `metadata` | character_id / trace_id / session_id |

### 7.2 集成方式

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

## 八、日志规范

### 8.1 结构化 JSON 日志

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

### 8.2 日志级别

| 级别 | 适用 |
|------|------|
| `DEBUG` | 详细调试信息（默认不输出） |
| `INFO` | 正常流程关键节点 |
| `WARN` | 可恢复异常（重试、降级） |
| `ERROR` | 错误（Action 失败、模块异常） |
| `CRITICAL` | 系统级故障（DB 不可用） |

### 8.3 日志查询

- Loki + Grafana LogQL 查询；
- 按 `trace_id` 过滤可还原一次决策的全部日志。

---

## 九、调试回放

### 9.1 基于 Trace 的回放

给定 `trace_id`，可还原：

1. 角色当时的状态（从 `character.tick` span 属性）；
2. 检索到的记忆（从 `memory.retrieve` span）；
3. LLM 的完整 Prompt 与输出（从 Langfuse）；
4. Action 执行结果（从 `action.execute` span）；
5. 写入的数据库行（从 `db.tx` span + 日志）。

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

OTel Collector 配置 tail-based sampling，错误与慢请求优先保留。

---

## 十一、相关文档

| 主题 | 文档 |
|------|------|
| 世界引擎埋点 | [world-engine.md](world-engine.md) |
| Action 系统埋点 | [action-system.md](action-system.md) |
| 部署可观测组件 | [deployment.md](deployment.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
