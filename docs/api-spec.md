# API 设计文档

> 本文档定义 AI Town 后端的 RESTful API、WebSocket/SSE 端点，以及关键请求/响应示例。所有 API 路径以 `/api/v1` 为前缀，返回 JSON。

---

## 一、通用约定

### 1.1 基础信息

| 项       | 值                                      |
| -------- | --------------------------------------- |
| Base URL | `http://localhost:8000/api/v1`          |
| 协议     | HTTP/1.1 (生产建议 HTTP/2 + HTTPS)      |
| 数据格式 | JSON (`Content-Type: application/json`) |
| 字符编码 | UTF-8                                   |
| 时间格式 | ISO 8601 UTC（`2026-07-06T08:00:00Z`）  |
| ID 格式  | UUID v7（时间有序）                     |
| 分页     | `?page=1&page_size=20`，响应含 `total`  |

### 1.2 统一响应格式

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
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

| 范围    | 含义                           |
| ------- | ------------------------------ |
| `0`     | 成功                           |
| `400xx` | 客户端错误（参数/权限/未找到） |
| `500xx` | 服务端错误                     |
| `503xx` | 依赖服务不可用（LLM/DB）       |

### 1.4 鉴权

| 渠道          | 方式                          |
| ------------- | ----------------------------- |
| Web Dashboard | `Authorization: Bearer <jwt>` |
| 第三方集成    | `X-API-Key: <key>`            |

---

## 二、RESTful API

### 2.1 角色管理

| 端点                                  | 方法   | 说明                                 |
| ------------------------------------- | ------ | ------------------------------------ |
| `/api/v1/characters`                  | GET    | 角色列表（分页、过滤）               |
| `/api/v1/characters`                  | POST   | 创建角色                             |
| `/api/v1/characters/{id}`             | GET    | 角色详情                             |
| `/api/v1/characters/{id}`             | PUT    | 更新角色                             |
| `/api/v1/characters/{id}`             | DELETE | 删除角色（软删除）                   |
| `/api/v1/characters/{id}/state`       | GET    | 角色实时状态                         |
| `/api/v1/characters/{id}/memories`    | GET    | 角色记忆列表                         |
| `/api/v1/characters/{id}/actions`     | GET    | 角色行为历史                         |
| `/api/v1/characters/{id}/reflections` | GET    | 角色反思列表                         |
| `/api/v1/characters/{id}/plans`       | GET    | 角色计划列表                         |
| `/api/v1/characters/{id}/relations`   | GET    | 角色关系图谱                         |
| `/api/v1/characters/{id}/nearby`      | GET    | 同场景其他角色（多智能体交互可见性） |

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

#### 查询同场景其他角色

```http
GET /api/v1/characters/{id}/nearby
```

返回与该角色处于同一场景的其他角色，用于前端展示「当前场景中还有谁」并支撑多智能体 `chat_with` 决策。位置优先从 Redis 实时状态读取，缺失时降级到 PG。

```json
{
  "data": [
    {
      "id": "019f4c52-...",
      "name": "绫音",
      "personality": "温柔、内向、喜欢读书",
      "mood": "calm",
      "current_action_name": "读书",
      "relationship_type": "friend",
      "strength": 50,
      "location": "cafe"
    }
  ],
  "total": 1,
  "location": "cafe"
}
```

**说明**：

- 仅返回同 `location` 的其他活跃角色，排除查询角色本人；
- `relationship_type` / `strength` 来自 `RelationGraph`，未建立关系时返回 `stranger` / `0`；
- 角色无位置时返回空列表与 `location: null`。

### 2.2 世界管理

| 端点                      | 方法 | 说明               |
| ------------------------- | ---- | ------------------ |
| `/api/v1/world/state`     | GET  | 世界状态           |
| `/api/v1/world/weather`   | PUT  | 手动设置天气       |
| `/api/v1/world/time`      | PUT  | 手动调整时间       |
| `/api/v1/world/pause`     | POST | 暂停世界推进       |
| `/api/v1/world/resume`    | POST | 恢复世界推进       |
| `/api/v1/world/snapshots` | GET  | 快照列表（回放用） |

#### 设置天气

```http
PUT /api/v1/world/weather
Content-Type: application/json

{ "weather": "rainy", "temperature": 18 }
```

### 2.3 模块管理

| 端点                             | 方法               | 说明                   |
| -------------------------------- | ------------------ | ---------------------- |
| `/api/v1/modules`                | GET / POST         | 模块列表 / 注册        |
| `/api/v1/modules/{name}`         | GET / PUT / DELETE | 模块详情 / 更新 / 卸载 |
| `/api/v1/modules/{name}/enable`  | POST               | 启用模块               |
| `/api/v1/modules/{name}/disable` | POST               | 禁用模块               |
| `/api/v1/modules/{name}/health`  | GET                | 模块健康检查           |

#### 注册模块

```http
POST /api/v1/modules
Content-Type: application/json

{
  "name": "shop",
  "type": "tools",
  "enabled": true,
  "config": {},
  "dependencies": []
}
```

> `type` 可选 `tools`（进程内本地工具，对应 `src/tools/`）/ `local` / `skill`。

#### 启用模块

```http
POST /api/v1/modules/shop/enable
```

```json
{ "code": 0, "data": { "name": "shop", "enabled": true, "health_check_status": "healthy" } }
```

### 2.4 工具管理

> **路径说明**：以下端点路径前缀为 `/api/v1/tools/*`，管理进程内本地工具命名空间（`src/tools/`），不涉及独立 Server 容器。

| 端点                                         | 方法 | 说明                                                          |
| ------------------------------------------- | ---- | ------------------------------------------------------------- |
| `/api/v1/tools/servers`                     | GET  | 工具命名空间列表（含工具清单 + `enabled` 字段）               |
| `/api/v1/tools/servers/health`              | GET  | 健康检查（本地工具为进程内调用，始终返回 `online`）           |
| `/api/v1/tools/servers/{name}`              | GET  | 单个工具命名空间详情                                          |
| `/api/v1/tools/servers/{server_name}/enabled` | PUT  | **动态启用/禁用整个命名空间**（Redis `tools:enabled` 持久化） |
| `/api/v1/tools/tools`                       | GET  | 所有可用工具列表（仅返回已启用命名空间的工具）                |
| `/api/v1/tools/tools/{tool_name}/invoke`    | POST | 测试调用本地工具（管理调试用）                                |

#### 查询所有可用工具

```http
GET /api/v1/tools/tools
```

```json
{
  "code": 0,
  "data": [
    { "name": "shop.list_items", "namespace": "shop", "description": "列出商店商品", "parameters": { ... } },
    { "name": "shop.buy_item", "namespace": "shop", "description": "购买商品", "parameters": { ... } },
    { "name": "world.get_world_info", "namespace": "world", "description": "查询世界状态/天气", "parameters": { ... } }
  ]
}
```

#### 切换工具命名空间启用状态

```http
PUT /api/v1/tools/servers/{server_name}/enabled
Content-Type: application/json

{ "enabled": false }
```

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "server_name": "shop",
    "enabled": false
  }
}
```

**说明**：

- 开关状态持久化到 Redis hash `tools:enabled`（键为工具全名，值为 `"true"` / `"false"`），重启后端自动恢复；
- 禁用后，`format_tools_for_prompt()` 不再返回该命名空间的工具，LLM 决策时不可见；
- 本地工具为进程内 async 函数调用，无网络开销，`/servers/health` 始终返回 `online`；
- 未配置开关时默认全部启用。

详见 [模块与本地工具系统设计 - 工具命名空间单独开关](module-system.md#51-工具命名空间单独开关redis-持久化)。

### 2.5 会话与消息

| 端点                                   | 方法 | 说明                 |
| -------------------------------------- | ---- | -------------------- |
| `/api/v1/conversations`                | GET  | 会话列表             |
| `/api/v1/conversations/{id}`           | GET  | 会话详情             |
| `/api/v1/conversations/{id}/messages`  | GET  | 会话消息历史（分页） |
| `/api/v1/conversations/{id}/messages`  | POST | 发送消息（Web 渠道） |
| `/api/v1/conversations/{id}/intervene` | POST | 人工干预插入消息     |

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

| 端点                              | 方法 | 说明                                          |
| --------------------------------- | ---- | --------------------------------------------- |
| `/api/v1/observability/traces`    | GET  | 链路查询（按 trace_id / character_id / time） |
| `/api/v1/observability/logs`      | GET  | 日志查询                                      |
| `/api/v1/observability/metrics`   | GET  | 指标数据                                      |
| `/api/v1/observability/db/health` | GET  | PG/Redis 健康检查                             |

### 2.7 系统设置

| 端点                           | 方法      | 说明            |
| ------------------------------ | --------- | --------------- |
| `/api/v1/settings/models`      | GET / PUT | 模型配置        |
| `/api/v1/settings/prompts`     | GET / PUT | Prompt 模板管理 |
| `/api/v1/settings/permissions` | GET / PUT | 权限管理        |

### 2.8 运维

| 端点                                 | 方法 | 说明                                                                              |
| ------------------------------------ | ---- | --------------------------------------------------------------------------------- |
| `/api/v1/admin/partitions`           | GET  | 查看分区表状态                                                                    |
| `/api/v1/admin/partitions/precreate` | POST | 手动预创建分区                                                                    |
| `/api/v1/admin/tick`                 | POST | 强制触发某角色 Tick（调试用）                                                     |
| `/api/v1/admin/restore-snapshot`     | POST | 用指定快照重置世界态（调试用）                                                    |
| `/api/v1/admin/logs`                 | GET  | 读取后端日志文件（支持行数与级别过滤）                                            |
| `/api/v1/admin/metrics-detail`       | GET  | 解析 Prometheus 指标为结构化 JSON（World/Character/Action/LLM/Message/HTTP 分类） |

#### 读取后端日志

```http
GET /api/v1/admin/logs?lines=200&level=ERROR
```

```json
{
  "code": 0,
  "data": {
    "file": "data/logs/backend.log",
    "lines": 200,
    "level": "ERROR",
    "content": "[2026-07-12 10:23:45] ERROR src.core.world_engine: tick_failed ..."
  }
}
```

**参数**：

- `lines`（可选，默认 200）：返回最后 N 行日志；
- `level`（可选，默认全部）：过滤日志级别（DEBUG/INFO/WARN/ERROR）。

#### 解析 Prometheus 指标

```http
GET /api/v1/admin/metrics-detail
```

```json
{
  "code": 0,
  "data": {
    "world": { "tick_total": 1234, "tick_duration_p95": 1.2 },
    "character": { "active_count": 8, "tick_duration_p95": 2.1 },
    "action": { "success_rate": 0.98, "total": 5678 },
    "llm": { "call_total": 890, "cost_total_usd": 1.23, "error_rate": 0.01 },
    "message": { "processed_total": 234, "response_time_p95": 3.4 },
    "http": { "request_total": 5678, "error_5xx_rate": 0.001 }
  }
}
```

**说明**：该端点解析 `/metrics` Prometheus 文本格式，转换为结构化 JSON 便于前端监控页面直接消费，无需对接 Grafana。

### 2.9 记忆扩展（日记 + Person Memory）

| 端点                                         | 方法 | 说明                                          | 鉴权 |
| -------------------------------------------- | ---- | --------------------------------------------- | ---- |
| `/api/v1/characters/{id}/diaries`            | GET  | 角色日记列表（支持 `period` 与 `limit` 过滤） | 可选 |
| `/api/v1/characters/{id}/diaries/generate`   | POST | 为角色生成指定周期日记（需 admin/operator）   | 必须 |
| `/api/v1/characters/{id}/person-memory`      | GET  | 角色对某用户的记忆（`user_id` 查询参数）      | 可选 |
| `/api/v1/characters/{id}/person-memory/list` | GET  | 角色对所有用户的记忆列表（按热度倒序）        | 可选 |

#### 生成角色日记

```http
POST /api/v1/characters/{character_id}/diaries/generate?period=day
Authorization: Bearer <jwt>
```

**参数**：

- `period`（默认 `day`）：`day` / `week` / `month` / `year`
- `character_name`（可选）：角色名，未提供时从数据库查询

**流程**：

1. 从 `memory_episodes` 提取指定时间段内的记忆（按真实 UTC 时间过滤）
2. 调用 LLM 生成叙事性日记（第一人称，200-500 字）
3. 保存到 `character_diaries` 表

**响应**：

```json
{
  "data": {
    "character_id": "019f4c52-...",
    "period": "day",
    "diary_date": "2026-07-13T03:44:36.095466+00:00",
    "diary_end_date": null,
    "title": "神社里的漫长一日",
    "content": "今天真是漫长又煎熬的一天...",
    "mood": "疲惫中带着期待"
  }
}
```

**失败响应**（422）：

```json
{
  "detail": "Diary generation failed: insufficient memories or LLM unavailable"
}
```

**说明**：

- 当指定时间段内记忆少于 3 条时返回 422
- `diary_date` 使用真实 UTC 时间戳（非虚拟世界时间）
- `diary_end_date` 仅在 `period != "day"` 时填充
- 日记不替代 `memory_episodes` 真相源，是角色视角的叙事归档

详见 [记忆系统 - 日记服务](memory-system.md)。

#### Person Memory 列表

```http
GET /api/v1/characters/{character_id}/person-memory/list?limit=50
```

```json
{
  "data": [
    {
      "id": "019f...",
      "character_id": "019f...",
      "user_id": "user_123",
      "platform": "web",
      "content": "用户喜欢咖啡拉花",
      "heat": 85,
      "last_interaction_at": "2026-07-12T...",
      "created_at": "2026-07-10T...",
      "updated_at": "2026-07-12T..."
    }
  ],
  "total": 12
}
```

### 2.10 通知

| 端点                              | 方法 | 说明                             |
| --------------------------------- | ---- | -------------------------------- |
| `/api/v1/notifications`           | GET  | 当前用户通知列表（支持 `limit`） |
| `/api/v1/notifications/{id}/read` | PUT  | 标记单条通知为已读               |
| `/api/v1/notifications/read-all`  | PUT  | 标记当前用户所有通知为已读       |

#### 通知列表

```http
GET /api/v1/notifications?limit=20
Authorization: Bearer <jwt>
```

```json
{
  "data": [
    {
      "id": "019f564d-...",
      "type": "share",
      "title": "test notif",
      "content": "hello from test",
      "created_at": "2026-07-12T12:28:48.997559+00:00",
      "read": false
    }
  ],
  "total": 5,
  "unread": 3
}
```

#### 标记已读

```http
PUT /api/v1/notifications/{notif_id}/read
Authorization: Bearer <jwt>
```

```json
{ "success": true, "id": "019f564d-..." }
```

### 2.11 调试与检索

| 端点                                    | 方法 | 说明                                                      |
| --------------------------------------- | ---- | --------------------------------------------------------- |
| `/api/v1/admin/vector-search`           | POST | 向量检索测试（pgvector + HNSW）                           |
| `/api/v1/admin/proactive-shares`        | GET  | 主动分享历史（`extra_data->>'share_type' = 'proactive'`） |
| `/api/v1/admin/world/snapshots`         | GET  | 世界状态快照列表                                          |
| `/api/v1/characters/{id}/state-history` | GET  | 角色状态变更历史                                          |

#### 向量检索

```http
POST /api/v1/admin/vector-search?character_id=...&query=piano&top_k=10
Authorization: Bearer <jwt>
```

```json
{
  "query": "piano",
  "character_id": "019f4c52-...",
  "data": [
    {
      "id": "019f57f6-...",
      "content": "奏在shrine执行了move...",
      "importance": 5,
      "timestamp": "2026-07-12T20:13:40.932110+00:00",
      "similarity": 0.2692674080479498,
      "is_reflected": true,
      "source_type": "action"
    }
  ]
}
```

**说明**：仅检索 `materialized=true` 的记忆（embedding 已生成）。

---

## 三、WebSocket / SSE

### 3.1 WebSocket 端点

| 端点                     | 说明             | 推送内容                       |
| ------------------------ | ---------------- | ------------------------------ |
| `/ws/dashboard`          | 仪表盘实时数据   | 全局角色状态、世界状态、事件流 |
| `/ws/characters/{id}`    | 特定角色状态推送 | 该角色的状态变更、行为事件     |
| `/ws/modules`            | 模块状态变更推送 | 模块启用/禁用/健康状态         |
| `/ws/conversations/{id}` | 会话实时消息     | 新消息推送                     |

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
const ws = new WebSocket("ws://localhost:8000/ws/dashboard");
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "character.state_update") {
    store.updateCharacter(msg.data);
  }
};
```

### 3.2 SSE 端点

| 端点          | 说明                           |
| ------------- | ------------------------------ |
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

| 参数        | 默认 | 说明                 |
| ----------- | ---- | -------------------- |
| `page`      | 1    | 页码（从 1 开始）    |
| `page_size` | 20   | 每页数量（最大 100） |

### 4.2 过滤参数

| 参数                      | 适用                    | 示例                           |
| ------------------------- | ----------------------- | ------------------------------ |
| `q`                       | 角色名/消息内容模糊搜索 | `?q=小明`                      |
| `status`                  | 角色/模块/计划          | `?status=active`               |
| `character_id`            | 行为/记忆/消息          | `?character_id=...`            |
| `start_time` / `end_time` | 时间范围                | `?start_time=...&end_time=...` |
| `platform`                | 消息                    | `?platform=qq`                 |

### 4.3 排序

`?sort=-created_at`（前缀 `-` 表示降序）。

---

## 五、限流

| 端点分类                           | 限流             |
| ---------------------------------- | ---------------- |
| 公开查询                           | 60 req/min/IP    |
| 鉴权用户                           | 300 req/min/user |
| LLM 触发端点（发送消息、强制决策） | 20 req/min/user  |
| 管理端点（启用/禁用模块）          | 10 req/min/user  |

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

| 主题            | 文档                                       |
| --------------- | ------------------------------------------ |
| 前端 API 客户端 | [frontend-design.md](frontend-design.md)   |
| 模块系统        | [module-system.md](module-system.md)       |
| 数据模型        | [data-model.md](data-model.md)             |
| 配置参考        | [config-reference.md](config-reference.md) |
| 可观测性端点    | [observability.md](observability.md)       |
