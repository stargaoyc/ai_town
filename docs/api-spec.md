# API 设计文档

> 本文档定义 AI Town 后端的 RESTful API、WebSocket/SSE 端点，以及关键请求/响应示例。所有 API 路径以 `/api/v1` 为前缀，返回 JSON。

---

## 一、通用约定

### 1.1 基础信息

| 项 | 值 |
|----|----|
| Base URL | `http://localhost:8000/api/v1` |
| 协议 | HTTP/1.1 (生产建议 HTTP/2 + HTTPS) |
| 数据格式 | JSON (`Content-Type: application/json`) |
| 字符编码 | UTF-8 |
| 时间格式 | ISO 8601 UTC（`2026-07-06T08:00:00Z`） |
| ID 格式 | UUID v4 |
| 分页 | `?page=1&page_size=20`，响应含 `total` |

### 1.2 统一响应格式

```json
{
  "code": 0,
  "message": "ok",
  "data": { },
  "trace_id": "abc123"
}
```

错误响应：

```json
{
  "code": 40001,
  "message": "Character not found",
  "data": null,
  "trace_id": "abc123"
}
```

### 1.3 错误码

| 范围 | 含义 |
|------|------|
| `0` | 成功 |
| `400xx` | 客户端错误（参数/权限/未找到） |
| `500xx` | 服务端错误 |
| `503xx` | 依赖服务不可用（LLM/MCP/DB） |

### 1.4 鉴权

| 渠道 | 方式 |
|------|------|
| Web Dashboard | `Authorization: Bearer <jwt>` |
| 第三方集成 | `X-API-Key: <key>` |

---

## 二、RESTful API

### 2.1 角色管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/characters` | GET | 角色列表（分页、过滤） |
| `/api/v1/characters` | POST | 创建角色 |
| `/api/v1/characters/{id}` | GET | 角色详情 |
| `/api/v1/characters/{id}` | PUT | 更新角色 |
| `/api/v1/characters/{id}` | DELETE | 删除角色（软删除） |
| `/api/v1/characters/{id}/state` | GET | 角色实时状态 |
| `/api/v1/characters/{id}/memories` | GET | 角色记忆列表 |
| `/api/v1/characters/{id}/actions` | GET | 角色行为历史 |
| `/api/v1/characters/{id}/reflections` | GET | 角色反思列表 |
| `/api/v1/characters/{id}/plans` | GET | 角色计划列表 |
| `/api/v1/characters/{id}/relations` | GET | 角色关系图谱 |

#### 创建角色

```http
POST /api/v1/characters
Content-Type: application/json

{
  "name": "小明",
  "age": 17,
  "occupation": "高中生",
  "personality": ["开朗", "细心", "有点社恐"],
  "traits": { "hobby": "咖啡拉花", "favorite_color": "blue" },
  "backstory": "从小在小镇长大...",
  "avatar_url": "https://cdn.example.com/avatar/xm.png"
}
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "id": "7f9c...e3",
    "name": "小明",
    "age": 17,
    "occupation": "高中生",
    "personality": ["开朗", "细心", "有点社恐"],
    "traits": { "hobby": "咖啡拉花", "favorite_color": "blue" },
    "backstory": "从小在小镇长大...",
    "avatar_url": "https://cdn.example.com/avatar/xm.png",
    "status": "active",
    "created_at": "2026-07-06T08:00:00Z",
    "updated_at": "2026-07-06T08:00:00Z"
  }
}
```

#### 查询角色实时状态

```http
GET /api/v1/characters/{id}/state
```

```json
{
  "code": 0,
  "data": {
    "character_id": "7f9c...e3",
    "location": "cafe",
    "current_action": "work_parttime",
    "action_started_at": 1783345456000,
    "energy": 65,
    "hunger": 40,
    "mood": "happy",
    "last_updated": 1783345480000
  }
}
```

### 2.2 世界管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/world/state` | GET | 世界状态 |
| `/api/v1/world/weather` | PUT | 手动设置天气 |
| `/api/v1/world/time` | PUT | 手动调整时间 |
| `/api/v1/world/pause` | POST | 暂停世界推进 |
| `/api/v1/world/resume` | POST | 恢复世界推进 |
| `/api/v1/world/snapshots` | GET | 快照列表（回放用） |

#### 设置天气

```http
PUT /api/v1/world/weather
Content-Type: application/json

{ "weather": "rainy", "temperature": 18 }
```

### 2.3 模块管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/modules` | GET / POST | 模块列表 / 注册 |
| `/api/v1/modules/{name}` | GET / PUT / DELETE | 模块详情 / 更新 / 卸载 |
| `/api/v1/modules/{name}/enable` | POST | 启用模块 |
| `/api/v1/modules/{name}/disable` | POST | 禁用模块 |
| `/api/v1/modules/{name}/health` | GET | 模块健康检查 |

#### 注册 MCP 模块

```http
POST /api/v1/modules
Content-Type: application/json

{
  "name": "code-executor",
  "type": "mcp",
  "enabled": true,
  "config": { "timeout": 30 },
  "dependencies": [],
  "mcp_server_url": "http://localhost:8001"
}
```

#### 启用模块

```http
POST /api/v1/modules/code-executor/enable
```

```json
{ "code": 0, "data": { "name": "code-executor", "enabled": true, "health_check_status": "healthy" } }
```

### 2.4 MCP Server 管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/mcp/servers` | GET / POST | MCP Server 列表 / 注册 |
| `/api/v1/mcp/servers/{id}` | DELETE | 注销 MCP Server |
| `/api/v1/mcp/tools` | GET | 所有可用工具列表（聚合所有 Server） |

#### 查询所有可用工具

```http
GET /api/v1/mcp/tools
```

```json
{
  "code": 0,
  "data": [
    { "name": "run_python", "server": "code-executor", "description": "在沙箱中执行Python代码", "parameters": { ... } },
    { "name": "search_web", "server": "web-search", "description": "网页搜索", "parameters": { ... } }
  ]
}
```

### 2.5 会话与消息

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/conversations` | GET | 会话列表 |
| `/api/v1/conversations/{id}` | GET | 会话详情 |
| `/api/v1/conversations/{id}/messages` | GET | 会话消息历史（分页） |
| `/api/v1/conversations/{id}/messages` | POST | 发送消息（Web 渠道） |
| `/api/v1/conversations/{id}/intervene` | POST | 人工干预插入消息 |

#### 发送消息

```http
POST /api/v1/conversations/{id}/messages
Content-Type: application/json

{ "role": "user", "content": "你今天过得怎么样？" }
```

#### 消息历史（分页）

```http
GET /api/v1/conversations/{id}/messages?page=1&page_size=20
```

```json
{
  "code": 0,
  "data": {
    "items": [
      { "id": "...", "role": "user", "content": "...", "created_at": "..." },
      { "id": "...", "role": "assistant", "content": "...", "created_at": "..." }
    ],
    "total": 142,
    "page": 1,
    "page_size": 20
  }
}
```

### 2.6 可观测性

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/observability/traces` | GET | 链路查询（按 trace_id / character_id / time） |
| `/api/v1/observability/logs` | GET | 日志查询 |
| `/api/v1/observability/metrics` | GET | 指标数据 |
| `/api/v1/observability/db/health` | GET | PG/Redis 健康检查 |

### 2.7 系统设置

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/settings/models` | GET / PUT | 模型配置 |
| `/api/v1/settings/prompts` | GET / PUT | Prompt 模板管理 |
| `/api/v1/settings/permissions` | GET / PUT | 权限管理 |

### 2.8 运维

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/admin/migrate` | POST | 一次性从旧 Mongo 导入历史数据 |
| `/api/v1/admin/partitions` | GET | 查看分区表状态 |
| `/api/v1/admin/partitions/precreate` | POST | 手动预创建分区 |

---

## 三、WebSocket / SSE

### 3.1 WebSocket 端点

| 端点 | 说明 | 推送内容 |
|------|------|----------|
| `/ws/dashboard` | 仪表盘实时数据 | 全局角色状态、世界状态、事件流 |
| `/ws/characters/{id}` | 特定角色状态推送 | 该角色的状态变更、行为事件 |
| `/ws/modules` | 模块状态变更推送 | 模块启用/禁用/健康状态 |
| `/ws/conversations/{id}` | 会话实时消息 | 新消息推送 |

#### WebSocket 消息格式

```json
{
  "type": "character.state_update",
  "data": {
    "character_id": "7f9c...e3",
    "location": "cafe",
    "current_action": "work_parttime",
    "energy": 65,
    "mood": "happy"
  },
  "timestamp": 1783345480000
}
```

#### 客户端订阅示例

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/dashboard');
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === 'character.state_update') {
    store.updateCharacter(msg.data);
  }
};
```

### 3.2 SSE 端点

| 端点 | 说明 |
|------|------|
| `/sse/traces` | 链路追踪实时流（用于调试面板） |

```http
GET /sse/traces
Accept: text/event-stream

event: trace
data: {"trace_id":"abc","span":"character.tick","character_id":"...","duration_ms":1200}

event: trace
data: {"trace_id":"def","span":"llm.generate","model":"gpt-4o","tokens":450}
```

---

## 四、分页与过滤约定

### 4.1 分页参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `page` | 1 | 页码（从 1 开始） |
| `page_size` | 20 | 每页数量（最大 100） |

### 4.2 过滤参数

| 参数 | 适用 | 示例 |
|------|------|------|
| `q` | 角色名/消息内容模糊搜索 | `?q=小明` |
| `status` | 角色/模块/计划 | `?status=active` |
| `character_id` | 行为/记忆/消息 | `?character_id=...` |
| `start_time` / `end_time` | 时间范围 | `?start_time=...&end_time=...` |
| `platform` | 消息 | `?platform=qq` |

### 4.3 排序

`?sort=-created_at`（前缀 `-` 表示降序）。

---

## 五、限流

| 端点分类 | 限流 |
|----------|------|
| 公开查询 | 60 req/min/IP |
| 鉴权用户 | 300 req/min/user |
| LLM 触发端点（发送消息、强制决策） | 20 req/min/user |
| 管理端点（启用/禁用模块） | 10 req/min/user |

超限返回 `429 Too Many Requests`，响应头 `X-RateLimit-Reset`。

---

## 六、OpenAPI 规范

完整 OpenAPI 3.1 规范由 FastAPI 自动生成，访问：

- `GET /openapi.json` — JSON 规范
- `GET /docs` — Swagger UI
- `GET /redoc` — ReDoc 文档

前端客户端由 `openapi-typescript` 从 `/openapi.json` 生成 TypeScript 类型，详见 [前端设计](frontend-design.md)。

---

## 七、相关文档

| 主题 | 文档 |
|------|------|
| 前端 API 客户端 | [frontend-design.md](frontend-design.md) |
| 模块系统 | [module-system.md](module-system.md) |
| 数据模型 | [data-model.md](data-model.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
