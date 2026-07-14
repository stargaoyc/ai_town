# MCP 方案改进分析

> 本文档对 aitown 当前 MCP（Model Context Protocol）工具调用方案进行优缺点分析，对比 yuiju 的工具调用方式，提出标准化改进建议，并给出工具的 MCP/内联归属判断与迁移路径。
>
> - 审查范围：`packages/mcp-servers/`（6 个 Server）、`packages/backend/src/mcp/`（客户端）、`packages/backend/src/api/mcp.py`（管理 API）、`packages/backend/src/core/character_tick.py`（集成点）
> - 参考项目：yuiju 的 `packages/utils/src/llm/tools/` 工具系统
> - 撰写日期：2026-07-14

---

## 一、当前 MCP 方案概览

### 1.1 架构拓扑

```text
Character Tick 引擎 ──(phase 3.5 use_tool)──> MCPClient ──HTTP/SSE──> MCP Server Cluster
                                                  │
                                                  ├── code-executor   (8001, 自研, subprocess 沙箱)
                                                  ├── web-search      (8002, 社区, Tavily API)
                                                  ├── weather         (8003, 社区, OpenWeatherMap)
                                                  ├── shop-simulator  (8004, 自研, 商店经济)
                                                  ├── knowledge-base  (8005, 自研, 设定库)
                                                  └── character-social(8006, 自研, 社交系统)
```

### 1.2 关键集成点

| 位置 | 文件 | 行为 |
|------|------|------|
| 决策注入 | `core/character_tick.py::_decide` | 把 `format_tools_for_prompt()` 追加到 Prompt，让 LLM 选 `action="use_tool"` |
| 工具执行 | `core/character_tick.py::_execute_mcp_tool` | 调 `MCPClient.call_tool_by_full_name`，结果写入 MemoryEpisode（截断 500 字符），随后强制 `decision.action = "wait"` |
| 启用控制 | `src/mcp/client.py::get_enabled_servers` | 从 Redis hash `mcp:enabled` 读取开关，未配置时默认全开 |
| 管理 API | `src/api/mcp.py` | 列表 / 详情 / 健康检查 / 启停 / 测试调用 |

### 1.3 设计约定

- **工具不直接写状态**：shop-simulator / character-social 返回 `money_delta`、`inventory_delta`、`relation_strength_delta`，由 caller（Action executor）应用到 Redis/PG
- **进程隔离**：每个 Server 是独立 FastMCP 2.0+ 进程，通过 SSE `/sse` + JSON-RPC `/messages/` 通信
- **降级安全**：Server 离线时返回错误字典，不中断 Tick

---

## 二、优点与缺点

### 2.1 优点

| # | 优点 | 说明 |
|---|------|------|
| 1 | 进程隔离 | code-executor 用 subprocess 沙箱执行用户代码，主进程不被污染；其他 Server 故障不影响后端 |
| 2 | 独立部署 | 各 Server 可独立扩缩容、独立发版，符合 monorepo 边界 |
| 3 | 标准协议 | 采用 MCP 协议，未来可被社区 Server 替换（web-search、weather 已是社区骨架） |
| 4 | 启停可控 | Redis hash 控制启用粒度，前端可视化开关 |
| 5 | 边界正确 | 工具返回 deltas 而非直接写状态，与 AGENTS.md「Action executor 不直接写状态」「LLM 不直接修改状态」一致 |
| 6 | 失败不阻塞 | MCPClient 所有调用 try/except 兜底，离线时返回错误，不中断 Tick |

### 2.2 缺点

#### 缺点 1：配置真相源分裂（P0）

工具清单在 **三处重复定义且不一致**：

| 位置 | Server 数 | code-executor | 工具参数描述 |
|------|-----------|---------------|--------------|
| `src/api/mcp.py::_MCP_SERVERS_CONFIG` | 6 | ✅ 含 | 完整 |
| `src/mcp/client.py::MCP_SERVERS` | 5 | ❌ 缺 | 参数描述与上不一致（如 `query_kb` 一边是 `question`、一边是 `query`） |
| 各 Server `server.py` | — | — | 真正实现，但工具 schema 未被客户端读取 |

后果：LLM Prompt 看到的工具列表与实际可调用的工具对不齐，code-executor 在客户端根本不可见。

#### 缺点 2：工具 Schema 未自动发现（P0）

`MCPClient.list_tools()` 返回的是 `client.py` 内硬编码的 `{desc, params}` 字典，**没有调用 MCP 协议的 `tools/list` 方法**动态发现。新增工具必须改两处 Python 代码，违反单一真相源。

#### 缺点 3：MCP 与 Action 边界混乱（P0）

`character-social` 与 `shop-simulator` 的工具签名是 `(character_id, current_state, ...) -> deltas`，**这正是 Action executor 的模式**。把它们放在外部进程带来：

- 额外 IPC 序列化开销（每次调用要打包 character 状态、库存、关系）
- 与 ActionRegistry 候选过滤脱节，LLM 选 `use_tool` 时绕过了 precondition
- 工具结果还要再走一次 Action executor 才能落库，链路冗长

#### 缺点 4：缺少熔断/重试/预算（P1）

- MCPClient 直接 `httpx.post`，**没有 CircuitBreaker**（与 LLM 客户端的 `circuit_breaker` 不一致）
- Server 短暂抖动会导致每个 Tick 都尝试连接失败，浪费决策时间
- 无单角色工具调用预算，LLM 可在多个 Tick 中连续 spam 工具

#### 缺点 5：工具结果未回流决策（P1）

`_execute_mcp_tool` 把工具结果写入 MemoryEpisode 后强制 `decision.action = "wait"`，工具返回的信息要等到**下一个 Tick** 才能通过记忆检索影响决策。对「查天气 → 决定是否带伞」这类即时依赖，延迟一 Tick（10 分钟虚拟时间）体验割裂。

#### 缺点 6：缺少可观测性（P1）

- 无 OTel span 包裹 MCP 调用
- 无 Prometheus 指标（调用次数、延迟、失败率）
- 日志只记录 `result_preview[:200]`，排查困难

#### 缺点 7：参数无 Pydantic 校验（P1）

LLM 返回的 `tool_args` 是 `dict[str, Any]`，直接透传给 Server。错误类型（如 `quantity="abc"`）只能由 Server 端发现，浪费一次 IPC 往返。

#### 缺点 8：知识库硬编码（P2）

`knowledge-base/server.py` 把 16 条 KBEntry 写死在模块常量里，与世界设定（`configs/world-map.yaml`、`configs/characters/*.yaml`）脱节，无法热更新。

---

## 三、对比 yuiju 工具调用方式

### 3.1 yuiju 工具系统概览

| 维度 | yuiju 做法 |
|------|-----------|
| 工具位置 | `packages/utils/src/llm/tools/` 内联 TypeScript 模块 |
| Schema 定义 | 独立 `schema/` 目录，每个工具有 JSON Schema |
| 调用方式 | 进程内函数调用，无 IPC |
| 工具清单 | query-state / memory-search / person-memory / propose-plan-changes / review-plan-changes / query-available-inventory-items / query-static-guide |
| 状态修改权 | LLM 仅能「提案」`proposePlanChanges`，需 `reviewPlanChanges` 审查后才生效 |
| 调用约束 | Prompt 显式声明「`proposePlanChanges` 只能调用一次」「工具返回为客观事实」 |
| 静态知识 | `queryStaticGuide` 工具查询 worldMap/shopProducts/dinerMenu，避免 Prompt 膨胀 |

### 3.2 关键差异矩阵

| 维度 | aitown 现状 | yuiju 现状 | 评价 |
|------|-------------|------------|------|
| 工具部署 | 外部进程（MCP） | 内联模块 | aitown 隔离更强，但 IPC 开销大 |
| Schema 来源 | 客户端硬编码 | 服务端定义 + 客户端引用 | yuiju 单一真相源 |
| 工具发现 | 静态配置 | 静态导出 | 都未利用 MCP `tools/list` |
| 状态修改 | 工具返回 deltas，caller 应用 | LLM 提案 → 审查 → 应用 | yuiju 多一层审查 |
| 调用约束 | Prompt 未声明 | Prompt 显式声明次数与语义 | yuiju 更严谨 |
| 静态知识 | 直接注入 Prompt | 工具按需查询 | yuiju 避免 Prompt 膨胀 |
| 业务工具 | shop/social 放 MCP | 库存查询为工具，买卖为 Action | yuiju 边界更清晰 |

### 3.3 yuiju 可借鉴点

1. **Schema-first**：每个工具先定义 JSON Schema，再写实现，客户端从 Schema 自动生成 Prompt 描述
2. **提案-审查分离**：状态变更类工具走 `propose → review → apply` 三段式，避免 LLM 滥用
3. **Prompt 内声明约束**：明确告知 LLM「工具返回为客观事实」「某工具只能调用 N 次」
4. **静态知识工具化**：用 `queryStaticGuide` 替代 Prompt 注入，按需查询

---

## 四、标准化工具调用改进建议

### 4.1 统一工具注册中心

新增 `src/mcp/registry.py`，作为唯一真相源：

```python
# 替代 _MCP_SERVERS_CONFIG 与 MCP_SERVERS 两处硬编码
class ToolRegistry:
    async def discover_tools(self) -> list[ToolSpec]:
        """启动时通过 MCP tools/list 发现各 Server 的工具，缓存到 Redis"""
    async def get_tool(self, full_name: str) -> ToolSpec | None
    async def list_for_prompt(self, character_id: UUID) -> list[ToolSpec]
```

`ToolSpec` 用 Pydantic 定义，含 `name / description / params_schema / server / enabled`。

### 4.2 引入 Pydantic 参数校验

每个工具的 `params_schema` 是一个 Pydantic 模型，`MCPClient.call_tool` 在发送前用 `Model.model_validate(args)` 校验，类型错误立即返回，不浪费 IPC。

### 4.3 增加 CircuitBreaker + 重试

复用 `src/llm/circuit_breaker.py` 模式，为每个 Server 维护独立的熔断状态：

- 连续 5 次失败 → 熔断 60 秒
- 熔断期间直接返回 fallback，不发请求
- 成功后重置计数

### 4.4 单角色工具调用预算

在 Redis `char:{id}:state` 增加 `tool_calls_today` 字段，每 Tick 决策时检查：

- 单角色每日最多 N 次工具调用（默认 10）
- 单 Tick 最多 1 次工具调用（已是现状，需显式约束到 Prompt）

### 4.5 工具结果即时回流

改造 `_execute_mcp_tool`：工具成功后**重新触发一次决策**（带工具结果作为新增上下文），而非强制 `wait`。代价是多一次 LLM 调用，但避免了「查天气 → 等 10 分钟 → 才决定带伞」的割裂。

### 4.6 Prompt 内声明工具使用规则

在 `configs/prompts/decision.yaml` 增加 `[工具使用规则]` 段：

- 工具返回为客观事实，不得否认
- `query_world_guide` 类工具单次决策只能调用一次
- 工具调用结果不影响本次 Action 选择时，不应调用

### 4.7 可观测性补齐

- OTel span：`MCPClient.call_tool` 包裹 `tracer.start_as_current_span("mcp.tool.call")`
- Prometheus 指标：`mcp_tool_calls_total{server, tool, status}`、`mcp_tool_latency_seconds`
- 结构化日志：完整 args + result（debug 级别），不截断

---

## 五、工具归属：MCP vs 内联

### 5.1 判断标准

| 标准 | 倾向 MCP | 倾向内联 |
|------|----------|----------|
| 是否需要进程隔离（安全） | ✅ | — |
| 是否依赖外部 API | ✅ | — |
| 是否返回状态 deltas | — | ✅（应为 Action executor） |
| 是否需要 precondition 过滤 | — | ✅（应为 Action） |
| 是否纯查询、无副作用 | — | ✅（可为 local 工具） |
| 是否需要独立扩缩容 | ✅ | — |

### 5.2 归属建议

| 当前工具 | 现位置 | 建议位置 | 理由 |
|----------|--------|----------|------|
| `code-executor.execute_python` | MCP | **保留 MCP** | 需要 subprocess 沙箱隔离，安全边界硬需求 |
| `web-search.search` / `search_news` | MCP | **保留 MCP** | 外部 Tavily API，网络 IO 重，可被社区 Server 替换 |
| `weather.get_current_weather` / `get_forecast` | MCP | **保留 MCP** | 外部 OpenWeatherMap API |
| `shop-simulator.buy_item` / `sell_item` | MCP | **迁移为 Action executor** | 返回 money/inventory deltas，纯业务逻辑，无外部依赖，应受 precondition 约束（如必须在 shop 场景） |
| `shop-simulator.list_items` / `get_item_details` | MCP | **内联为 local 工具** | 纯查询，可由 `TownService` 直接读取 `configs/shop-catalog.yaml` |
| `knowledge-base.query_kb` | MCP | **内联为 local 工具** | 硬编码数据应迁到 PG `world_settings` 表或 YAML，按需查询 |
| `character-social.give_gift` | MCP | **迁移为 Action executor** | 返回 relation/inventory deltas，应受关系等级 precondition 约束 |
| `character-social.invite_date` | MCP | **迁移为 Action executor** | 同上，且需要 target 在同场景的 precondition |
| `character-social.resolve_conflict` | MCP | **迁移为 Action executor** | 同上 |

### 5.3 迁移后拓扑

```text
外部依赖类（保留 MCP）          业务逻辑类（迁移为 Action）        查询类（内联 local）
├── code-executor              ├── buy_item (Action)             ├── list_items (TownService)
├── web-search                 ├── sell_item (Action)            ├── get_item_details (TownService)
└── weather                    ├── give_gift (Action)            └── query_kb (WorldGuideService)
                               ├── invite_date (Action)
                               └── resolve_conflict (Action)
```

---

## 六、迁移路径与优先级

### 6.1 P0（1 周内）：消除真相源分裂

| 任务 | 落点 | 验收 |
|------|------|------|
| 创建 `src/mcp/registry.py` 统一注册中心 | 新增模块 | `_MCP_SERVERS_CONFIG` 与 `MCP_SERVERS` 删除，所有引用走 registry |
| 启动时调用 MCP `tools/list` 自动发现工具 | `registry.discover_tools` | 新增 Server 工具无需改客户端代码 |
| 修复 code-executor 在客户端不可见 | registry | LLM Prompt 中可见 code-executor 工具 |
| 工具参数 Pydantic 校验 | `MCPClient.call_tool` | 错误类型 args 在客户端即被拒绝 |

### 6.2 P1（2-3 周）：业务工具回归 Action

| 任务 | 落点 | 验收 |
|------|------|------|
| `give_gift` / `invite_date` / `resolve_conflict` 迁为 Action | `src/actions/social/` | 候选过滤生效，precondition 约束关系等级与场景 |
| `buy_item` / `sell_item` 迁为 Action | `src/actions/economy/` | 必须在 shop 场景才进候选 |
| `list_items` / `get_item_details` 迁为 TownService 内联 | `src/modules/town/` | 商店目录外置到 `configs/shop-catalog.yaml` |
| `query_kb` 迁为 WorldGuideService 内联 | `src/modules/world/` | 知识数据迁到 PG `world_settings` 表或 YAML |
| 删除 `packages/mcp-servers/shop-simulator/` 与 `character-social/` | 包移除 | docker-compose 同步移除服务 |

### 6.3 P1（2 周）：韧性与可观测性

| 任务 | 落点 | 验收 |
|------|------|------|
| MCPClient 接入 CircuitBreaker | `src/mcp/client.py` | Server 故障时熔断 60s，不每 Tick 重试 |
| 工具调用 OTel span + Prometheus 指标 | `src/observability/` | Grafana 可见 MCP 调用面板 |
| 单角色工具调用预算 | `char:{id}:state` | 超预算时 Prompt 不再列出工具 |
| 工具结果即时回流决策 | `character_tick.py` | 工具成功后带结果重决策，不再强制 wait |

### 6.4 P2（1 个月）：Prompt 规范化与深度集成

| 任务 | 落点 | 验收 |
|------|------|------|
| `decision.yaml` 增加 `[工具使用规则]` 段 | `configs/prompts/` | LLM 不再滥用工具 |
| 工具调用约束（次数/语义）写入 Prompt | 同上 | 与 yuiju 对齐 |
| 探索 MCP `resources` 用于静态知识 | `src/mcp/` | 替代当前 query_kb 的工具模式 |
| MCP Client/Server 鉴权（多租户场景） | `src/mcp/auth.py` | 非 localhost 部署可用 |

---

## 七、风险与注意事项

1. **迁移 shop/social 不可一次完成**：需先在 Action 侧实现新 executor，再切换 Prompt 工具列表，最后下线 MCP Server，分三步走避免 Tick 中断
2. **code-executor 安全升级**：当前 subprocess + 模块白名单**非真沙箱**，生产环境应升级为 Docker/nsjail，这是 MCP 保留它的核心理由
3. **MCP `tools/list` 缓存策略**：启动时发现一次并缓存到 Redis，避免每次决策都发 HTTP；Server 重启后需触发重新发现
4. **不要为统一而统一**：code-executor / web-search / weather 留在 MCP 是正确的（隔离 + 外部依赖），迁移应只针对业务逻辑类
5. **Prompt 膨胀风险**：工具列表 + 使用规则会增加 decision Prompt token，建议工具描述控制在单行 80 字符内，且按场景过滤可见工具

---

## 八、相关文档

| 主题 | 文档 |
|------|------|
| 模块与 MCP 设计 | [module-system.md](module-system.md) |
| Action 系统 | [action-system.md](action-system.md) |
| yuiju 工具系统对比 | [yuiju-comparison.md](yuiju-comparison.md#五llm-工具系统对比) |
| 项目 gap 分析 | [gap-analysis.md](gap-analysis.md) |
| LLM 边界约定 | [AGENTS.md §4.3](../AGENTS.md) |
| Prompt 规范 | [rules/prompt-style.md](rules/prompt-style.md) |
