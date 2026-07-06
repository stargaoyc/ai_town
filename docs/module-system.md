# 模块与 MCP 系统设计

> 模块管理器是系统的可插拔能力中枢，负责所有功能模块的注册、开关控制和生命周期管理。MCP 工具调用层提供标准化的外部工具接口。

---

## 一、模块管理器

### 1.1 设计目标

| 目标 | 说明 |
|------|------|
| 可插拔 | 模块可动态启用/禁用/卸载，无需重启 |
| 依赖治理 | 模块间依赖关系明确，禁用前检查反向依赖 |
| 健康检查 | 模块状态可观测，异常自动降级 |
| 三态开关 | 配置文件、运行时 API、Agent 自适应 |

### 1.2 模块类型

| 模块类型 | 说明 | 示例 |
|----------|------|------|
| `MCP` | 通过 MCP 协议调用的外部服务 | 代码执行、网页搜索、天气查询 |
| `local` | 内联函数，毫秒级响应 | 情感分析、记忆检索、本地工具 |
| `skill` | 多步骤复杂工作流 | 数据分析报告生成、多轮调研 |

### 1.3 模块生命周期

```text
REGISTERED(已注册) → ENABLED(已启用) → 运行中
       ↓                  ↓
    DISABLED(已禁用)    ERROR(错误)
```

| 阶段 | 动作 |
|------|------|
| 注册 | 系统启动时扫描并注册所有可用模块 |
| 启用 | 检查依赖 → 健康检查 → 加载配置 → 标记可用 |
| 调用 | Agent 决策后通过统一接口调用 |
| 禁用 | 检查反向依赖 → 清理资源 → 标记不可用 |
| 卸载 | 完全移除模块（热插拔） |

### 1.4 开关控制方式

| 方式 | 说明 | 适用场景 |
|------|------|----------|
| 配置文件 | `config.yaml` 中 `enabled: true/false` | 运维管理，重启生效 |
| 运行时 API | `POST /api/v1/modules/{name}/enable` | 灰度测试、紧急禁用 |
| Agent 自适应 | LLM 根据上下文决策是否使用 | 智能降级、自主选择 |

### 1.5 模块与 Action 联动

```text
模块启用 → 模块管理器注册工具 → ActionRegistry 注册对应 TOOL Action
        ↓                          ↓
    模块禁用 → 模块管理器注销工具 → ActionRegistry 注销对应 Action
```

模块禁用时，依赖该模块的 Action 自动从候选列表移除，LLM 决策不再可见。

### 1.6 模块配置（PG `module_configs` 表）

| 字段 | 说明 |
|------|------|
| `name` | 模块唯一名 |
| `type` | `mcp` / `local` / `skill` |
| `enabled` | 是否启用 |
| `config` | JSONB，模块特定配置 |
| `dependencies` | 依赖的模块名列表 |
| `mcp_server_url` | MCP 类型模块的 Server 地址 |
| `health_check_status` | `healthy` / `unhealthy` / `unknown` |
| `last_check_at` | 最近健康检查时间 |

详细 DDL 见 [数据模型设计](data-model.md#module_configs)。

---

## 二、MCP 工具调用层

### 2.1 MCP 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                   世界引擎 (MCP Client)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ 工具发现     │  │ 工具调用    │  │ 结果处理与重试          │ │
│  │ (ListTools) │  │ (CallTool) │  │ (超时/降级/熔断)        │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└─────────────────────────────┬───────────────────────────────────┘
                              │ JSON-RPC 2.0 / SSE
┌─────────────────────────────▼───────────────────────────────────┐
│                    MCP Server Cluster                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│  │ 代码执行     │ │ 网页搜索     │ │ 天气查询     │           │
│  │ Server       │ │ Server       │ │ Server       │           │
│  └──────────────┘ └──────────────┘ └──────────────┘           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│  │ 商店模拟     │ │ 知识库查询   │ │ 第三方API    │           │
│  │ Server       │ │ Server       │ │ Server       │           │
│  └──────────────┘ └──────────────┘ └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 通信协议

| 项 | 选型 |
|----|------|
| 协议 | JSON-RPC 2.0 |
| 传输 | SSE（Server-Sent Events）/ stdio / WebSocket |
| SDK | 官方 `mcp` Python SDK |

### 2.3 MCP Server 注册工具示例

```python
# mcp-servers/code-executor/server.py
from mcp.server import Server, tool

server = Server("code-executor")

@server.tool()
async def run_python(code: str) -> dict:
    """在沙箱中执行 Python 代码"""
    # 实际执行逻辑（容器化隔离）
    result = await sandbox.run(code)
    return {"output": result.stdout, "error": result.stderr}

@server.tool()
async def list_files(path: str) -> dict:
    """列出指定路径下的文件"""
    ...
```

### 2.4 MCP Client 调用示例

```python
# tools/mcp_client.py
from mcp import ClientSession

async def call_mcp_tool(server_url: str, tool_name: str, params: dict):
    async with mcp.ClientSession(server_url) as session:
        # 动态发现工具
        tools = await session.list_tools()
        # 调用工具
        result = await session.call_tool(tool_name, params)
        return result
```

### 2.5 容错策略

| 策略 | 说明 |
|------|------|
| 超时 | 单次调用默认 30s，可按工具配置 |
| 重试 | 可重试错误重试 2 次，指数退避 |
| 熔断 | 5 分钟内错误率 > 30% 触发熔断，10 分钟后探活 |
| 降级 | 工具不可用时，LLM 决策可见"工具暂不可用"，引导选择其他 Action |
| 健康检查 | 模块管理器每 60s 调用 `health` 端点，更新 `health_check_status` |

---

## 三、统一工具调用接口

### 3.1 工具抽象

```python
# tools/base.py
from abc import ABC, abstractmethod

class Tool(ABC):
    name: str
    description: str
    parameters: dict          # JSON Schema

    @abstractmethod
    async def call(self, **params) -> dict: ...


class LocalTool(Tool):
    """内联函数工具"""
    pass


class MCPTool(Tool):
    """MCP 远程工具"""
    server_url: str

    async def call(self, **params) -> dict:
        return await call_mcp_tool(self.server_url, self.name, params)


class SkillTool(Tool):
    """多步骤工作流工具"""
    steps: list[callable]

    async def call(self, **params) -> dict:
        result = params
        for step in self.steps:
            result = await step(result)
        return result
```

### 3.2 工具注册表

```python
# tools/registry.py
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> Tool: ...
    def list_all(self) -> list[Tool]: ...
    def to_openai_functions(self) -> list[dict]:
        """转换为 OpenAI function-calling 格式"""
        ...
```

### 3.3 与 LangGraph 集成

```python
# agents/character_agent.py
from langgraph.prebuilt import ToolNode

# 将所有已启用工具绑定到 LLM
tools = tool_registry.list_all()
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)
```

---

## 四、内置 MCP Server 清单

| Server | 端口 | 工具 | 说明 |
|--------|------|------|------|
| `code-executor` | 8001 | `run_python`, `run_javascript` | 沙箱代码执行 |
| `web-search` | 8002 | `search_web`, `fetch_page` | 网页搜索与抓取 |
| `weather` | 8003 | `get_weather` | 天气查询 |
| `shop-simulator` | 8004 | `buy_item`, `list_items` | 商店模拟 |
| `knowledge-base` | 8005 | `query_kb` | 知识库查询 |

各 Server 独立部署（多进程/Docker），通过 `MCP_*_SERVER` 环境变量配置地址。

---

## 五、管理 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/modules` | GET / POST | 模块列表 / 注册 |
| `/api/v1/modules/{name}` | GET / PUT / DELETE | 模块详情 / 更新 / 卸载 |
| `/api/v1/modules/{name}/enable` | POST | 启用模块 |
| `/api/v1/modules/{name}/disable` | POST | 禁用模块 |
| `/api/v1/modules/{name}/health` | GET | 模块健康检查 |
| `/api/v1/mcp/servers` | GET / POST | MCP Server 管理 |
| `/api/v1/mcp/tools` | GET | 所有可用工具列表 |

详细请求/响应见 [API设计文档](api-spec.md)。

---

## 六、可观测埋点

| Span | 关键属性 |
|------|----------|
| `mcp.tool.call` | `tool_name`, `server_url`, `latency_ms`, `success` |
| `module.enable` | `module_name`, `dependencies_checked` |
| `module.disable` | `module_name`, `reverse_deps_checked` |
| `module.health_check` | `module_name`, `status` |

---

## 七、相关文档

| 主题 | 文档 |
|------|------|
| Action 系统与 TOOL 类 Action | [action-system.md](action-system.md) |
| 数据模型（module_configs） | [data-model.md](data-model.md) |
| API 设计 | [api-spec.md](api-spec.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
