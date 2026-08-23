# 模块与本地工具系统设计

> 模块管理器是系统的可插拔能力中枢，负责所有功能模块的注册、开关控制和生命周期管理。本地工具调用层（`src/tools/`）以进程内 async 函数形式提供工具能力，替代原 MCP Server 架构，消除 HTTP/SSE 网络开销。
>
> **2026-07-15 更新**：原 MCP Server 架构已转换为本地工具。`packages/mcp-servers/` 下 4 个 Server（shop-simulator / knowledge-base / character-social / weather）已删除，`src/mcp/` 客户端模块已删除，`fastmcp` 依赖已移除。原工具能力收编为 `src/tools/` 下的进程内 async 函数，并新增 `world` / `self_info` 两类只读查询工具。API 路径前缀为 `/api/v1/tools/*`（原 `/api/v1/mcp/*` 已重命名）。

---

## 一、模块管理器

### 1.1 设计目标

| 目标     | 说明                                   |
| -------- | -------------------------------------- |
| 可插拔   | 模块可动态启用/禁用/卸载，无需重启     |
| 依赖治理 | 模块间依赖关系明确，禁用前检查反向依赖 |
| 健康检查 | 模块状态可观测，异常自动降级           |
| 三态开关 | 配置文件、运行时 API、Agent 自适应     |

### 1.2 模块类型

| 模块类型 | 说明                          | 示例                               |
| -------- | ----------------------------- | ---------------------------------- |
| `tools`  | 进程内 async 函数，无网络开销 | 商店、知识库、社交、世界查询、自省 |
| `local`  | 内联函数，毫秒级响应          | 情感分析、记忆检索                 |
| `skill`  | 多步骤复杂工作流              | 数据分析报告生成、多轮调研         |

### 1.3 模块生命周期

```text
REGISTERED(已注册) → ENABLED(已启用) → 运行中
       ↓                  ↓
    DISABLED(已禁用)    ERROR(错误)
```

| 阶段 | 动作                                      |
| ---- | ----------------------------------------- |
| 注册 | 系统启动时扫描并注册所有可用模块          |
| 启用 | 检查依赖 → 健康检查 → 加载配置 → 标记可用 |
| 调用 | Agent 决策后通过统一接口调用              |
| 禁用 | 检查反向依赖 → 清理资源 → 标记不可用      |
| 卸载 | 完全移除模块（热插拔）                    |

### 1.4 开关控制方式

| 方式         | 说明                                                  | 适用场景           |
| ------------ | ----------------------------------------------------- | ------------------ |
| 运行时 API   | `PUT /api/v1/tools/servers/{name}/enabled`（Redis 持久化） | 灰度测试、紧急禁用 |
| Agent 自适应 | LLM 根据上下文决策是否使用                            | 智能降级、自主选择 |

### 1.5 模块与 Action 联动

```text
模块启用 → 模块管理器注册工具 → ActionRegistry 注册对应 TOOL Action
        ↓                          ↓
    模块禁用 → 模块管理器注销工具 → ActionRegistry 注销对应 Action
```

模块禁用时，依赖该模块的 Action 自动从候选列表移除，LLM 决策不再可见。

### 1.6 模块配置（PG `module_configs` 表）

| 字段                  | 说明                                |
| --------------------- | ----------------------------------- |
| `name`                | 模块唯一名                          |
| `type`                | `tools` / `local` / `skill`         |
| `enabled`             | 是否启用                            |
| `config`              | JSONB，模块特定配置                 |
| `dependencies`        | 依赖的模块名列表                    |
| `health_check_status` | `healthy` / `unhealthy` / `unknown` |
| `last_check_at`       | 最近健康检查时间                    |

> 本地工具模块（`type=tools`）的开关状态实际持久化在 Redis hash `tools:enabled`（按工具全名存储，详见 §二），`module_configs` 表中的 `enabled` 字段仅作为模块元数据镜像。

详细 DDL 见 [数据模型设计](data-model.md#module_configs)。

---

## 二、本地工具调用层（ToolRegistry）

### 2.1 架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                   世界引擎 (Character Tick Engine)                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  ToolRegistry（src/tools/registry.py）                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │   │
│  │  │ format_tools │  │ call_tool_   │  │ _apply_tool_   │ │   │
│  │  │ _for_prompt  │  │ with_context │  │ deltas（写回  │ │   │
│  │  │ （注入 LLM） │  │ （注入状态） │  │ Redis/PG）    │ │   │
│  │  └──────────────┘  └──────────────┘  └────────────────┘ │   │
│  └──────────────────────────┬───────────────────────────────┘   │
└─────────────────────────────┬───────────────────────────────────┘
                              │ 进程内 async 函数调用（无网络）
┌─────────────────────────────▼───────────────────────────────────┐
│              src/tools/ 工具命名空间（5 个）                      │
│  ┌────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌──────────┐    │
│  │ shop   │ │knowledge │ │ social │ │ world  │ │ self_info│    │
│  │ 5 工具 │ │ 2 工具   │ │ 3 工具 │ │ 4 工具 │ │ 2 工具   │    │
│  └────────┘ └──────────┘ └────────┘ └────────┘ └──────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │ 读取启用状态
┌─────────────────────────────▼───────────────────────────────────┐
│              Redis hash `tools:enabled`                          │
│   { "shop.buy_item": "true", "social.give_gift": "false", ... }  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 工具命名空间与清单

共 16 个工具，按 5 个命名空间组织，工具全名格式 `namespace.tool`：

| 命名空间    | 类型           | 工具                                                                             | 说明                                           |
| ----------- | -------------- | -------------------------------------------------------------------------------- | ---------------------------------------------- |
| `shop`      | self-developed | `list_items`、`get_item_details`、`buy_item`、`sell_item`、`get_shop_categories` | 商店模拟（24 件商品 + 购买/出售事务）          |
| `knowledge` | self-developed | `query_kb`、`list_categories`                                                    | 小镇设定库（世界规则/角色/场景/行动/记忆系统） |
| `social`    | self-developed | `give_gift`、`invite_date`、`resolve_conflict`                                   | 角色社交（送礼/约会/冲突解决）                 |
| `world`     | read-only      | `get_world_info`、`find_character_by_name`、`get_scene_info`、`list_scenes`      | 只读：世界状态/场景/角色查找                   |
| `self_info` | read-only      | `get_relationships`、`search_memories`                                           | 只读：自身关系/记忆搜索                        |

### 2.3 工具定义结构

每个工具在 `TOOL_REGISTRY` 中以字典条目注册：

```python
# src/tools/registry.py
TOOL_REGISTRY["shop.buy_item"] = {
    "func": shop.buy_item,                       # async 函数引用
    "description": "购买商品（扣金钱、加库存）",   # LLM Prompt 中展示
    "llm_params": {"item_id": "商品 ID", "quantity": "购买数量（默认 1）"},
    "injected_params": {                         # 需从角色状态自动注入
        "current_money": "money",                # 普通字段：从 state 取值
        "current_inventory": "inventory",
    },
    "state_mutating": True,                      # 是否会产生状态 deltas
}
```

### 2.4 状态参数自动注入

状态变更类工具（`buy_item` / `sell_item` / `give_gift` / `invite_date` / `resolve_conflict`）需要角色当前状态参数（`current_money` / `current_inventory` / `current_relation_strength` 等），这些参数不暴露给 LLM，由 `ToolRegistry.call_tool_with_context()` 从调用方传入的 `context` 自动注入：

```python
# src/core/character/tick.py
context = {
    "character_id": character_id,
    "state": state,                             # Redis 角色状态（含 money/inventory/mood）
    "relations": relations_map,                 # {target_id: relation_strength}
}
result = await registry.call_tool_with_context(
    "shop.buy_item",
    {"item_id": "coffee", "quantity": 1},
    context,
)
```

`injected_params` 的特殊键：

| source 值                        | 含义                                                 |
| -------------------------------- | ---------------------------------------------------- |
| 普通字段名（如 `money`、`mood`） | 从 `context["state"]` 取值                           |
| `_character_id`                  | 从 `context["character_id"]` 取值                    |
| `_relation_strength_with_target` | 按 `args.target_id` 在 `context["relations"]` 中查找 |

### 2.5 工具状态 deltas 应用

工具返回结果中的 `money_delta` / `inventory_delta` / `relation_strength_delta` / `mood_delta` 由 `CharacterTickEngine._apply_tool_deltas()` 写回：

| delta 字段                | 写入目标                                |
| ------------------------- | --------------------------------------- |
| `money_delta`             | Redis `char:{cid}:state.money`          |
| `inventory_delta`         | Redis `char:{cid}:state.inventory`      |
| `mood_delta`              | Redis `char:{cid}:state.mood`           |
| `relation_strength_delta` | PG `relations` 表（需配合 `target_id`） |

> 此前工具调用只保存到记忆，不修改角色状态，属于已知缺陷。2026-07-15 转换为本地工具时已修复。

### 2.6 容错策略

| 策略     | 说明                                                               |
| -------- | ------------------------------------------------------------------ |
| 启用过滤 | 调用前检查 `tools:enabled`，禁用工具返回 `Tool is disabled`        |
| 异常捕获 | 工具异常捕获后返回 `{"success": false, "error": ...}`，不中断 Tick |
| 降级     | 工具失败时 LLM 决策可见"工具暂不可用"，引导选择其他 Action         |
| 健康检查 | 本地工具为进程内调用，`/servers/health` 始终返回 `online`          |

---

## 三、统一工具调用接口

### 3.1 ToolRegistry 接口

工具不再使用抽象基类，而是以字典条目形式注册在 `TOOL_REGISTRY` 中（详见 §2.3）。`ToolRegistry` 类提供与原 `MCPClient` 兼容的接口：

```python
# src/tools/registry.py
class ToolRegistry:
    async def list_tools(self) -> list[dict]:
        """列出已启用工具的元数据（过滤禁用工具）"""

    async def format_tools_for_prompt(self) -> str:
        """格式化工具列表供 LLM Prompt 使用（仅含已启用工具）"""

    async def call_tool_by_full_name(
        self, full_name: str, args: dict | None, context: dict | None
    ) -> dict:
        """通过全名调用工具（不处理 _relation_strength_with_target 特殊注入）"""

    async def call_tool_with_context(
        self, full_name: str, args: dict | None, context: dict
    ) -> dict:
        """带上下文调用工具（处理关系强度等特殊注入，Character Tick 使用）"""
```

### 3.2 模块级辅助函数

| 函数                           | 说明                                                |
| ------------------------------ | --------------------------------------------------- |
| `get_enabled_tools()`          | 从 Redis 读取已启用工具全名集合（未配置时返回全部） |
| `is_tool_enabled(full_name)`   | 检查单个工具是否启用                                |
| `list_all_tool_names()`        | 返回所有注册工具全名（不受启用状态过滤）            |
| `get_tool_metadata(full_name)` | 获取单个工具的元数据                                |

### 3.3 与 Character Tick 集成

```python
# src/core/character/tick.py
from src.tools import ToolRegistry

# 1. 渲染决策 Prompt 时注入工具描述
registry = ToolRegistry()
tools_text = await registry.format_tools_for_prompt()

# 2. LLM 决策返回 tool_call 后，带上下文调用
result = await registry.call_tool_with_context(
    tool_name, tool_args, context
)

# 3. 应用工具返回的状态 deltas 到 Redis / PG
await self._apply_tool_deltas(character_id, result, context)
```

---

## 四、内置工具清单与开发分工

### 4.1 工具归属判断

| 命名空间    | 来源           | 理由                                                    |
| ----------- | -------------- | ------------------------------------------------------- |
| `shop`      | **自研**       | 小镇专属业务逻辑（商品/库存/价格/角色消费），无现成方案 |
| `knowledge` | **自研**       | 小镇设定库与角色记忆检索，需深度定制                    |
| `social`    | **自研**       | 小镇社交系统专属（送礼/约会/冲突），强业务绑定          |
| `world`     | **自研（新）** | 2026-07-15 新增：世界状态/场景/角色查找，只读           |
| `self_info` | **自研（新）** | 2026-07-15 新增：角色自省（关系/记忆搜索），只读        |

### 4.2 历史迁移说明

| 原 MCP Server       | 当前归属                        | 状态               |
| ------------------- | ------------------------------- | ------------------ |
| `shop-simulator`    | `src/tools/shop.py`             | ✅ 已迁移          |
| `knowledge-base`    | `src/tools/knowledge.py`        | ✅ 已迁移          |
| `character-social`  | `src/tools/social.py`           | ✅ 已迁移          |
| `weather`           | 已合并到 `world.get_world_info` | ✅ 已合并          |
| ~~`code-executor`~~ | ~~已移除~~                      | ❌ 2026-07-14 移除 |
| ~~`web-search`~~    | ~~已移除~~                      | ❌ 2026-07-14 移除 |

### 4.3 新增工具流程

详见 [开发指南 - 新增本地工具](development-guide.md#62-新增本地工具)。

---

## 五、管理 API

已实现的管理 API 端点（路径前缀 `/api/v1/tools/*`，管理本地工具命名空间）：

| 端点                                          | 方法 | 说明                                                      | 状态 |
| --------------------------------------------- | ---- | --------------------------------------------------------- | ---- |
| `/api/v1/modules`                             | GET  | 模块列表（含运行状态，工具命名空间以 `tools.*` 形式展示） | ✅   |
| `/api/v1/tools/servers`                       | GET  | 工具命名空间列表（含工具清单 + `enabled` 字段）           | ✅   |
| `/api/v1/tools/servers/health`                | GET  | 健康检查（本地工具始终返回 `online`）                     | ✅   |
| `/api/v1/tools/servers/{name}`                | GET  | 单个工具命名空间详情                                      | ✅   |
| `/api/v1/tools/servers/{server_name}/enabled` | PUT  | **动态启用/禁用整个命名空间**（Redis 持久化）             | ✅   |
| `/api/v1/tools/tools`                         | GET  | 所有可用工具列表（仅返回已启用命名空间的工具）            | ✅   |
| `/api/v1/tools/tools/{tool_name}/invoke`      | POST | 测试调用本地工具（管理调试用）                            | ✅   |

详细请求/响应见 [API设计文档](api-spec.md)。

### 5.1 工具命名空间单独开关（Redis 持久化）

#### 设计目标

每个工具命名空间（shop / knowledge / social / world / self_info）可独立启用/禁用，无需重启后端。开关状态持久化到 Redis hash `tools:enabled`（替代原 `mcp:enabled`），重启后自动恢复。前端 Dashboard 提供可视化 toggle 控件。

#### 实现架构

```text
┌──────────────────────────────────────────────────────────┐
│  前端 Dashboard (/settings)                              │
│  ┌──────────────────────────────────────────────────┐    │
│  │  工具命名空间卡片                                  │    │
│  │  ┌────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐  │    │
│  │  │ shop   │ │knowledge │ │ social │ │ world    │  │    │
│  │  │[ON/OFF]│ │ [ON/OFF] │ │[ON/OFF]│ │ [ON/OFF] │  │    │
│  │  └────────┘ └──────────┘ └────────┘ └──────────┘  │    │
│  └──────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────┘
                         │ PUT /api/v1/tools/servers/{namespace}/enabled
                         ▼
┌──────────────────────────────────────────────────────────┐
│  后端 (src/api/tools.py)                                 │
│  ┌──────────────────────────────────────────────────┐    │
│  │  PUT /api/v1/tools/servers/{namespace}/enabled   │    │
│  │  → redis.hset("tools:enabled", mapping={         │    │
│  │       tool_full_name: "true"|"false" for t in ns │    │
│  │    })                                            │    │
│  └──────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────┘
                         │ Redis hash: tools:enabled
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Redis                                                   │
│  ┌──────────────────────────────────────────────────┐    │
│  │  HGETALL tools:enabled                            │    │
│  │  ┌──────────────────────┬─────────┐               │    │
│  │  │ Field                │ Value   │               │    │
│  │  ├──────────────────────┼─────────┤               │    │
│  │  │ shop.buy_item        │ true    │               │    │
│  │  │ shop.sell_item        │ true    │               │    │
│  │  │ social.give_gift     │ false   │  ← 已禁用     │    │
│  │  │ social.invite_date   │ false   │  ← 已禁用     │    │
│  │  │ knowledge.query_kb   │ true    │               │    │
│  │  └──────────────────────┴─────────┘               │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
                         │ 读取开关状态
                         ▼
┌──────────────────────────────────────────────────────────┐
│  ToolRegistry (src/tools/registry.py)                    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  async list_tools()                              │    │
│  │    → get_enabled_tools() 过滤禁用工具             │    │
│  │  async format_tools_for_prompt()                 │    │
│  │    → 仅注入已启用工具到 LLM Prompt                │    │
│  │  async call_tool_with_context(name, args, ctx)   │    │
│  │    → is_tool_enabled() 检查，禁用则返回 error     │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

#### 关键代码位置

| 文件                                        | 函数/方法/常量                                  | 说明                                                            |
| ------------------------------------------- | ----------------------------------------------- | --------------------------------------------------------------- |
| `src/tools/registry.py`                     | `TOOLS_ENABLED_KEY = "tools:enabled"`           | Redis hash key 常量（替代原 `mcp:enabled`）                     |
| `src/tools/registry.py`                     | `get_enabled_tools()`                           | 读取 Redis hash，返回启用的工具全名集合（未配置时默认全部启用） |
| `src/tools/registry.py`                     | `is_tool_enabled(full_name)`                    | 检查单个工具是否启用                                            |
| `src/tools/registry.py`                     | `ToolRegistry.format_tools_for_prompt()`        | 仅注入已启用工具到 LLM Prompt                                   |
| `src/tools/registry.py`                     | `ToolRegistry.call_tool_with_context()`         | 调用前检查启用状态，禁用则返回 `Tool is disabled`               |
| `src/api/tools.py`                          | `_NAMESPACES`                                   | 工具命名空间元数据（5 个）                                      |
| `src/api/tools.py`                          | `PUT /api/v1/tools/servers/{namespace}/enabled` | 切换开关的 API 端点                                             |
| `src/api/tools.py`                          | `list_tool_servers()`                           | 响应中包含 `enabled` 字段                                       |
| `src/api/system.py`                         | `_TOOL_NAMESPACES` 导入                         | 系统模块列表中以 `tools.{namespace}` 形式展示                   |
| `packages/frontend/src/routes/settings.tsx` | 工具命名空间卡片 toggle                         | 前端 toggle 控件（sakura 色主题）                               |
| `packages/frontend/src/lib/api.ts`          | `toggleMcpServer(name, enabled)`                | 前端 API 调用方法（函数名保留以兼容）                           |
| `packages/frontend/src/lib/queries.ts`      | `useToggleMcpServer`                            | TanStack Query mutation hook                                    |

#### 默认行为

- **未配置开关时**：`get_enabled_tools()` 返回所有已注册工具，即默认全部启用；
- **配置后**：仅 Redis hash 中值为 `true` 的工具被启用；
- **重启后端**：Redis 中开关状态自动恢复，无需重新配置。

#### 前端 UI 设计

- 每个工具命名空间卡片右上角显示 toggle 开关按钮；
- 启用状态：sakura 色主题（樱花粉），显示"已启用"标签；
- 禁用状态：灰色 + `opacity-70` + "已禁用"标签；
- 点击 toggle 立即调用 API，无需额外确认；
- 切换成功后自动刷新命名空间列表（TanStack Query 缓存失效）。

---

## 六、可观测埋点

| Span                  | 关键属性                                               |
| --------------------- | ------------------------------------------------------ |
| `tool.call`           | `tool_full_name`, `namespace`, `latency_ms`, `success` |
| `module.enable`       | `module_name`, `dependencies_checked`                  |
| `module.disable`      | `module_name`, `reverse_deps_checked`                  |
| `module.health_check` | `module_name`, `status`                                |

---

## 七、相关文档

| 主题                         | 文档                                         |
| ---------------------------- | -------------------------------------------- |
| Action 系统与 TOOL 类 Action | [action-system.md](action-system.md)         |
| 数据模型（module_configs）   | [data-model.md](data-model.md)               |
| API 设计                     | [api-spec.md](api-spec.md)                   |
| 配置参考                     | [config-reference.md](config-reference.md)   |
| 前端设计（工具 toggle UI）   | [frontend-design.md](frontend-design.md)     |
| Docker 部署                  | [docker-deployment.md](docker-deployment.md) |
