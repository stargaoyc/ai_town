# AI Town — AI小镇陪伴智能体

> 由 LLM 驱动的多智能体虚拟小镇。AI 角色拥有独立记忆、反思、规划与社交能力，在持续运行的虚拟世界中自主生活，并可主动通过 QQ 与你建立长期陪伴关系。

核心理念：**不做"随叫随到的AI助手"，而是做一群有自己生活的"人"**。用户的每一次对话，都来自角色在小镇中真实经历的事件，而非临时生成的人设文本。角色不仅会在被 @ 时回复，还能读懂群聊上下文、主动找你聊天、把日常经历分享给你——你不在的时候，他们也在认真生活。

---

## 项目特性

| 特性                 | 说明                                                                                                                                                      |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 多角色共居           | AI 角色在小镇中生活、决策、交互                                                    |
| 世界持续运行         | 世界状态推进不依赖用户消息，角色在用户不在时依然生活                                                                                                      |
| 记忆与演化           | 角色拥有记忆流、反思能力和长期规划，行为长期一致且可演化                                                                                                  |
| 本地工具单独开关 | 本地工具可在前端 Dashboard 单独启用/禁用，状态持久化到 Redis hash `tools:enabled`，无需重启后端 |
| ReAct 工具调用   | 角色决策时可调用本地工具，LLM 决策→执行工具→观察结果→再次决策，让角色"先查询再行动"           |
| 全链路可观测         | 每个决策周期可追踪、可审计、可调试                                                                                                                        |
| 多端触达             | 支持 Web Dashboard、QQ等多渠道交互                                                                                                                  |                                                                            |
| 主动分享         | 角色在 Tick 中产生分享意图时，会主动把小镇中刚发生的事推送给你                                                  |
| 反思系统         | 角色定期从记忆流中归纳出高层认知，影响后续决策，让陪伴更"懂你"                                                   |
| LLM 记忆评分     | 通过 `MEMORY_LLM_SCORING_ENABLED` 开关启用 LLM 对事件重要程度进行 1-10 分评分（基于情感强度、关系影响、稀缺性、后续影响） |
| 角色日记         | 基于一段时间内的记忆事件，由 LLM 生成第一人称叙事日记（日/周/月/年四种周期），作为角色情感与经历的浓缩归档，不替代事件级真相源                            |
| Person Memory    | 角色对不同用户的专属记忆归档，记录偏好、互动历史与情感连接，按热度排序，让角色在后续对话中体现「我记得你」                                                |
| Docker 一键部署  | 提供完整的 Docker Compose 编排（多阶段构建 + Nginx 反代 + Profile 按需启动），支持开发/生产/可观测性多种部署模式                                          |

---

## 技术栈速览

| 层次       | 选型                                                                 |
| ---------- | -------------------------------------------------------------------- |
| Agent 框架 | LangChain (Python 3.13)                                              |
| Web 框架   | FastAPI                                                              |
| 包管理     | uv                                                                   |
| 异步驱动   | asyncpg + SQLAlchemy 2.0                           |
| ORM 迁移   | alembic                                                              |
| 前端       | React 19.2 + TypeScript 7.0 + Vite (Rolldown) 8.1 + React Compiler   |
| 前端状态   | TanStack Router 1.170 + TanStack Query 5.101 + Zustand 5.0 + Zod 4.4 |
| 前端 Lint  | oxlint + oxfmt                                                       |
| 前端组件   | shadcn/ui + Tailwind CSS v4 + Framer Motion                          |
| 主数据库   | PostgreSQL 18 + pgvector + pg_uuidv7 + JSONB + 分区表                |
| 缓存/实时  | Redis 8.0                                                            |
| 消息队列   | Redis Streams                                                        |
| 连接池     | PgBouncer                                                            |
| 工具调用   | 本地工具注册表（ToolRegistry，进程内 async 函数，ReAct 循环）        |
| 可观测性   | OpenTelemetry + Langfuse + Prometheus + Grafana + Jaeger + Loki      |

> 数据持久化统一基于 **PostgreSQL 18 + pgvector**（结构化数据 + 向量检索 + JSONB 灵活字段 + 分区表）。主键采用 **UUID v7**（时间有序，索引友好）。详见 [架构设计](docs/architecture.md)。

---

## 快速开始

### 环境要求

- **Python 3.13+** / [uv](https://docs.astral.sh/uv/) 包管理器
- **Node.js 22+** / [pnpm](https://pnpm.io/) 11+
- **PostgreSQL 18+**，需启用以下扩展：
  - `pg_uuidv7`（时间有序 UUID 主键）
  - `vector`（pgvector 向量检索）
  - `pg_trgm`（模糊匹配）
- **Redis 8.0+**（缓存、分布式锁、消息队列、实时状态）
- （可选）一个 OneBot v11/v12 实现，如 [NapCat](https://github.com/NapNeko/NapCatQQ) 或 [Lagrange](https://github.com/LagrangeDev/Lagrange.Core)，用于接入 QQ

### 启动后端（详细步骤）

```bash
# 1. 进入后端目录
cd packages/backend

# 2. 安装依赖（推荐使用 uv）
uv sync

# 3. 准备环境变量
cp ../../.env.example .env        # 从模板复制配置文件
#   编辑 .env，至少填写：
#     - DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_town
#     - REDIS_URL=redis://localhost:6379/0
#     - OPENAI_API_KEY=sk-...
#     - JWT_SECRET=<随机字符串>
#     - （可选）ONEBOT_DEFAULT_CHARACTER_ID=<角色 UUID>

# 4. 执行数据库迁移（创建表结构、索引、扩展）
alembic upgrade head

# 5. 启动开发服务器
uvicorn src.main:app --reload --port 8000
```

启动成功后：

- API 文档：http://localhost:8000/docs
- OneBot 反向 WebSocket 端点：`ws://localhost:8000/ws/onebot/v12`

### 启动前端

```bash
cd packages/frontend
pnpm install
pnpm dev                          # 默认监听 http://localhost:5173
```

### 一键编排

推荐使用 Docker Compose 一键拉起完整依赖栈：

```bash
docker compose up -d              # 启动 PG / Redis / 后端（含本地工具） / 前端
```

详细部署架构、容器化方案与容量规划见 [部署与运维](docs/deployment.md)。

---

## QQ 机器人接入

AI Town 通过 OneBot v11/v12 协议接入 QQ，让二次元角色真正"住进"你的 QQ。以下能力开箱即用：

- **OneBot 反向 WebSocket 接入**：后端在 `/ws/onebot/v12` 暴露 WebSocket 服务端，由 OneBot 实现（NapCat / Lagrange 等）作为客户端主动反连，无需后端暴露公网入口。
- **群聊智能回复**：默认 `ONEBOT_GROUP_AT_ONLY=false`，角色会读取所有群消息，按"角色名命中 → 疑问/情绪启发式 → 轻量 LLM 判断"三层策略决策是否回复；被 @ 时则始终回复。
- **多段回复**：长回复按段落（`\n\n`）拆分为多条消息依次发送，段间附带约 0.6 秒打字间隔，单段上限 500 字符避免截断，更像真人说话节奏。
- **主动分享推送**：角色在 Character Tick 中产生 `proactiveShareIntent` 时，会查询该角色在 QQ 平台的所有活跃会话，通过 `OneBotAdapter.push_share` 主动推送分享文案——无需你先发消息。

### 配置示例（`.env`）

```bash
# 默认对话角色 UUID（私聊与未配置映射的群都使用此角色）
ONEBOT_DEFAULT_CHARACTER_ID=01964000-0000-7000-8000-000000000001
# 机器人自身 QQ 号（用于群聊 @ 检测；事件 self_id 缺失时回退到此值）
ONEBOT_SELF_ID=123456789
# 群聊是否仅在被 @ 时回复（false=智能回复模式）
ONEBOT_GROUP_AT_ONLY=false
# 群-角色映射：JSON 字符串，为不同群绑定不同角色
ONEBOT_GROUP_CHARACTER_MAP={"987654321":"01964000-0000-7000-8000-000000000002"}
```

详见 [消息服务设计](docs/messaging-service.md)。

---

## 文档导航

所有设计文档位于 [`docs/`](docs/) 目录：

### 设计文档

| 文档                                          | 内容                                                                   |
| --------------------------------------------- | ---------------------------------------------------------------------- |
| [总体架构设计](docs/architecture.md)          | 分层架构、数据流闭环、技术栈、关键架构决策                             |
| [详细架构设计](docs/detailed-architecture.md) | 数据库设计、缓存设计、核心循环、工具系统、可观测性、部署的深度细节参考 |
| [角色设计](docs/character-design.md)          | 角色档案、实时状态、记忆模型、计划系统、关系图谱、角色卡               |
| [小镇设计](docs/town-design.md)               | 世界地图、场景清单、移动矩阵、资源系统、节日与事件                     |
| [世界引擎设计](docs/world-engine.md)          | World Tick / Character Tick / 演化列表 / 作息 / 动态耗时               |
| [Action系统设计](docs/action-system.md)       | Action 定义、结构化决策、参数化、完成事件、主动分享、LLM 边界          |
| [记忆系统设计](docs/memory-system.md)         | 三层记忆、pgvector 检索、反思、规划                                    |
| [模块与工具系统设计](docs/module-system.md)   | 模块管理器、生命周期、本地工具调用层（ToolRegistry）                   |
| [消息服务设计](docs/messaging-service.md)     | 多平台接入、消息标准化、主动推送、群聊智能回复、多段回复               |

### 接口与数据

| 文档                                 | 内容                                       |
| ------------------------------------ | ------------------------------------------ |
| [数据模型设计](docs/data-model.md)   | 全部 DDL、ER 图、索引策略                  |
| [API设计文档](docs/api-spec.md)      | RESTful 端点、WebSocket/SSE、请求/响应示例 |
| [配置参考](docs/config-reference.md) | 环境变量、config.yaml、模块配置            |

### 工程实践

| 文档                                         | 内容                                                                 |
| -------------------------------------------- | -------------------------------------------------------------------- |
| [前端设计](docs/frontend-design.md)          | 页面结构、目录结构、实时数据流                                       |
| [可观测性设计](docs/observability.md)        | 埋点矩阵、链路追踪、指标与告警                                       |
| [部署与运维](docs/deployment.md)             | 部署架构、容器化、环境变量、容量规划                                 |
| [Docker 部署指南](docs/docker-deployment.md) | 完整 Docker Compose 编排、多阶段构建、Profile 按需启动、生产环境配置 |
| [开发指南](docs/development-guide.md)        | 本地开发、代码规范、测试、贡献流程                                   |
| [开发路线图](docs/roadmap.md)                | 分阶段任务清单、里程碑、风险与依赖                                   |

---

## 项目结构

```
ai-town/
├── packages/
│   ├── backend/                # Python 后端 (FastAPI + LangChain)
│   │   ├── src/
│   │   │   ├── core/           # 世界引擎 / Action 系统
│   │   │   ├── agents/         # 角色实现
│   │   │   ├── memory/         # 记忆系统（含 LLM 评分、反思、Embedding Worker）
│   │   │   ├── modules/        # 模块管理器
│   │   │   ├── tools/          # 本地工具注册表
│   │   │   ├── messaging/      # 消息服务（含主动分享）
│   │   │   ├── adapters/       # 平台适配器（OneBot 等）
│   │   │   ├── api/            # FastAPI 路由
│   │   │   ├── db/             # 数据访问层 (models / repositories / migrations)
│   │   │   ├── observability/  # OTel 配置 / Prometheus 指标 / 日志端点
│   │   │   ├── runtime.py      # 运行时依赖容器（消除业务模块对 main.py 的反向依赖）
│   │   │   └── main.py         # FastAPI 入口（lifespan + 路由聚合）
│   │   ├── alembic/            # 数据库迁移脚本
│   │   ├── pyproject.toml
│   │   ├── Dockerfile          # 多阶段构建（uv + Python 3.13-slim）
│   │   └── tests/
│   ├── frontend/               # React 19 前端 (React Compiler + oxlint)
│   │   ├── src/
│   │   │   ├── routes/         # TanStack Router 文件路由
│   │   │   ├── components/     # Glassmorphism 组件 + Framer Motion
│   │   │   └── lib/            # API 客户端 + TanStack Query hooks
│   │   ├── Dockerfile          # 多阶段构建（pnpm + Vite → Nginx）
│   │   └── nginx.conf          # SPA 回退 + API 反代 + WebSocket 反代
├── docs/                       # 项目文档
├── docker-compose.yml          # 完整生产部署编排
├── config.yaml
├── .env.example
└── README.md
```

---

## 设计原则

| 原则     | 说明                                                         |
| -------- | ------------------------------------------------------------ |
| 状态驱动 | LLM 是决策和生成能力，不是状态真相源；所有状态变更由代码执行 |
| 事实优先 | 所有可追溯事实必须落到行为记录或明确的状态字段中             |
| 闭环演化 | 行为沉淀为记忆 → 记忆影响未来决策 → 形成可追溯的生活轨迹     |
| 模块解耦 | 核心引擎与功能模块分离，模块可独立开关、独立升级             |
| 可观测性 | 埋点即契约，所有关键路径必须有 Trace 覆盖                    |

---

## 配置速查

以下为 `.env` 中关键配置项，完整说明见 [配置参考](docs/config-reference.md)。

### LLM 配置

```bash
OPENAI_API_KEY=sk-...                              # OpenAI 兼容 API Key
OPENAI_BASE_URL=https://api.openai.com/v1          # 可指向任何 OpenAI 兼容服务
MODEL_CHAT=gpt-4o-mini                             # 日常对话模型
MODEL_STRONG=gpt-4o                                # 强推理模型（决策、反思）
MODEL_FLASH=gpt-3.5-turbo                          # 轻量判断模型（群聊回复决策）
MODEL_EMBEDDING=text-embedding-3-small             # 向量化模型
EMBEDDING_DIM=1536                                 # 向量维度（需与 pgvector 列维度一致）
LLM_TIMEOUT=30                                     # 单次请求超时（秒）
LLM_DAILY_BUDGET_USD=10.0                          # 每日 LLM 成本预算上限
```

### 数据库配置

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_town
DB_POOL_SIZE=20                                    # 连接池大小
DB_MAX_OVERFLOW=10                                 # 连接池溢出上限
DB_ECHO=false                                      # 是否打印 SQL
```

### Redis 配置

```bash
REDIS_URL=redis://localhost:6379/0                 # 缓存 / 分布式锁 / 消息队列 / 实时状态
```

### OneBot QQ 配置

```bash
ONEBOT_DEFAULT_CHARACTER_ID=01964000-...           # 默认对话角色 UUID（必填，否则机器人无法回复）
ONEBOT_SELF_ID=123456789                           # 机器人自身 QQ 号（用于群聊 @ 检测）
ONEBOT_GROUP_AT_ONLY=false                         # 群聊是否仅在被 @ 时回复（false=智能回复模式）
ONEBOT_GROUP_CHARACTER_MAP={"群号":"角色UUID"}       # 群-角色映射 JSON，未配置的群使用默认角色
```

> **说明**：`ONEBOT_DEFAULT_CHARACTER_ID` 是 QQ 接入的最低门槛，未配置时机器人会向用户回复"尚未配置对话角色"。`ONEBOT_GROUP_AT_ONLY=false` 时启用智能回复，会读取所有群消息并按三层策略决策回复；设为 `true` 则仅在被 @ 时回复，成本最低。

---

## 许可证

AGPLv3
