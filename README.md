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
| Agent 框架 | LangGraph (Python 3.13) |
| Web 框架 | FastAPI |
| 包管理 | uv |
| 异步驱动 | asyncpg + SQLAlchemy 2.0 (async, 混合策略) |
| ORM 迁移 | alembic |
| 前端 | React 19.2 + TypeScript 7.0 + Vite (Rolldown) 8.1 + React Compiler |
| 前端状态 | TanStack Router 1.170 + TanStack Query 5.101 + Zustand 5.0 + Zod 4.4 |
| 前端 Lint | oxlint + oxfmt |
| 前端组件 | shadcn/ui + Tailwind CSS v4 + Framer Motion |
| 主数据库 | PostgreSQL 17 + pgvector + pg_uuidv7 + JSONB + 分区表 |
| 缓存/实时 | Redis 8.0 |
| 对象存储 | MinIO / AWS S3 |
| 消息队列 | Redis Streams |
| 连接池 | PgBouncer |
| 工具调用 | MCP 协议（自研 + 社区现成） |
| 可观测性 | OpenTelemetry + Langfuse + Prometheus + Grafana + Jaeger + Loki |

> 数据持久化统一基于 **PostgreSQL 17 + pgvector**（结构化数据 + 向量检索 + JSONB 灵活字段 + 分区表）。主键采用 **UUID v7**（时间有序，索引友好）。详见 [架构设计](docs/architecture.md)。

---

## 快速开始

### 环境要求

- Python 3.13+ / uv
- Node.js 22+ / pnpm 11+
- PostgreSQL 17+ (启用 `pg_uuidv7`、`vector`、`pg_trgm` 扩展)
- Redis 8.0+

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
| [总体架构设计](docs/architecture.md) | 分层架构、数据流闭环、技术栈、关键架构决策 |
| [角色设计](docs/character-design.md) | 角色档案、实时状态、记忆模型、计划系统、关系图谱、角色卡 |
| [小镇设计](docs/town-design.md) | 世界地图、场景清单、移动矩阵、资源系统、节日与事件 |
| [世界引擎设计](docs/world-engine.md) | World Tick / Character Tick / 演化列表 / 作息 / 动态耗时 |
| [Action系统设计](docs/action-system.md) | Action 定义、结构化决策、参数化、完成事件、主动分享、LLM 边界 |
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
│   ├── frontend/               # React 19 前端 (React Compiler + oxlint + 二次元现代风)
│   ├── mcp-servers/            # MCP Server 集合 (自研: code-executor/shop/social 等)
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

AGPLv3
