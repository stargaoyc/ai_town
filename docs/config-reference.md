# 配置参考

> 本文档列出 AI Town 的全部配置项：环境变量、`config.yaml`、模块配置、角色卡与 Prompt 配置。

---

## 一、环境变量

### 1.1 数据库

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DATABASE_URL` | 是 | — | PG 连接串，`postgresql+asyncpg://user:pass@host:5432/db` |
| `DB_POOL_SIZE` | 否 | 20 | 连接池大小 |
| `DB_MAX_OVERFLOW` | 否 | 10 | 连接池溢出上限 |
| `DB_ECHO` | 否 | false | 是否打印 SQL（调试用） |
| `EMBEDDING_DIM` | 否 | 1536 | 向量维度（需与表 DDL 一致） |
| `EMBEDDING_MODEL` | 否 | text-embedding-3-small | embedding 模型 |

### 1.2 Redis

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `REDIS_URL` | 是 | — | `redis://host:6379/0` |
| `REDIS_PASSWORD` | 否 | — | Redis 密码 |

### 1.3 LLM 配置

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `OPENAI_API_KEY` | 是 | — | OpenAI API Key |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com/v1` | API 基址（兼容代理） |
| `MODEL_CHAT` | 否 | gpt-4o-mini | 日常对话模型 |
| `MODEL_STRONG` | 否 | gpt-4o | 复杂决策模型 |
| `MODEL_FLASH` | 否 | gpt-3.5-turbo | 快速响应模型 |
| `MODEL_EMBEDDING` | 否 | text-embedding-3-small | embedding 模型 |
| `LLM_TIMEOUT` | 否 | 30 | LLM 调用超时（秒） |
| `LLM_MAX_RETRIES` | 否 | 2 | 最大重试次数 |
| `LLM_DAILY_BUDGET_USD` | 否 | 10.0 | 每日 LLM 成本预算上限（超预算自动降级到 flash 模型） |

### 1.4 记忆系统

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `MEMORY_LLM_SCORING_ENABLED` | 否 | `false` | 是否启用 LLM 记忆重要程度评分。启用后，每条事件由 LLM 按 1-10 分评分（基于情感强度、关系影响、稀缺性、后续影响）；禁用时使用默认固定分值 5 |

> **说明**：LLM 评分会额外消耗 Token，建议在低角色数（<10）或调试期启用。生产环境 50 角色时建议保持 `false` 以控制成本。

### 1.5 MCP Servers

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `MCP_CODE_SERVER` | 否 | — | 代码执行 Server URL |
| `MCP_SEARCH_SERVER` | 否 | — | 网页搜索 Server URL |
| `MCP_WEATHER_SERVER` | 否 | — | 天气查询 Server URL |
| `MCP_SHOP_SERVER` | 否 | — | 商店模拟 Server URL |
| `MCP_KB_SERVER` | 否 | — | 知识库 Server URL |
| `MCP_TOOL_TIMEOUT` | 否 | 30 | 工具调用超时（秒） |

> **MCP 插件开关**：每个 MCP Server 的启用/禁用状态存储在 Redis hash `mcp:enabled` 中，通过前端 Dashboard 或 `PUT /api/v1/mcp/servers/{name}/enabled` API 控制。未配置时默认全部启用。详见 [模块与 MCP 系统设计](module-system.md#51-mcp-插件单独开关redis-持久化)。

### 1.6 可观测性

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 否 | — | OTel Collector 地址 |
| `OTEL_SERVICE_NAME` | 否 | ai-town-backend | 服务名 |
| `OTEL_TRACES_SAMPLER_RATE` | 否 | 0.5 | Trace 采样率 |
| `LANGFUSE_PUBLIC_KEY` | 否 | — | Langfuse 公钥 |
| `LANGFUSE_SECRET_KEY` | 否 | — | Langfuse 密钥 |
| `LANGFUSE_HOST` | 否 | — | Langfuse 地址 |
| `LOKI_URL` | 否 | http://loki:3100 | Loki 推送地址（Alloy 用） |
| `LOG_LEVEL` | 否 | info | 日志级别（debug/info/warn/error） |
| `LOG_FORMAT` | 否 | json | 日志格式（json/text） |

---

### 1.7 消息平台

#### OneBot（QQ 接入）

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `ONEBOT_DEFAULT_CHARACTER_ID` | 否 | — | 默认对话角色 UUID（私聊和未映射的群聊使用） |
| `ONEBOT_SELF_ID` | 否 | — | 机器人自身 QQ 号（用于群聊 @ 检测，也可从事件 self_id 读取） |
| `ONEBOT_GROUP_AT_ONLY` | 否 | `false` | 群聊回复模式：`false`=智能回复（读取所有消息决策）、`true`=仅@回复 |
| `ONEBOT_GROUP_CHARACTER_MAP` | 否 | `{}` | 群-角色映射 JSON，如 `{"123456": "uuid-..."}`，未配置的群使用默认角色 |

配置示例：

```bash
# .env
ONEBOT_DEFAULT_CHARACTER_ID=01923456-7890-7abc-def0-123456789abc
ONEBOT_SELF_ID=123456789
ONEBOT_GROUP_AT_ONLY=false
ONEBOT_GROUP_CHARACTER_MAP={"群号A":"角色UUID-A","群号B":"角色UUID-B"}
```

群聊智能回复决策流程：
1. 被 @ 机器人 → 直接回复（移除 @ 前缀）
2. `ONEBOT_GROUP_AT_ONLY=true` 且未 @ → 跳过
3. `ONEBOT_GROUP_AT_ONLY=false`（默认）→ 三层智能决策：
   - 关键词命中（消息含角色名）→ 直接回复
   - 启发式规则（疑问句 40% / 情绪 20% 概率）→ 概率回复
   - LLM 相关性判断 → 受 40% 概率上限约束

#### 其他平台

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `LARK_APP_ID` | 否 | — | 飞书应用 ID |
| `LARK_APP_SECRET` | 否 | — | 飞书应用密钥 |
| `WEB_WS_PATH` | 否 | /ws | Web WebSocket 路径 |

### 1.8 鉴权

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `JWT_SECRET` | 是 | — | JWT 签名密钥 |
| `JWT_ALGORITHM` | 否 | HS256 | JWT 算法 |
| `JWT_EXPIRE_HOURS` | 否 | 24 | JWT 过期时间 |
| `API_KEY` | 否 | — | 第三方集成 API Key |

### 1.9 世界引擎

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `WORLD_TICK_SECONDS` | 否 | 30 | World Tick 真实间隔 |
| `WORLD_TICK_MINUTES` | 否 | 10 | 每个 Tick 推进的虚拟分钟 |
| `WORLD_WEATHER_INTERVAL` | 否 | 60 | 每 N Tick 更新天气 |
| `WORLD_SNAPSHOT_INTERVAL` | 否 | 120 | 每 N Tick 持久化快照 |
| `CHARACTER_TICK_SECONDS` | 否 | 30 | Character Tick 真实间隔 |
| `CHARACTER_MAX_CONCURRENT` | 否 | 10 | 并发角色 Tick 上限 |
| `CHARACTER_LOCK_TTL_SECONDS` | 否 | 30 | 角色锁 TTL |

---

## 二、config.yaml

```yaml
# config.yaml — 主配置文件

app:
  name: ai-town
  env: development                # development | staging | production
  log_level: info                 # debug | info | warn | error

database:
  url: ${DATABASE_URL}
  pool_size: 20
  max_overflow: 10
  echo: false

redis:
  url: ${REDIS_URL}

llm:
  api_key: ${OPENAI_API_KEY}
  base_url: ${OPENAI_BASE_URL}
  models:
    chat: gpt-4o-mini
    strong: gpt-4o
    flash: gpt-3.5-turbo
    embedding: text-embedding-3-small
  timeout: 30
  max_retries: 2

world:
  tick_seconds: 30
  tick_minutes: 10
  weather_interval: 60
  snapshot_interval: 120

character:
  tick_seconds: 30
  max_concurrent: 10
  lock_ttl_seconds: 30
  memory_top_k: 10
  reflection_threshold: 20       # 每 N 条未反思记忆触发反思

mcp:
  tool_timeout: 30
  servers:
    code-executor: ${MCP_CODE_SERVER}
    web-search: ${MCP_SEARCH_SERVER}
    weather: ${MCP_WEATHER_SERVER}
    shop: ${MCP_SHOP_SERVER}
    knowledge-base: ${MCP_KB_SERVER}

messaging:
  qq:
    ws_url: ${ONE_BOT_WS_URL}
  lark:
    app_id: ${LARK_APP_ID}
    app_secret: ${LARK_APP_SECRET}
  web:
    ws_path: /ws

observability:
  otel:
    endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}
    service_name: ai-town-backend
    traces_sampler_rate: 0.5
  langfuse:
    host: ${LANGFUSE_HOST}
    public_key: ${LANGFUSE_PUBLIC_KEY}
    secret_key: ${LANGFUSE_SECRET_KEY}
  loki:
    enabled: true
    url: http://loki:3100        # Alloy 推送目标
  logging:
    level: info                   # debug | info | warn | error
    format: json                  # structlog 输出格式

auth:
  jwt_secret: ${JWT_SECRET}
  jwt_algorithm: HS256
  jwt_expire_hours: 24

modules:
  # 见第三节：模块配置
  enabled:
    - code-executor
    - web-search
    - weather
```

---

## 三、模块配置

模块配置既可通过 `config.yaml` 静态声明，也可通过 PG `module_configs` 表动态管理。

### 3.1 静态声明（config.yaml）

```yaml
modules:
  enabled:
    - code-executor
    - web-search
  registered:
    - name: code-executor
      type: mcp
      mcp_server_url: ${MCP_CODE_SERVER}
      config:
        timeout: 30
      dependencies: []
    - name: emotion-analyzer
      type: local
      config:
        model: local-emotion-v1
```

### 3.2 动态管理（PG `module_configs` 表）

```sql
INSERT INTO module_configs (name, type, enabled, config, dependencies, mcp_server_url)
VALUES (
  'code-executor',
  'mcp',
  true,
  '{"timeout": 30}'::jsonb,
  '{}',
  'http://localhost:8001'
);
```

### 3.3 运行时开关

```bash
# 启用
curl -X POST http://localhost:8000/api/v1/modules/code-executor/enable

# 禁用
curl -X POST http://localhost:8000/api/v1/modules/code-executor/disable
```

详见 [模块与MCP系统设计](module-system.md)。

---

## 四、角色卡配置

角色卡定义角色的基础档案，支持从 YAML 文件批量导入：

```yaml
# configs/characters/xiaoming.yaml
name: 小明
age: 17
occupation: 高中生
personality:
  - 开朗
  - 细心
  - 有点社恐
traits:
  hobby: 咖啡拉花
  favorite_color: blue
  schedule: early_bird
backstory: |
  从小在小镇长大，父母经营一家咖啡店。
  性格开朗但对陌生人有社恐，喜欢画画和咖啡。
avatar_url: https://cdn.example.com/avatar/xm.png
```

通过 CLI 导入：

```bash
python -m cli.import_character configs/characters/xiaoming.yaml
```

---

## 五、Prompt 配置

Prompt 模板存储在 PG `settings` 表（或文件系统），支持变量插值与版本管理。

### 5.1 决策 Prompt 模板

```yaml
# configs/prompts/decision.yaml
name: character.decision
version: 1
template: |
  [角色档案]
  姓名: {name}
  性格: {personality}
  背景: {backstory}

  [当前状态]
  位置: {location}
  精力: {energy}/100
  饥饿: {hunger}/100
  情绪: {mood}

  [世界状态]
  时间: {world_time}
  天气: {weather}
  场景: {scenes}

  [相关记忆]
  {memories}

  [当前计划]
  {plans}

  [候选 Action]
  {candidates}

  [输出格式]
  请输出 JSON:
  { "action": "<action_id>", "reason": "<理由>", "params": {...}, "duration": <分钟> }
```

### 5.2 反思 Prompt 模板

```yaml
# configs/prompts/reflection.yaml
name: character.reflection
version: 1
template: |
  [角色]
  姓名: {name}
  性格: {personality}

  [近期记忆]
  {memories}

  [任务]
  请基于以上记忆，归纳出 3 条关于该角色的高层认知。
  每条以 JSON 输出: { "summary": "...", "detail": "..." }
```

### 5.3 对话回复 Prompt 模板

```yaml
# configs/prompts/reply.yaml
name: message.reply
version: 1
template: |
  你是 {name}，{personality}。{backstory}

  当前状态: {location}, {mood}
  正在做: {current_action}

  [近期对话]
  {conversation}

  [相关记忆]
  {memories}

  [用户消息]
  {user_message}

  请以 {name} 的口吻回复，保持性格一致。
```

---

## 六、场景配置

```yaml
# configs/scenes.yaml
scenes:
  - id: cafe
    name: 咖啡店
    open_hours: [7, 22]            # 营业时间
    capacity: 20                    # 最大容量
    activities: [eat, drink, work_parttime, chat]
  - id: school
    name: 学校
    open_hours: [8, 17]
    capacity: 50
    activities: [study, chat]
    workday_only: true
  - id: park
    name: 公园
    open_hours: [0, 24]
    capacity: 100
    activities: [relax, chat, exercise]
  - id: home
    name: 家
    open_hours: [0, 24]
    capacity: 5
    activities: [sleep, eat, relax]
```

---

## 七、默认值速查

| 配置 | 默认 | 来源 |
|------|------|------|
| World Tick 间隔 | 30s | 真实时间 |
| 虚拟时间推进 | 10 分钟/Tick | 虚拟时间 |
| 角色并发上限 | 10 | — |
| 记忆 Top-K | 10 | 检索 |
| 反思阈值 | 20 条 | — |
| embedding 维度 | 1536 | OpenAI small |
| 连接池大小 | 20 | — |
| LLM 超时 | 30s | — |
| LLM 日预算 | 10.0 USD | 超预算降级到 flash |
| MCP 工具超时 | 30s | — |
| MCP 插件开关 | 全部启用 | Redis `mcp:enabled` 未配置时默认 |
| 记忆 LLM 评分 | 关闭 (`false`) | `MEMORY_LLM_SCORING_ENABLED` |
| OneBot 群聊回复模式 | 智能回复 (`false`) | `ONEBOT_GROUP_AT_ONLY` |
| OneBot 群聊回复概率上限 | 0.4 | `GROUP_REPLY_PROBABILITY_CAP` |
| JWT 过期 | 24h | — |
| Trace 采样率 | 0.5 | — |

---

## 八、相关文档

| 主题 | 文档 |
|------|------|
| 部署环境变量 | [deployment.md](deployment.md#三环境变量清单) |
| Docker 部署 | [docker-deployment.md](docker-deployment.md) |
| 模块系统 | [module-system.md](module-system.md) |
| 世界引擎参数 | [world-engine.md](world-engine.md#六配置参数) |
| 记忆系统（LLM 评分） | [memory-system.md](memory-system.md#九llm-记忆重要程度评分) |
