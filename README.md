# AI Town — 二次元AI小镇陪伴智能体

> 由 LLM 驱动的多智能体虚拟小镇。AI 角色拥有独立记忆、反思、规划与社交能力，在持续运行的虚拟世界中自主生活。

核心理念：**不做"随叫随到的AI助手"，而是做一群有自己生活的"人"**。用户的每一次对话，都来自角色在小镇中真实经历的事件，而非临时生成的人设文本。

---

## 项目特性

| 特性 | 说明 |
|------|------|
| 多角色共居 | 支持 10–50 个 AI 角色同时在小镇中生活、决策、交互 |
| 世界持续运行 | 世界状态推进不依赖用户消息，角色在用户不在时依然生活 |
| 记忆与演化 | 角色拥有记忆流、反思能力和长期规划，行为长期一致且可演化 |
| 可插拔能力 | 功能模块（代码执行、搜索、绘图等）可动态启用/禁用，热插拔 |
| 全链路可观测 | 每个决策周期可追踪、可审计、可调试 |
| 多端触达 | 支持 Web Dashboard、QQ、飞书等多渠道交互 |

---

## 技术栈速览

| 层次 | 选型 |
|------|------|
| Agent 框架 | LangGraph (Python) |
| Web 框架 | FastAPI |
| 异步驱动 | asyncpg + SQLAlchemy 2.0 (async) |
| ORM 迁移 | alembic |
| 前端 | React 19 + TanStack Router/Query + Zustand + shadcn/ui + Tailwind v4 |
| 构建工具 | Vite 8 / pnpm 11 |
| 主数据库 | PostgreSQL 17 + pgvector + JSONB + 分区表 |
| 缓存/实时 | Redis 7.4+ |
| 对象存储 | MinIO / AWS S3 |
| 消息队列 | Redis Streams |
| 工具调用 | MCP 协议 |
| 可观测性 | OpenTelemetry + Langfuse + Prometheus + Grafana + Jaeger |

> 数据持久化已**统一收敛到 PostgreSQL（含 pgvector 向量检索）**，不使用 MongoDB 与独立向量库。详见 [架构设计](docs/architecture.md)。

---

## 快速开始

### 环境要求

- Python 3.12+
- Node.js 22+ / pnpm 11+
- PostgreSQL 17+ (启用 `vector`、`uuid-ossp`、`pg_trgm` 扩展)
- Redis 7.4+

### 启动后端

```bash
cd packages/backend
uv sync                           # 或 poetry install
cp ../../.env.example .env        # 填写 LLM/DB/Redis 等密钥
alembic upgrade head              # 执行数据库迁移
uvicorn src.main:app --reload --port 8000
```

### 启动前端

```bash
cd packages/frontend
pnpm install
pnpm dev
```

### 一键编排（推荐）

```bash
docker compose up -d              # 启动 PG / Redis / MinIO / MCP Servers / 后端 / 前端
```

详细部署见 [部署与运维](docs/deployment.md)。

---

## 文档导航

所有设计文档位于 [`docs/`](docs/) 目录：

### 设计文档

| 文档 | 内容 |
|------|------|
| [总体架构设计](docs/architecture.md) | 分层架构、数据流闭环、技术栈、去 MongoDB 决策 |
| [世界引擎设计](docs/world-engine.md) | World Tick / Character Tick / 多智能体调度 |
| [Action系统设计](docs/action-system.md) | Action 定义、分类、执行闭环、注册机制 |
| [记忆系统设计](docs/memory-system.md) | 三层记忆、pgvector 检索、反思、规划 |
| [模块与MCP系统设计](docs/module-system.md) | 模块管理器、生命周期、MCP 工具调用层 |
| [消息服务设计](docs/messaging-service.md) | 多平台接入、消息标准化、主动推送 |

### 接口与数据

| 文档 | 内容 |
|------|------|
| [数据模型设计](docs/data-model.md) | 全部 DDL、ER 图、索引策略 |
| [API设计文档](docs/api-spec.md) | RESTful 端点、WebSocket/SSE、请求/响应示例 |
| [配置参考](docs/config-reference.md) | 环境变量、config.yaml、模块配置 |

### 工程实践

| 文档 | 内容 |
|------|------|
| [前端设计](docs/frontend-design.md) | 页面结构、目录结构、实时数据流 |
| [可观测性设计](docs/observability.md) | 埋点矩阵、链路追踪、指标与告警 |
| [部署与运维](docs/deployment.md) | 部署架构、容器化、环境变量、容量规划 |
| [开发指南](docs/development-guide.md) | 本地开发、代码规范、测试、贡献流程 |

### 迁移

| 文档 | 内容 |
|------|------|
| [迁移指南](docs/migration-guide.md) | 从 MongoDB 迁移到 PostgreSQL 的步骤与脚本 |

---

## 项目结构

```
ai-town/
├── packages/
│   ├── backend/                # Python 后端 (FastAPI + LangGraph)
│   │   ├── src/
│   │   │   ├── core/           # 世界引擎 / Action 系统
│   │   │   ├── agents/         # 角色实现
│   │   │   ├── memory/         # 记忆系统
│   │   │   ├── modules/        # 模块管理器
│   │   │   ├── tools/          # MCP 集成
│   │   │   ├── messaging/      # 消息服务
│   │   │   ├── api/            # FastAPI 路由
│   │   │   ├── db/             # 数据访问层 (models / repositories / migrations)
│   │   │   ├── observability/  # OTel 配置
│   │   │   └── main.py
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── tests/
│   ├── frontend/               # React 前端
│   ├── mcp-servers/            # MCP Server 集合
│   └── shared/                 # 前后端共享 (types / openapi)
├── docs/                       # 项目文档
├── docker-compose.yml
├── config.yaml
├── .env.example
└── README.md
```

---

## 设计原则

| 原则 | 说明 |
|------|------|
| 状态驱动 | LLM 是决策和生成能力，不是状态真相源；所有状态变更由代码执行 |
| 事实优先 | 所有可追溯事实必须落到行为记录或明确的状态字段中 |
| 闭环演化 | 行为沉淀为记忆 → 记忆影响未来决策 → 形成可追溯的生活轨迹 |
| 模块解耦 | 核心引擎与功能模块分离，模块可独立开关、独立升级 |
| 可观测性 | 埋点即契约，所有关键路径必须有 Trace 覆盖 |

---

## 许可证

(待补充)
