# AI Town — AI小镇陪伴智能体

> 由 LLM 驱动的多智能体虚拟小镇。AI 角色拥有独立记忆、反思、规划与社交能力，在持续运行的虚拟世界中自主生活，并可主动通过 QQ 与你建立长期陪伴关系。

核心理念：**不做"随叫随到的AI助手"，而是做一群有自己生活的"人"**。用户的每一次对话，都来自角色在小镇中真实经历的事件，而非临时生成的人设文本。角色不仅会在被 @ 时回复，还能读懂群聊上下文、主动找你聊天、把日常经历分享给你——你不在的时候，他们也在认真生活。

---

## 项目特性

| 特性 | 说明 |
|------|------|
| 多角色共居 | 多个 AI 角色在小镇中生活、决策、交互 |
| 世界持续运行 | 世界状态推进不依赖用户消息，30 秒世界节拍，角色在用户不在时依然生活 |
| 记忆与演化 | 角色拥有记忆流（pgvector 混合检索 + 重要性 0.25 权重 + 指数衰减 25% 下限）、反思系统和长期规划 |
| 社交会话实体 | 角色间对话升级为事件驱动会话实体（Redis 持久化），三层终止机制防无休止——轮数硬上限/LLM 软结束/超时死亡|
| 关系记忆注入 | chat_with 对话前检索双方共同经历（related_characters GIN 索引），让角色「还记得上次…」 |
| 消息回复注入近期经历 | 私聊回复对齐 Tick 决策感知：语义检索相关记忆 top_k=12 + 传闻 + 世界动态 + 当前计划，角色回复时知道"自己最近在小镇里经历过什么" |
| 本地工具单独开关 | 本地工具可在前端 Dashboard 单独启用/禁用，状态持久化到 Redis hash `tools:enabled`，无需重启后端 |
| ReAct 工具调用 | 角色决策时可调用本地工具（shop/knowledge/social/world/self_info/media），LLM 决策→执行工具→观察结果→再次决策，工具参数经类型/范围/枚举校验 |
| 全链路可观测 | 每个决策周期可追踪、可审计、可调试（OpenTelemetry + Langfuse + Prometheus + Grafana） |
| 多端触达 | 支持 Web Dashboard、QQ 多频道交互，事件队列至少一次语义 + 死信隔离 |
| 主动分享 | 角色在 Tick 中产生分享意图时，主动把小镇中刚发生的事推送给你 |
| 反思系统 | 双层反思，语义去重 + 重大事件双通道触发，回灌决策形成闭合回路 |
| LLM 记忆评分 | 通过 `MEMORY_LLM_SCORING_ENABLED` 开关启用 LLM 对事件重要程度进行 1-10 分评分 |
| 角色日记 | 基于记忆事件由 LLM 生成第一人称叙事日记（日/周/月/年），作为情感与经历的浓缩归档 |
| Person Memory | 两层结构（append-only 事实条目 + 主档），语义召回（pgvector）替代字符二元组重叠，按热度排序 |
| 群体动力学 | 传闻传播、共同经历标记与临时群聚（group_activity），关系网络随互动自然生长 |
| 记忆生命周期治理 | 分级保留（importance 分级 + 永久阈值可配）、压缩归档、改写式去重，长期运行记忆不膨胀 |
| Embedding 多源 fallback | embed/embed_batch 纳入多源 fallback（失败自动切源 + 5 分钟冷却），与 chat 链路策略统一 |
| 维度自动同步 | 启动时幂等对齐向量列维度到 EMBEDDING_DIM（2048→4000 自动重建），换模型无需新增迁移 |
| 迁移协调锁 | 多副本部署时 PG advisory lock 协调 alembic 迁移，消除 RUN_MIGRATIONS 手动约定 |
| RBAC 权限接线 | JWT 与 API Key 同等对待，角色显式可配（api_key_role），集中归属校验防越权 |
| Docker 一键部署 | 完整 Docker Compose 编排（多阶段构建 + Nginx 反代 + Profile 按需启动），支持开发/生产/可观测性三种模式 |

---

## 技术栈速览

| 层次 | 选型 |
|------|------|
| Agent 框架 | LangChain+ 原生 AsyncOpenAI，双栈统一超时/重试/降级策略 |
| Web 框架 | FastAPI |
| 包管理 | uv |
| 异步驱动 | asyncpg + SQLAlchemy 2.0 |
| ORM 迁移 | alembic |
| 前端 | React 19.2 + TypeScript 7.0 + Vite 8.1 |
| 前端状态 | TanStack Router 1.170 + TanStack Query 5.101 + Zustand 5.0 |
| 前端 Lint | oxlint + oxfmt |
| 前端组件 | 自建 Glassmorphism 组件库 + Tailwind CSS v4 + Framer Motion |
| 主数据库 | PostgreSQL 18 + pgvector + JSONB + 分区表（RANGE 月分区/HASH 16 分区） |
| 缓存/实时状态 | Redis 8.0（Hash + Streams + 分布式锁） |
| 消息队列 | Redis Streams Consumer Group（至少一次语义 + 死信流隔离） |
| 工具调用 | 本地工具注册表（ToolRegistry，18 个工具，进程内 async 函数，ReAct 循环） |
| 可观测性 | OpenTelemetry + Langfuse 自托管 + Prometheus + Grafana + Jaeger + Loki |

> 数据持久化统一基于 **PostgreSQL 18 + pgvector**（结构化数据 + 向量检索 + JSONB 灵活字段 + 分区表）。主键采用 **UUID v7**（时间有序，索引友好）。详见 [架构设计](docs/architecture.md)。

---

## 快速开始

### 环境要求

- **Python 3.13+** / [uv](https://docs.astral.sh/uv/) 包管理器
- **Node.js 22+** / [pnpm](https://pnpm.io/) 11+
- **PostgreSQL 18+**（PG 18 内建 `uuidv7()`），需启用 `vector`（pgvector 向量检索）
- **Redis 8.0+**（缓存、分布式锁、消息队列、实时状态）
- **Embedding 服务**：默认使用本地 Qwen3-Embedding-8B，也可配置 OpenAI 兼容 embedding API
- （可选）一个 OneBot v11/v12 实现，如 [NapCat](https://github.com/NapNeko/NapCatQQ) 或 [Lagrange](https://github.com/LagrangeDev/Lagrange.Core)，用于接入 QQ

### 一键编排（推荐）

```bash
# 1. 复制环境变量配置
cp .env.example .env
#    编辑 .env，至少填写：
#    - POSTGRES_PASSWORD / REDIS_PASSWORD（compose 强制必填）
#    - OPENAI_API_KEY / OPENAI_BASE_URL（LLM 供应商）
#    - EMBEDDING_MODEL_URL / EMBEDDING_MODEL_KEY（embedding 服务）
#    - JWT_SECRET（随机字符串）
#    - LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY（可观测性，见下方）

# 2. 启动完整栈（PG / Redis / 后端 / 前端）
docker compose up -d

# 3. （可选）启动可观测性栈（Langfuse + Prometheus + Grafana + Jaeger）
docker compose --profile observability up -d
```

启动成功后：
- 后端 API：http://localhost:8001/docs
- 前端：http://localhost:80
- Langfuse：http://localhost:3001（首次需创建账号 + API Key）
- Grafana：http://localhost:3000
- Jaeger：http://localhost:16686

### 本地开发（裸机启动）

```bash
# 后端
cd packages/backend
uv sync
cp ../../.env.example .env
# 编辑 .env，至少填写 DATABASE_URL / REDIS_URL / OPENAI_API_KEY / JWT_SECRET
alembic upgrade head
uvicorn src.main:app --reload --port 8000

# 前端（新终端）
cd packages/frontend
pnpm install
pnpm dev                         # http://localhost:5173
```

### 自托管 Langfuse 配置

首次启动 observability profile 后：

1. 打开 http://localhost:3001 → 注册账号
2. 登录后创建 Organization 和 Project
3. 进入 Project Settings → API Keys → 生成 `pk-lf-...` 和 `sk-lf-...`
4. 写入 `.env`：
   ```bash
   LANGFUSE_HOST=http://localhost:3001
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   ```
5. 重启后端容器：`docker compose up -d --force-recreate backend`

---

## QQ 机器人接入

AI Town 通过 OneBot v11/v12 协议接入 QQ，让角色真正"住进"你的 QQ。以下能力开箱即用：

- **OneBot 反向 WebSocket 接入**：后端在 `/ws/onebot/v12` 暴露 WebSocket 服务端，由 OneBot 实现（NapCat / Lagrange 等）作为客户端主动反连，无需后端暴露公网入口。
- **群聊智能回复**：默认 `ONEBOT_GROUP_AT_ONLY=false`，角色会读取所有群消息，按三层策略决策是否回复；被 @ 时则始终回复。
  > 隐私提示：智能回复模式会将群消息内容发送给所配置的 LLM 服务用于回复决策。若群成员对消息出域敏感，请设为 `ONEBOT_GROUP_AT_ONLY=true`。
- **多段回复**：长回复按段落拆分发送，段间附带打字间隔，单段上限 500 字符。
- **主动分享推送**：角色在 Tick 中产生 `proactiveShareIntent` 时，主动推送分享文案到 QQ。

### 配置示例（`.env`）

```bash
ONEBOT_DEFAULT_CHARACTER_ID=01964000-0000-7000-8000-000000000001
ONEBOT_SELF_ID=123456789
ONEBOT_GROUP_AT_ONLY=false
ONEBOT_GROUP_CHARACTER_MAP={"987654321":"01964000-0000-7000-8000-000000000002"}
```

详见 [消息服务设计](docs/messaging-service.md)。

---

## 文档导航

所有设计文档位于 [`docs/`](docs/) 目录：

### 设计文档

| 文档 | 内容 |
|------|------|
| [总体架构设计](docs/architecture.md) | 分层架构、数据流闭环、技术栈、关键架构决策 |
| [详细架构设计](docs/detailed-architecture.md) | 数据库设计、缓存设计、核心循环、工具系统、可观测性、部署的深度细节 |
| [角色设计](docs/character-design.md) | 角色档案、实时状态、记忆模型、计划系统、关系图谱、角色卡 |
| [小镇设计](docs/town-design.md) | 世界地图、场景清单、移动矩阵、资源系统、节日与事件 |
| [世界引擎设计](docs/world-engine.md) | World Tick / Character Tick / 演化列表 / 作息 / 动态耗时 |
| [Action系统设计](docs/action-system.md) | Action 定义、结构化决策、参数化、完成事件、主动分享、LLM 边界 |
| [记忆系统设计](docs/memory-system.md) | 三层记忆、pgvector 混合检索（权重 0.25）、反思、规划、社交会话 |
| [模块与工具系统设计](docs/module-system.md) | 模块管理器、生命周期、本地工具调用层（ToolRegistry）、参数校验 |
| [消息服务设计](docs/messaging-service.md) | 多平台接入、消息标准化、主动推送、群聊智能回复、多段回复、近期经历注入 |

### 接口与数据

| 文档 | 内容 |
|------|------|
| [数据模型设计](docs/data-model.md) | 全部 DDL、ER 图、索引策略 |
| [API设计文档](docs/api-spec.md) | RESTful 端点、WebSocket/SSE、请求/响应示例 |
| [配置参考](docs/config-reference.md) | 环境变量、运行时热更新配置、模块配置（138 项） |

### 工程实践

| 文档 | 内容 |
|------|------|
| [前端设计](docs/frontend-design.md) | 页面结构、目录结构、实时数据流 |
| [可观测性设计](docs/observability.md) | 埋点矩阵、链路追踪、指标与告警、Langfuse 自托管 |
| [部署与运维](docs/deployment.md) | 部署架构、容器化、环境变量、容量规划 |
| [Docker 部署指南](docs/docker-deployment.md) | Docker Compose 编排、多阶段构建、Profile 按需启动、迁移协调锁 |
| [开发指南](docs/development-guide.md) | 本地开发、代码规范、测试、贡献流程 |
| [开发路线图](docs/roadmap.md) | 分阶段任务清单、里程碑、风险与依赖 |

---

## 项目结构

```
ai-town/
├── packages/
│   ├── backend/                # Python 后端 (FastAPI + LangChain + 原生 SDK)
│   │   ├── src/
│   │   │   ├── core/           # 世界引擎 / Action 系统 / 角色 Tick / 社交会话
│   │   │   ├── memory/         # 记忆系统（LLM 评分、反思、Embedding Worker、社交会话、日记）
│   │   │   ├── modules/        # 模块管理器（town/schedule/movement/relation/character）
│   │   │   ├── tools/          # 本地工具注册表（工具，参数校验）
│   │   │   ├── messaging/      # 消息服务（含主动分享、事件队列、出站过滤）
│   │   │   ├── adapters/       # 平台适配器（OneBot 等）
│   │   │   ├── auth/           # RBAC 权限（JWT + API Key 双模式，集中归属校验）
│   │   │   ├── api/            # FastAPI 路由
│   │   │   ├── services/       # 服务层（WorldService/ActionService/MemoryService 等）
│   │   │   ├── db/             # 数据访问层（models / repositories / embedding_dim_sync / migrations）
│   │   │   ├── cost_control/   # 成本控制（原子预算、角色/用户配额、熔断器）
│   │   │   ├── security/       # Prompt 注入防护、启动校验、限流
│   │   │   ├── scheduler/      # 调度器（后台任务 + 保留周期）
│   │   │   ├── observability/  # OTel + Langfuse + Prometheus 可观测性
│   │   │   ├── runtime.py      # 运行时依赖容器
│   │   │   └── main.py         # FastAPI 入口（lifespan + 路由聚合）
│   │   ├── alembic/            # 23 个数据库迁移
│   │   ├── eval_data/          # 检索质量评估标注集
│   │   ├── scripts/            # 运维脚本（迁移协调、维度同步、LLM 可用性测试、评估）
│   │   ├── Dockerfile          # 多阶段构建（uv + Python 3.13-slim）
│   │   └── tests/              # 测试（单元测试 + 集成测试）
│   ├── frontend/               # React 19 前端
│   │   ├── src/
│   │   │   ├── routes/         # TanStack Router 文件路由
│   │   │   ├── components/     # Glassmorphism 组件 + Framer Motion
│   │   │   └── lib/            # API 客户端 + TanStack Query hooks
│   │   └── Dockerfile          # 多阶段构建（pnpm + Vite → Nginx）
├── docs/                       # 项目文档
├── configs/                    # 配置 YAML（角色卡/场景/地图/事件/Prompt 模板）
├── docker-compose.yml          # 统一编排（基础设施 + 应用 + observability + backup profile）
├── .env.example
├── AGENTS.md                   # AI Coding Agent 入口规范
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

## 配置速查

以下为 `.env` 中关键配置项，完整说明见 [配置参考](docs/config-reference.md)。

### LLM 配置

```bash
OPENAI_API_KEY=sk-...                              # OpenAI 兼容 API Key
OPENAI_BASE_URL=https://api.openai.com/v1          # 可指向任何 OpenAI 兼容服务
MODEL_CHAT=agnes-2.5-flash                          # 日常对话模型
MODEL_IMAGE=agnes-image-2.1-flash                   # 图像生成模型
MODEL_VIDEO=agnes-video-v2.0                        # 视频生成模型
MODEL_EMBEDDING=text-embedding-3-small             # 向量化模型（可切换至 Qwen3-Embedding-8B 等）
EMBEDDING_DIM=4000                                 # 向量维度（与 pgvector 列维度一致，halfvec 上限 4000）
EMBEDDING_MODEL_URL=                               # Embedding 专用 API URL（如 OpenRouter/本地服务）
EMBEDDING_MODEL_KEY=                                # Embedding 专用 API Key
EMBEDDING_PROBE_ENABLED=true                        # 启动时探测 embedding 模型输出维度
LLM_TIMEOUT=30                                     # 单次请求超时（秒）
LLM_MAX_RETRIES=2                                  # 失败重试次数
LLM_DAILY_BUDGET_USD=10.0                          # 全局日预算上限
LLM_FALLBACK_SOURCES=[]                            # 多源 fallback 配置 JSON
```

### 数据库配置

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_town
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
```

### Redis 配置

```bash
REDIS_URL=redis://localhost:6379/0
```

### 可观测性（Langfuse 自托管）

```bash
LANGFUSE_HOST=http://localhost:3001
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
# 以下三个密钥由 docker compose observability profile 自动读取
LANGFUSE_NEXTAUTH_SECRET=<随机 64 hex>
LANGFUSE_SALT_KEY=<随机 32 hex>
LANGFUSE_ENCRYPTION_KEY=<随机 64 hex>
```

### 社交会话（交互终止防无休止）

```bash
CHAT_WITH_MAX_ROUNDS=2       # 单次 Action 内最多轮数
CHAT_MAX_TURNS=6             # 会话累计轮数硬上限（跨 Tick 延续后递减）
CHAT_IDLE_TICKS=2            # 超时：N 个世界 Tick 无人回应自动结束
```

### 记忆治理

```bash
MEMORY_WRITE_GATE_ENABLED=true       # 显著性门禁：低重要性 action 不写记忆
MEMORY_WRITE_MIN_IMPORTANCE=4        # 记忆写入最低重要性
MEMORY_RETENTION_PERMANENT_IMPORTANCE=7  # 永久保留阈值
MEMORY_RETENTION_INTERVAL_SECONDS=3600   # 治理周期
MEMORY_COMPRESSION_BATCH_LIMIT=5000      # 单周期最大处理条数
```

### 安全

```bash
JWT_SECRET=<随机字符串>
JWT_ALGORITHM=HS256
JWT_EXPIRE_HOURS=24
API_KEY=your-api-key                    # 生产环境请改为强密码
API_KEY_ROLE=admin                      # 静态 Key 绑定的 RBAC 角色（可下调至 operator/viewer）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123                 # 生产环境请改为强密码
RBAC_ROLES=                             # 逗号分隔的用户名:角色列表
```

### OneBot QQ 配置

```bash
ONEBOT_DEFAULT_CHARACTER_ID=01964000-...   # 必填
ONEBOT_SELF_ID=123456789
ONEBOT_GROUP_AT_ONLY=false
ONEBOT_GROUP_CHARACTER_MAP={}
ONEBOT_ACCESS_TOKEN=                       # 生产环境建议配置
```

---

## 许可证

AGPLv3