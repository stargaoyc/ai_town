# 详细架构设计文档

> 本文档是 AI Town 的**深度架构参考**，对 [architecture.md](architecture.md)（总览）与 [data-model.md](data-model.md)（数据模型）的二次沉淀，覆盖**数据库设计 / 缓存设计 / 核心循环 / Agent 能力层 / 工具系统 / 消息服务 / LLM 客户端 / 成本控制 / 安全设计 / 可观测性 / 部署架构 / 性能优化 / 配置真相源 / 关键架构决策**等每一个工程细节。
>
> 阅读顺序建议：先看 [architecture.md](architecture.md) 建立总体认知，再看本文档查每一个落点的实现细节。所有内容均对齐 `packages/backend` 最新代码（2026-07-16），覆盖 8 个 Alembic 迁移（`0001` ~ `0008`）、16 个本地工具、ReAct 决策循环、OneBot v11/v12 适配器与完整可观测性栈。

---

## 目录

- [一、文档定位与设计哲学](#一文档定位与设计哲学)
- [二、技术栈全景](#二技术栈全景)
- [三、数据库设计](#三数据库设计)
- [四、缓存设计（Redis）](#四缓存设计redis)
- [五、核心循环详解](#五核心循环详解)
- [六、Agent 能力层](#六agent-能力层)
- [七、本地工具系统（ToolRegistry + ReAct）](#七本地工具系统toolregistry--react)
- [八、消息服务层](#八消息服务层)
- [九、LLM 客户端架构](#九llm-客户端架构)
- [十、成本控制与熔断器](#十成本控制与熔断器)
- [十一、安全设计](#十一安全设计)
- [十二、可观测性体系](#十二可观测性体系)
- [十三、部署架构](#十三部署架构)
- [十四、性能优化与容量规划](#十四性能优化与容量规划)
- [十五、配置真相源全景](#十五配置真相源全景)
- [十六、关键架构决策汇总](#十六关键架构决策汇总)
- [附录 A：术语速查表](#附录-a术语速查表)
- [附录 B：关键代码文件索引](#附录-b关键代码文件索引)
- [附录 C：Alembic 迁移版本演进](#附录-calembic-迁移版本演进)

---

## 一、文档定位与设计哲学

### 1.1 文档定位

本文档面向**后端工程师 / 架构师 / SRE**，目标是让读者能在不读源码的前提下，理解 AI Town 的每一个工程落点、每一个表结构、每一个 Redis key、每一个循环的执行步骤、每一个工具的参数与副作用。

与项目其他文档的分工：

| 文档 | 覆盖范围 | 读者 |
|------|----------|------|
| [architecture.md](architecture.md) | 总体架构与模块关系 | 所有角色 |
| [data-model.md](data-model.md) | 数据模型 ER 图与字段说明 | 后端 / DBA |
| **本文档** | **每一个工程细节、DDL、Key、循环、决策、配置** | **后端 / 架构师 / SRE** |
| [action-system.md](action-system.md) | Action 系统设计 | 后端 |
| [memory-system.md](memory-system.md) | 记忆系统设计 | 后端 |
| [world-engine.md](world-engine.md) | 世界引擎设计 | 后端 |

### 1.2 设计哲学

| 原则 | 落点 | 反模式 |
|------|------|--------|
| **状态驱动** | LLM 是决策与生成能力，**不是状态真相源**；所有状态变更由代码执行落到 PG/Redis | 让 LLM 直接 UPDATE 数据库 |
| **事实优先** | 可追溯事实必须落到 `action_records` / `memory_episodes` / `world_events` | 仅靠日志/内存记录"发生过什么" |
| **闭环演化** | 行为→记忆→反思→未来决策→行为，形成自演化轨迹 | 一次性链路、无反馈循环 |
| **模块解耦** | 核心引擎（World Engine / Character Tick）与功能工具（ToolRegistry / 适配器）分离 | 工具调用嵌入核心循环 |
| **异步化解耦** | 慢操作（embedding、视频生成、主动分享推送）异步化，不阻塞 Tick | 同步等待 LLM embed 完成才写记忆 |
| **可观测性** | 埋点即契约，所有关键路径必须有 Trace 覆盖 | 关键路径无指标、无日志 |
| **成本可控** | LLM 调用必须有日预算 + 熔断器兜底 | 无限制调用 LLM 导致账单失控 |
| **安全前置** | 用户输入必须经 PromptGuard 消毒 + 注入检测 | 直接把用户输入拼到 Prompt |
| **幂等优先** | 事件写入、Tick 执行、消息处理均支持幂等 | 重试导致脏数据 |
| **显式边界** | 函数的输入输出与副作用必须显式 | 隐式修改全局状态 |
| **Upgrade-Only** | 数据库迁移只允许 upgrade，downgrade 仅 `raise RuntimeError` | 依赖 downgrade 回滚造成数据丢失 |
| **单一真相源** | 实时状态以 Redis 为唯一真相源，PG 仅镜像/快照 | Redis 与 PG 双写不一致时不知道以谁为准 |

### 1.3 三大核心循环

AI Town 由三个相互独立但协同的循环驱动：

```text
┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│   World Tick            │  │   Character Tick        │  │   用户消息处理           │
│   世界状态推进           │  │   角色行为闭环           │  │   对话响应              │
│   30s/Tick              │  │   30s/Tick              │  │   事件驱动              │
│   Redis Leader 单实例   │  │   多实例分担            │  │   无状态多实例          │
└─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘
```

- **World Tick**：推进虚拟世界时间 / 天气 / 全局事件，由 Redis 分布式锁保证**全局单实例**运行（避免多实例同时推进导致时间跳跃）。
- **Character Tick**：对每个活跃角色执行"感知→决策→执行→记忆→反思"五阶段闭环，多实例可并行处理不同角色（受 `character_max_concurrent` 信号量约束）。
- **用户消息处理**：响应用户通过 Web WebSocket / OneBot（QQ）发送的消息，无状态多实例可水平扩展。

### 1.4 状态真相源分层

| 数据 | 真相源 | 说明 |
|------|--------|------|
| **角色实时状态** | **Redis** `char:{id}:state` | 唯一真相源，PG `character_states` 仅镜像 |
| **世界实时状态** | **Redis** `world:state` | 唯一真相源，PG `world_snapshots` 仅快照 |
| 角色档案 | PG `characters` 表 | 静态身份信息（姓名/性格/背景） |
| 行为历史 | PG `action_records` 表（按月分区） | 不可变事实记录 |
| 记忆事实 | PG `memory_episodes`（HASH 16 分区）+ pgvector | 不可变经历事件 |
| 反思 | PG `reflections` + `reflection_sources` | 高层认知归纳 |
| 日记 | PG `character_diaries` | 叙事归档层 |
| 用户认知 | PG `person_memories` | 角色对用户的长期记忆 |

> **关键约束**：Action 执行时先写 PG 事务（`action_records` + 状态变更），事务提交后再写 Redis；若 Redis 写入失败，由 PG 镜像回灌。LLM 永远不能直接修改 Redis/PG 状态。

---

## 二、技术栈全景

### 2.1 后端技术栈

| 类别 | 选型 | 版本 | 用途 |
|------|------|------|------|
| 语言 | Python | 3.13+ | 异步原生支持、PEP 604 类型语法（`X \| None`） |
| Web 框架 | FastAPI | 最新 | 异步 REST + WebSocket |
| ORM | SQLAlchemy | 2.0 | 模型/迁移/简单 CRUD |
| 迁移 | Alembic | 最新 | Schema 版本化（upgrade-only） |
| 数据库 | PostgreSQL | 18 | 主存储（分区 + pgvector + pg_uuidv7） |
| 向量扩展 | pgvector | 最新 | HNSW + HALFVEC（halfvec 支持最多 4000 维） |
| UUID 扩展 | pg_uuidv7 | 最新 | 时间有序 UUID（避免 B-tree 页分裂） |
| 文本扩展 | pg_trgm | 最新 | 模糊检索（ similarity 函数） |
| 缓存/锁 | Redis | 8.0-alpine | 实时状态 + 分布式锁 + 预算统计 |
| LLM 编排 | LangChain | 最新 | OpenAI 兼容 API 调用（作为传递依赖） |
| 配置 | pydantic-settings | 最新 | `.env` + 类型安全 + 运行时覆盖 |
| 日志 | structlog | 最新 | JSON 结构化日志 |
| 指标 | prometheus-client | 最新 | Prometheus 指标（Counter/Histogram/Gauge） |
| LLM 追踪 | Langfuse | 3.x | LLM 专用 Trace（prompt/completion/cost） |
| 链路追踪 | OpenTelemetry | 1.28+ | 分布式 Trace（OTLP → Jaeger） |
| 日志聚合 | Loki | 3.0.0 | 结构化日志存储（通过 Alloy 采集） |
| 包管理 | uv | 最新 | Python 包管理 |
| 测试 | pytest | 最新 | 单元 + 集成测试（251 项） |
| Lint | ruff | 最新 | 代码规范（替代 flake8/isort） |
| 类型 | mypy | strict | 静态类型检查（strict 模式） |

### 2.2 前端技术栈

| 类别 | 选型 | 版本 |
|------|------|------|
| 框架 | React | 19.2 |
| 语言 | TypeScript | 7.0 |
| 构建 | Vite (Rolldown) | 8.1 |
| 编译器 | React Compiler | 1.0 |
| 路由 | TanStack Router | 1.170 |
| 数据 | TanStack Query | 5.101 |
| 校验 | Zod | 4.4 |
| 状态 | Zustand | 5.0 |
| 动画 | framer-motion | 最新 |
| Lint | oxlint | 最新 |
| 格式化 | oxfmt | 最新 |
| 包管理 | pnpm | 最新 |

### 2.3 前端设计风格

**Glassmorphism（毛玻璃）+ 二次元配色**：

| 颜色 | 色值 | 用途 |
|------|------|------|
| 樱花粉 | `#FF8FAB` | 主色调（按钮/强调） |
| 天空蓝 | `#7EC8E3` | 次要色（信息/链接） |
| 暮光紫 | `#B19CD9` | 辅助色（卡片/背景） |

### 2.4 Monorepo 结构

```text
aitown/
├── packages/
│   ├── backend/          # 后端（uv 管理，Python 3.13+）
│   │   ├── src/
│   │   │   ├── api/      # FastAPI 路由层
│   │   │   ├── core/     # 核心引擎（world / character）
│   │   │   ├── actions/  # Action 系统
│   │   │   ├── tools/    # 本地工具系统
│   │   │   ├── memory/   # 记忆服务（embedding / reflection / diary）
│   │   │   ├── messaging/# 消息服务（WebSocket / OneBot）
│   │   │   ├── adapters/ # 适配器（OneBot）
│   │   │   ├── llm/      # LLM 客户端
│   │   │   ├── cost_control/ # 成本控制（budget / circuit_breaker）
│   │   │   ├── security/ # 安全（prompt_guard / rate_limiter）
│   │   │   ├── observability/ # 可观测性（metrics / tracing / langfuse）
│   │   │   ├── db/       # 数据库（session / repositories / models）
│   │   │   └── config.py # 配置真相源
│   │   ├── alembic/versions/ # 迁移脚本（0001 ~ 0008）
│   │   └── tests/        # 测试
│   ├── frontend/         # 前端（pnpm 管理）
├── configs/
│   ├── characters/       # 角色卡 YAML（24 个角色）
│   ├── prompts/          # Prompt 模板 YAML
│   ├── scenes.yaml       # 场景配置
│   ├── world-map.yaml    # 世界地图（场景连通矩阵）
│   └── events.yaml       # 事件配置
├── docker/
│   ├── postgres/Dockerfile # PG 18 + pgvector + pg_uuidv7 + pg_trgm
│   └── observability/    # Prometheus / Loki / Jaeger / Alloy / Grafana 配置
├── docs/                 # 文档
├── data/logs/            # 运行时日志
├── docker-compose.yml    # 完整部署编排
└── AGENTS.md             # AI Agent 执行规范
```

### 2.5 工具系统演进

| 阶段 | 架构 | 问题 |
|------|------|------|
| Phase 1 | 独立 MCP Server（HTTP/SSE） | 网络开销大、部署复杂 |
| **Phase 2（当前）** | **本地工具（`src/tools/`）+ ReAct 循环** | **消除网络开销，进程内 async 调用** |

工具从独立 MCP Server 迁移为进程内 async 函数调用，Redis key 从 `mcp:enabled` 改为 `tools:enabled`，API 路径从 `/api/v1/mcp/*` 改为 `/api/v1/tools/*`。

---

## 三、数据库设计

### 3.1 扩展与基础设施

```sql
-- 必须的扩展（在 0001_init.py 中创建）
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- 模糊检索

-- pg_uuidv7 扩展（Docker 镜像预装，提供 uuidv7() 函数）
-- 时间有序 UUID，避免 B-tree 索引页分裂
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;
```

**UUID v7 选型理由**：
- UUID v4（随机）导致 B-tree 索引碎片化，写入性能随数据量增长下降
- UUID v7（时间有序）保证新数据顺序写入索引末尾，减少页分裂
- 所有表主键统一使用 `UUID DEFAULT uuidv7()`

### 3.2 迁移版本演进（0001 ~ 0008）

| 版本 | 文件 | 核心变更 |
|------|------|----------|
| 0001 | `0001_init.py` | 初始化所有核心表 + 扩展 + 分区 |
| 0002 | `0002_optimize.py` | memory_episodes 重建为 HASH 16 分区 + HNSW + 复合外键 + fillfactor 调优 |
| 0003 | `0003_messages.py` | 新增 conversations / messages 表 + fail_count 字段 |
| 0004 | `0004_phase3_refinements.py` | conversations 唯一键扩展 + CHECK 约束 + next_retry_at 指数退避 |
| 0005 | `0005_embedding_dim_2048.py` | **embedding `vector(1536)` → `halfvec(2048)`** + HNSW 重建 |
| 0006 | `0006_world_event_key.py` | world_events 新增 `event_key` 字段 + UNIQUE 约束调整 |
| 0007 | `0007_character_state_history.py` | 新增 `character_state_history` 表（按月分区） |
| 0008 | `0008_add_character_diaries.py` | 新增 `character_diaries` + `person_memories` 表 |

> **Upgrade-Only 原则**：所有 `downgrade()` 函数仅 `raise RuntimeError("Downgrade not supported. Follow upgrade-only principle.")`，禁止回滚。

### 3.3 核心表 DDL 详解

#### 3.3.1 characters 表（角色档案）

```sql
CREATE TABLE characters (
    id UUID NOT NULL DEFAULT uuidv7(),
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(100),
    age INTEGER,
    gender VARCHAR(20),
    personality TEXT NOT NULL,           -- JSON: 大五人格 / MBTI
    background TEXT,                     -- 背景故事
    appearance TEXT,                     -- 外貌描述
    initial_scene VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (name)
);

-- updated_at 触发器（update_updated_at() 函数在 0002 中创建）
CREATE TRIGGER trg_characters_updated_at
    BEFORE UPDATE ON characters
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX idx_characters_active ON characters (is_active);
CREATE INDEX idx_characters_name_trgm ON characters USING gin (name gin_trgm_ops);
```

#### 3.3.2 character_states 表（角色状态镜像，真相源在 Redis）

```sql
CREATE TABLE character_states (
    character_id UUID NOT NULL,
    location VARCHAR(50) NOT NULL DEFAULT 'home',
    stamina INTEGER NOT NULL DEFAULT 80,         -- 体力 0-100
    satiety INTEGER NOT NULL DEFAULT 80,         -- 饱腹度 0-100
    mood VARCHAR(20) NOT NULL DEFAULT 'neutral', -- 情绪
    money INTEGER NOT NULL DEFAULT 1000,         -- 金钱
    phone_battery INTEGER NOT NULL DEFAULT 100,  -- 手机电量 0-100
    social_energy INTEGER NOT NULL DEFAULT 80,   -- 社交能量 0-100
    inventory JSONB NOT NULL DEFAULT '{}'::jsonb, -- 库存
    version INTEGER NOT NULL DEFAULT 1,          -- 乐观锁版本号
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cs_character_fk FOREIGN KEY (character_id)
        REFERENCES characters(id) ON DELETE CASCADE,
    CONSTRAINT cs_stamina_check CHECK (stamina BETWEEN 0 AND 100),
    CONSTRAINT cs_satiety_check CHECK (satiety BETWEEN 0 AND 100),
    CONSTRAINT cs_phone_battery_check CHECK (phone_battery BETWEEN 0 AND 100),
    CONSTRAINT cs_social_energy_check CHECK (social_energy BETWEEN 0 AND 100),
    PRIMARY KEY (character_id)
) WITH (fillfactor = 85);

-- fillfactor=85：保留 15% 空间给 HOT 更新，减少页分裂
-- 适用于频繁更新的状态表

-- 乐观锁：UPDATE ... WHERE character_id = ? AND version = ?
-- 更新成功后 version = version + 1

-- 自定义 autovacuum 配置（防止 bloat）
ALTER TABLE character_states SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

-- updated_at 触发器
CREATE TRIGGER trg_character_states_updated_at
    BEFORE UPDATE ON character_states
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

#### 3.3.3 character_state_history 表（状态历史快照，0007 新增）

```sql
-- 按月 RANGE 分区，每次角色状态更新写入一条快照
CREATE TABLE character_state_history (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL,
    location VARCHAR(50),
    stamina INTEGER NOT NULL,
    satiety INTEGER NOT NULL,
    mood VARCHAR(20),
    money INTEGER NOT NULL,
    phone_battery INTEGER NOT NULL,
    social_energy INTEGER NOT NULL,
    action_id VARCHAR(100),                 -- 触发状态变更的 Action ID
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT csh_character_fk FOREIGN KEY (character_id)
        REFERENCES characters(id) ON DELETE CASCADE
) PARTITION BY RANGE (recorded_at);

-- 默认分区
CREATE TABLE character_state_history_default
    PARTITION OF character_state_history DEFAULT;

-- 索引（在主表创建，传播到所有分区）
CREATE INDEX idx_csh_char_time
    ON character_state_history (character_id, recorded_at);
```

**用途**：原 `/api/v1/characters/{id}/state-history` 端点查询 `character_states` 表（仅 1 行/角色），导致前端状态趋势图只有一个点。新增历史快照表后，支持完整趋势曲线。

**分区预创建**：`pre_create_partitions(3)` 函数会为 `action_records` 和 `character_state_history` 预创建未来 3 个月的分区。

#### 3.3.4 action_records 表（行为历史，按月 RANGE 分区）

```sql
CREATE TABLE action_records (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL,
    tick_id BIGINT NOT NULL,                -- 关联 world_ticks.id
    action_id VARCHAR(100) NOT NULL,
    action_name VARCHAR(200) NOT NULL,
    action_category VARCHAR(50),
    scene VARCHAR(50),
    status VARCHAR(20) NOT NULL DEFAULT 'success', -- success / failed
    reason TEXT,
    state_before JSONB NOT NULL,
    state_after JSONB NOT NULL,
    extra_data JSONB,                       -- 扩展数据（如 share_type='proactive'）
    duration_seconds FLOAT,
    cost_usd NUMERIC(10, 6),
    tokens_used INTEGER,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ar_character_fk FOREIGN KEY (character_id)
        REFERENCES characters(id) ON DELETE CASCADE,
    PRIMARY KEY (id, timestamp)             -- 复合主键（分区键必须包含在主键中）
) PARTITION BY RANGE (timestamp);

CREATE TABLE action_records_default
    PARTITION OF action_records DEFAULT;

-- 索引
CREATE INDEX idx_ar_char_time ON action_records (character_id, timestamp DESC);
CREATE INDEX idx_ar_tick ON action_records (tick_id);
CREATE INDEX idx_ar_action ON action_records (action_id);
```

**关键设计**：
- 复合主键 `(id, timestamp)`：分区表要求分区键必须在主键中
- 按月 RANGE 分区：便于按时间归档与清理
- `extra_data` JSONB：存储扩展数据，如 `{"share_type": "proactive"}` 标记主动分享
- `cost_usd` 使用 `NUMERIC(10, 6)`：精确到小数点后 6 位（LLM 调用费用粒度）

#### 3.3.5 memory_episodes 表（记忆事实，HASH 16 分区 + pgvector）

```sql
-- 0001 初始为普通表，0002 重建为 HASH 16 分区，0005 改 embedding 为 halfvec(2048)
CREATE TABLE memory_episodes (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL,
    episode_type VARCHAR(50) NOT NULL,      -- action / conversation / observation / reflection
    content TEXT NOT NULL,                  -- 记忆内容（自然语言）
    importance INTEGER NOT NULL DEFAULT 5,  -- 重要性 1-10
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    location VARCHAR(50),
    related_characters JSONB,               -- 相关角色 ID 列表
    emotional_valence FLOAT,                -- 情绪效价 -1.0 ~ 1.0
    tags JSONB,                             -- 标签
    source_type VARCHAR(50),                -- 来源类型
    is_reflected BOOLEAN NOT NULL DEFAULT FALSE, -- 是否已被反思
    embedding halfvec(2048),                -- 向量（halfvec 半精度，支持 4000 维）
    fail_count INTEGER NOT NULL DEFAULT 0,  -- 向量化失败次数（0003 新增）
    last_error TEXT,                        -- 最近一次错误（0003 新增）
    next_retry_at TIMESTAMPTZ,              -- 下次重试时间（指数退避，0004 新增）
    CONSTRAINT me_character_fk FOREIGN KEY (character_id)
        REFERENCES characters(id) ON DELETE CASCADE,
    PRIMARY KEY (id, character_id)          -- 复合主键（HASH 分区键必须在主键中）
) PARTITION BY HASH (character_id);

-- 16 个 HASH 分区
CREATE TABLE memory_episodes_p0 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 0);
CREATE TABLE memory_episodes_p1 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 1);
-- ... p2 ~ p15 ...

-- HNSW 索引（halfvec_cosine_ops，0005 重建）
CREATE INDEX idx_mem_embedding_hnsw
    ON memory_episodes USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- 时间线索引（在每个分区上创建）
CREATE INDEX idx_mem_char_time ON memory_episodes (character_id, timestamp DESC);
CREATE INDEX idx_mem_importance ON memory_episodes (character_id, importance DESC);

-- 模糊检索索引
CREATE INDEX idx_mem_content_trgm ON memory_episodes USING gin (content gin_trgm_ops);
```

**关键设计**：
- **HASH 16 分区**：按 `character_id` 哈希分散到 16 个分区，避免单角色记忆集中在一个分区
- **HASH 分区固定**：分区数固定为 16，扩展需要全表重分布（设计文档需明确声明）
- **halfvec(2048)**：pgvector 半精度浮点向量，支持最多 4000 维，存储效率是 vector 的 2 倍
- **HNSW 索引**：`m=16`（每层连接数）、`ef_construction=128`（构建时搜索宽度），平衡召回率与构建速度
- **fail_count + next_retry_at**：向量化失败指数退避机制，`fail_count >= 5` 熔断（EmbeddingWorker 不再处理）
- **复合外键**：`reflection_sources` 通过 `(memory_id, memory_character_id)` 引用 `memory_episodes(id, character_id)`

#### 3.3.6 reflections + reflection_sources 表（反思）

```sql
CREATE TABLE reflections (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL,
    content TEXT NOT NULL,                  -- 反思内容（高层认知）
    insights JSONB NOT NULL,                -- 3 条洞察数组
    importance INTEGER NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ref_character_fk FOREIGN KEY (character_id)
        REFERENCES characters(id) ON DELETE CASCADE,
    PRIMARY KEY (id)
);

-- 反思来源中间表（复合外键，0002 新增）
CREATE TABLE reflection_sources (
    id UUID NOT NULL DEFAULT uuidv7(),
    reflection_id UUID NOT NULL,
    memory_id UUID NOT NULL,
    memory_character_id UUID NOT NULL,      -- 必须与 memory_episodes.character_id 一致
    CONSTRAINT rs_reflection_fk FOREIGN KEY (reflection_id)
        REFERENCES reflections(id) ON DELETE CASCADE,
    -- 复合外键：引用 memory_episodes 的 (id, character_id)
    CONSTRAINT rs_memory_fk FOREIGN KEY (memory_id, memory_character_id)
        REFERENCES memory_episodes(id, character_id) ON DELETE CASCADE,
    PRIMARY KEY (id)
);

CREATE INDEX idx_rs_reflection ON reflection_sources (reflection_id);
CREATE INDEX idx_rs_memory ON reflection_sources (memory_id);
```

**关键设计**：
- `reflections.insights` 是 JSONB 数组，存储 3 条高层认知（一次反思生成 1 条 reflection 记录 + 3 条 insight）
- `reflection_sources` 通过**复合外键** `(memory_id, memory_character_id)` 引用 `memory_episodes(id, character_id)`，确保引用完整性
- `ON DELETE CASCADE`：删除 memory_episode 时级联删除引用它的 reflection_sources

#### 3.3.7 character_diaries 表（角色日记，0008 新增）

```sql
CREATE TABLE character_diaries (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    period VARCHAR(20) NOT NULL,            -- day / week / month / year
    diary_date TIMESTAMPTZ NOT NULL,        -- 日记日期
    diary_end_date TIMESTAMPTZ,             -- 周期结束日期（day 类型为空，其他为周期起始）
    title VARCHAR(200),
    content TEXT NOT NULL,                  -- 叙事性正文
    mood VARCHAR(50),                       -- 日记时的情绪
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE INDEX idx_diary_char_date ON character_diaries (character_id, diary_date);
CREATE INDEX idx_diary_char_period ON character_diaries (character_id, period, diary_date);
```

**用途**：基于 `memory_episodes` 生成的叙事归档层，不替代 Episode 真相源。支持 day/week/month/year 四种周期生成日记。

#### 3.3.8 person_memories 表（角色对用户的记忆，0008 新增）

```sql
CREATE TABLE person_memories (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    user_id VARCHAR(100) NOT NULL,          -- 用户标识（如 qq_123456）
    platform VARCHAR(20) NOT NULL DEFAULT 'web', -- web / qq / lark / internal
    content TEXT NOT NULL,                  -- 记忆内容（自然语言描述）
    summary TEXT,                           -- 压缩摘要
    heat INTEGER NOT NULL DEFAULT 0,        -- 热度（交互次数）
    last_interaction_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    preferences JSONB,                      -- 用户偏好（结构化）
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);

-- 角色 + 用户唯一约束
CREATE UNIQUE INDEX idx_pmem_char_user ON person_memories (character_id, user_id);
CREATE INDEX idx_pmem_heat ON person_memories (character_id, heat);
```

**用途**：记录角色对每个用户的长期认知（偏好、关系进展、共同话题），每次交互后更新，影响后续对话上下文。与 `conversation.context`（会话级短期摘要）互补。

#### 3.3.9 conversations + messages 表（消息系统，0003 新增 / 0004 优化）

```sql
CREATE TABLE conversations (
    id UUID NOT NULL DEFAULT uuidv7(),
    user_id VARCHAR(100) NOT NULL,
    platform VARCHAR(20) NOT NULL DEFAULT 'web', -- web / qq / lark / internal
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 会话上下文（短期摘要）
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- 0004: 扩展为三元组唯一键（含 platform）
    UNIQUE (user_id, platform, character_id),
    -- 0004: platform CHECK 约束
    CONSTRAINT conversations_platform_check
        CHECK (platform IN ('web', 'qq', 'lark', 'internal'))
);

CREATE TRIGGER trg_conversations_updated_at
    BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX idx_conv_user_char ON conversations (user_id, character_id);

CREATE TABLE messages (
    id UUID NOT NULL DEFAULT uuidv7(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender VARCHAR(20) NOT NULL,            -- user / character / system
    content TEXT NOT NULL,
    tokens INTEGER,                         -- 本次消息消耗的 token
    cost NUMERIC(10, 6),                    -- 费用 USD（精度 6 位）
    metadata JSONB,                         -- 额外元数据
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- 0004: sender CHECK 约束
    CONSTRAINT messages_sender_check
        CHECK (sender IN ('user', 'character', 'system'))
);

CREATE INDEX idx_messages_conv_time ON messages (conversation_id, created_at);
CREATE INDEX idx_messages_created_at ON messages (created_at);
```

**关键设计**：
- `conversations` 唯一键为 `(user_id, platform, character_id)`：同一用户在不同平台（web/qq）与同一角色的对话是独立会话
- `messages` 非分区表：消息量预期可控，避免分区带来的 JOIN 复杂度
- `cost` 使用 `NUMERIC(10, 6)`：精确到小数点后 6 位
- `tokens` 和 `cost` 可为 NULL：系统消息不产生费用

#### 3.3.10 world_snapshots + world_events + world_ticks 表（世界状态）

```sql
-- 世界 Tick 记录
CREATE TABLE world_ticks (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    virtual_time TIMESTAMPTZ NOT NULL,      -- 虚拟世界时间
    duration_seconds FLOAT,                 -- Tick 执行耗时
    status VARCHAR(20) NOT NULL DEFAULT 'success'
);

-- 完整快照（每 1000 Tick 一次，冷启动恢复）
CREATE TABLE world_snapshots (
    id UUID NOT NULL DEFAULT uuidv7(),
    tick_id BIGINT NOT NULL REFERENCES world_ticks(id),
    state JSONB NOT NULL,                   -- 完整世界状态
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE INDEX idx_ws_tick ON world_snapshots (tick_id);

-- 差分事件（每 10 Tick 持久化一次，前端事件时间线）
CREATE TABLE world_events (
    id UUID NOT NULL DEFAULT uuidv7(),
    tick_id BIGINT NOT NULL REFERENCES world_ticks(id),
    event_type VARCHAR(50) NOT NULL,        -- weather_change / time_advance / character_action / ...
    event_key VARCHAR(100),                 -- 0006 新增：事件唯一键（如 character_id 或 weather）
    payload JSONB NOT NULL,                 -- 事件详情
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- 0006: UNIQUE 约束调整为 (tick_id, event_type, event_key)
    -- 保证幂等性：同一 Tick 同一类型同一 key 的事件只写入一次
    UNIQUE (tick_id, event_type, event_key)
);

CREATE INDEX idx_we_tick ON world_events (tick_id);
CREATE INDEX idx_we_time ON world_events (timestamp DESC);
```

**关键设计**：
- **事件溯源模式**：`world_events` 记录差分事件（每 10 Tick），`world_snapshots` 记录完整快照（每 1000 Tick）
- **幂等保证**：`UNIQUE(tick_id, event_type, event_key)` 确保重复 Tick 不会产生重复事件
- **应用层去重**：除数据库约束外，应用层实现状态变化检测，只有状态变化才写入事件（防止事件风暴）
- **冷启动恢复**：读取最近的 `world_snapshots` + 重放后续 `world_events` 重建状态

#### 3.3.11 plans + relations 表（计划与关系）

```sql
CREATE TABLE plans (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    priority INTEGER NOT NULL DEFAULT 5,
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending / in_progress / completed / failed
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE TRIGGER trg_plans_updated_at
    BEFORE UPDATE ON plans
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX idx_plans_char_status ON plans (character_id, status);

CREATE TABLE relations (
    id UUID NOT NULL DEFAULT uuidv7(),
    character_id UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    target_type VARCHAR(20) NOT NULL,       -- character / user
    target_id VARCHAR(100) NOT NULL,        -- 目标 ID（角色 UUID 或用户标识）
    relation_type VARCHAR(50),              -- friend / family / colleague / acquaintance
    strength INTEGER NOT NULL DEFAULT 50,   -- 关系强度 0-100
    trust INTEGER NOT NULL DEFAULT 50,      -- 信任度 0-100
    intimacy INTEGER NOT NULL DEFAULT 50,   -- 亲密度 0-100
    notes TEXT,
    last_interaction TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (character_id, target_type, target_id)
);

CREATE TRIGGER trg_relations_updated_at
    BEFORE UPDATE ON relations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX idx_relations_char ON relations (character_id);
CREATE INDEX idx_relations_target ON relations (target_type, target_id);
```

### 3.4 分区策略对比

| 表 | 分区类型 | 分区键 | 分区数 | 理由 |
|----|----------|--------|--------|------|
| `action_records` | RANGE | `timestamp` | 按月 | 时间序列数据，便于归档 |
| `memory_episodes` | HASH | `character_id` | 16（固定） | 按角色分散，避免热点 |
| `character_state_history` | RANGE | `recorded_at` | 按月 | 时间序列数据 |

**HASH 分区固定警告**：
- HASH 分区数（16）在创建时固定，后续无法动态扩展
- 扩展分区数需要全表重分布（数据迁移），成本极高
- 设计文档必须明确声明此约束

### 3.5 pre_create_partitions() 函数（0002 创建 / 0007 更新）

```sql
CREATE OR REPLACE FUNCTION pre_create_partitions(months_ahead INT DEFAULT 3)
RETURNS VOID AS $$
DECLARE
    i INT;
    target_month DATE;
    partition_name TEXT;
    start_date TIMESTAMPTZ;
    end_date TIMESTAMPTZ;
BEGIN
    -- action_records 按月分区
    FOR i IN 0..months_ahead LOOP
        target_month := date_trunc('month', CURRENT_TIMESTAMP + (i || ' months')::interval)::date;
        start_date := target_month::timestamptz;
        end_date := (target_month + INTERVAL '1 month')::timestamptz;
        partition_name := 'action_records_' || to_char(target_month, 'YYYY_MM');

        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
            BEGIN
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF action_records FOR VALUES FROM (%L) TO (%L)',
                    partition_name, start_date, end_date
                );
                RAISE NOTICE 'Created partition: %', partition_name;
            EXCEPTION
                -- 必须用具体异常类型，不能用 WHEN OTHERS 掩盖关键错误
                WHEN undefined_table THEN
                    RAISE NOTICE 'Table action_records does not exist, skipping partition %', partition_name;
                WHEN duplicate_table THEN
                    RAISE NOTICE 'Partition already exists: %', partition_name;
            END;
        END IF;
    END LOOP;

    -- character_state_history 按月分区（0007 新增）
    FOR i IN 0..months_ahead LOOP
        -- ... 同上逻辑，partition_name := 'character_state_history_' || ...
    END LOOP;
END;
$$ LANGUAGE plpgsql;
```

**关键设计**：
- 应用启动时自动调用 `SELECT pre_create_partitions(3);` 预创建未来 3 个月分区
- 异常处理使用具体的 `undefined_table` 和 `duplicate_table`，**禁止用 `WHEN OTHERS`** 掩盖关键错误
- `PartitionScheduler` 每月 25 号 03:00 自动执行，解决长期运行（>3 月）漏建分区问题

### 3.6 向量检索 SQL（CTE 模式）

```sql
-- 记忆检索：混合向量相似度 + 重要性 + 时间衰减
WITH ranked_memories AS (
    SELECT
        id,
        character_id,
        content,
        importance,
        timestamp,
        source_type,
        is_reflected,
        1 - (embedding <=> $2::halfvec(2048)) AS sim_score  -- 余弦相似度
    FROM memory_episodes
    WHERE character_id = $1::uuid
      AND embedding IS NOT NULL
    ORDER BY embedding <=> $2::halfvec(2048)  -- HNSW 索引扫描
    LIMIT $3::int * 3                          -- 过采样 3 倍
)
SELECT * FROM ranked_memories
ORDER BY (sim_score * 0.7 + importance / 10.0 * 0.2 + time_decay * 0.1) DESC
LIMIT $3::int;
```

**关键设计**：
- 使用 `<=>` 操作符（cosine distance），HNSW 索引自动启用
- CTE 必须包含所有需要的列：`importance / timestamp / source_type / is_reflected / sim_score`
- 过采样 3 倍后重排序，平衡召回率与精度
- asyncpg Record 对象必须用 dict key 访问（`row["id"]`），不能用属性访问（`row.id` 会报错）

### 3.7 数据库连接池

```python
# src/db/session.py
class Database:
    def __init__(self):
        self.engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,      # 默认 20
            max_overflow=settings.db_max_overflow, # 默认 10
            echo=settings.db_echo,
            pool_pre_ping=True,                    # 连接前检查
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
```

**连接池参数**：
- `pool_size=20`：常驻连接数
- `max_overflow=10`：允许临时超出 10 个连接
- `pool_pre_ping=True`：每次借出连接前 ping 一下，避免使用断开连接

---

## 四、缓存设计（Redis）

### 4.1 Redis 部署

```yaml
# docker-compose.yml
redis:
  image: redis:8.0-alpine
  command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
  ports: ["6379:6379"]
```

**关键配置**：
- `--maxmemory 256mb`：最大内存 256MB
- `--maxmemory-policy allkeys-lru`：内存满时 LRU 淘汰任意 key
- `decode_responses=True`：Python 客户端配置，自动解码为 str

### 4.2 Redis Key 全景

| Key 模式 | 类型 | TTL | 用途 |
|----------|------|-----|------|
| `world:state` | Hash | 永久 | 世界实时状态（时间/天气/Tick ID） |
| `world:tick:leader` | String | 30s | World Tick Leader 选举锁 |
| `char:{id}:state` | Hash | 永久 | 角色实时状态（唯一真相源） |
| `char:{id}:lock` | String | 30s | 角色 Tick 分布式锁 |
| `char:{id}:share:cooldown` | String | 1800s | 主动分享冷却标记 |
| `char:{id}:share:daily:{date}` | String | 86400s | 当日分享次数计数 |
| `llm:cost:{YYYY-MM-DD}` | Hash | 48h | 当日 LLM 成本统计（tokens/cost/count） |
| `llm:circuit_breaker` | Hash | 永久 | LLM 熔断器状态（state/failure_count/last_failure_time） |
| `tools:enabled` | Hash | 永久 | 工具启用状态（field=工具全名，value=true/false） |
| `runtime:config` | Hash | 永久 | 运行时配置覆盖 |

### 4.3 世界状态结构（`world:state`）

```text
world:state (Hash)
├── tick_id: "12345"                         # 当前 Tick ID
├── virtual_time: "2026-07-16T10:30:00+08:00" # 虚拟世界时间
├── weather: "sunny"                          # 天气
├── temperature: "25"                         # 温度
├── last_tick_at: "2026-07-16T10:30:00Z"      # 现实上次 Tick 时间
└── updated_at: "2026-07-16T10:30:00Z"        # 状态更新时间
```

**虚拟时间推进规则**：
- 每个 World Tick 推进 `world_tick_minutes`（默认 10 分钟）虚拟时间
- 现实 30 秒 = 虚拟 10 分钟（1:20 比例）
- LLM 必须严格遵循虚拟时间，不能臆造日期/天气

### 4.4 角色状态结构（`char:{id}:state`）

```text
char:uuid:state (Hash)
├── location: "cafe"              # 当前场景
├── stamina: "80"                 # 体力 0-100
├── satiety: "75"                 # 饱腹度 0-100
├── mood: "happy"                 # 情绪
├── money: "950"                  # 金钱
├── phone_battery: "85"           # 手机电量 0-100
├── social_energy: "70"           # 社交能量 0-100
├── inventory: '{"coffee": 1}'    # 库存（JSON 字符串）
├── version: "5"                  # 乐观锁版本号
└── updated_at: "2026-07-16T..."
```

**资源恢复规则**：
- `phone_battery`：**只能**通过 `charge_phone` action 恢复（+50），无被动恢复
- `social_energy`：仅在 rest 类独处 action（relax/sleep/read_book）时被动恢复 +10/tick
- `stamina` / `satiety`：通过相应 action 恢复

### 4.5 Leader 选举锁（World Tick）

```python
# src/core/world/engine.py
LOCK_KEY = "world:tick:leader"
LOCK_TTL = 30  # 锁 TTL 30 秒
LOCK_RENEW_INTERVAL = 10  # 每 10 秒续期

async def _try_acquire_leader(self) -> bool:
    # SET key value NX EX ttl
    acquired = await self.redis.set(
        LOCK_KEY, self.instance_id, nx=True, ex=LOCK_TTL
    )
    return bool(acquired)

async def _renew_leader(self) -> bool:
    # Lua 脚本：仅当持有者是自己才续期
    script = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    else
        return 0
    end
    """
    return bool(await self.redis.eval(script, 1, LOCK_KEY, self.instance_id, LOCK_TTL))
```

**设计要点**：
- 使用 `SET NX EX` 原子获取锁
- 使用 Lua 脚本续期，避免误续他人的锁
- 锁 TTL 30s，续期间隔 10s，留 20s 缓冲
- 单实例运行保证世界时间不会跳跃

### 4.6 角色 Tick 锁

```python
# src/core/character/tick.py
async def _process_character(self, character_id: str) -> None:
    lock_key = f"char:{character_id}:lock"
    # 获取锁（SET NX EX）
    acquired = await self.redis.set(lock_key, "1", nx=True, ex=settings.character_lock_ttl_seconds)
    if not acquired:
        return  # 其他实例正在处理
    try:
        await self._execute_tick(character_id)
    finally:
        await self.redis.delete(lock_key)
```

### 4.7 运行时配置覆盖（`runtime:config`）

```python
# src/config_runtime.py
async def load_runtime_config(redis: Redis) -> RuntimeConfig:
    """从 Redis 加载运行时配置覆盖（Pydantic 校验后覆盖 settings）"""
    raw = await redis.hgetall("runtime:config")
    if not raw:
        return RuntimeConfig()  # 默认值
    return RuntimeConfig.model_validate({k: json.loads(v) for k, v in raw.items()})
```

**设计要点**：
- 启动时从 Redis 读取配置覆盖，Pydantic 校验后覆盖 `settings` 对象
- 支持运行时动态调整参数（如 `world_tick_seconds`），无需重启
- 类型校验 + 范围检查，避免错误配置导致系统异常

---

## 五、核心循环详解

### 5.1 World Tick（世界推进循环）

**位置**：`src/core/world/engine.py` - `WorldEngine` 类

**执行频率**：每 `world_tick_seconds`（默认 30s）一次

**并发控制**：Redis 分布式锁，**全局单实例**运行

#### 5.1.1 执行流程（7 步）

```text
┌─────────────────────────────────────────────────────────────┐
│  WorldEngine.tick()  执行流程                                │
├─────────────────────────────────────────────────────────────┤
│  1. Leader 选举                                              │
│     ├── SET world:tick:leader NX EX 30                      │
│     └── 失败则跳过本次 Tick（其他实例在运行）                 │
│                                                              │
│  2. 读取当前世界状态                                          │
│     ├── 从 Redis world:state 读取                            │
│     └── 不存在则从 PG world_snapshots 冷启动恢复             │
│                                                              │
│  3. 执行演化器链（EvolutionChain）                           │
│     ├── TimeEvolver: 推进虚拟时间 +world_tick_minutes       │
│     ├── WeatherEvolver: 按 weather_interval 变化天气        │
│     └── EventEvolver: 触发节日/事件                          │
│     ※ 单个演化器失败不中断整个 Tick                          │
│                                                              │
│  4. 持久化到 Redis                                           │
│     └── HSET world:state ...（更新时间/天气/Tick ID）        │
│                                                              │
│  5. 差分事件持久化                                            │
│     ├── 每 world_snapshot_interval（10）Tick 写入 world_events│
│     └── 应用层去重：状态变化才写入                            │
│                                                              │
│  6. 完整快照持久化                                            │
│     ├── 每 world_full_snapshot_interval（1000）Tick 写一次   │
│     └── INSERT INTO world_snapshots (state) VALUES (...)    │
│                                                              │
│  7. 指标埋点                                                  │
│     ├── WORLD_TICK_DURATION.observe(duration)               │
│     ├── WORLD_TICK_TOTAL.inc()                              │
│     ├── WORLD_TICK_ID.set(tick_id)                          │
│     └── 失败时 WORLD_TICK_ERRORS.inc()                      │
└─────────────────────────────────────────────────────────────┘
```

#### 5.1.2 冷启动恢复

```python
async def _cold_start(self) -> dict:
    """从 PG 恢复世界状态"""
    # 1. 读取最近的完整快照
    snapshot = await self.snapshot_repo.get_latest()
    if snapshot:
        state = snapshot.state
        # 2. 重放后续差分事件
        events = await self.event_repo.get_after(snapshot.tick_id)
        for event in events:
            self._apply_event(state, event)
        return state
    # 3. 无快照则使用默认初始状态
    return self._default_state()
```

### 5.2 Character Tick（角色行为闭环）

**位置**：`src/core/character/tick.py` - `CharacterTickEngine` 类

**执行频率**：每 `character_tick_seconds`（默认 30s）一次

**并发控制**：
- `SEMAPHORE`（信号量）限制并发：`character_max_concurrent=10`
- 每个角色独立锁：`char:{id}:lock` TTL 30s

#### 5.2.1 五阶段闭环

```text
┌─────────────────────────────────────────────────────────────┐
│  CharacterTickEngine._execute_tick(character_id)            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  阶段 1: 感知（Perception）                                  │
│  ├── 读取角色状态（Redis char:{id}:state）                   │
│  ├── 读取世界状态（Redis world:state）                       │
│  ├── 检索相关记忆（memory_episodes 向量检索）                │
│  └── 获取候选 Action（precondition 过滤）                    │
│                                                              │
│  阶段 2: 决策（Decision，ReAct 模式）                       │
│  ├── LLM 决策：在候选 Action 中选择                          │
│  ├── 若 LLM 想调用工具：执行工具 → 结果回灌 → 再次决策       │
│  ├── ReAct 循环最多 3 轮                                    │
│  ├── 超过 3 轮强制改 wait（防止死循环）                      │
│  └── 输出：DecisionResult(action_id, reason, params, ...)   │
│                                                              │
│  阶段 3: 执行（Execute）                                    │
│  ├── ActionRegistry.execute(action_id, params, state)       │
│  ├── Action executor 返回 new_state（不直接写状态）          │
│  ├── _apply_tool_deltas: 应用工具产生的 deltas               │
│  │   ├── money_delta（金钱变化）                             │
│  │   ├── inventory_delta（库存变化）                         │
│  │   ├── mood_delta（情绪变化）                              │
│  │   └── relation_strength_delta（关系强度变化）             │
│  ├── 写 PG: action_records（事务内）                         │
│  ├── 写 PG: character_states（镜像，事务内）                 │
│  ├── 写 PG: character_state_history（快照，事务内）          │
│  └── 事务提交后写 Redis: char:{id}:state                     │
│                                                              │
│  阶段 4: 记忆（Memorize）                                   │
│  ├── 生成记忆内容（Action 内容 + 结果）                      │
│  ├── INSERT INTO memory_episodes (content, ...)             │
│  ├── embedding 字段暂为 NULL                                 │
│  └── EmbeddingWorker 异步向量化（不阻塞 Tick）               │
│                                                              │
│  阶段 5: 反思（Reflect）                                    │
│  ├── 检查未反思记忆数量                                      │
│  ├── 达到 REFLECTION_THRESHOLD=20 触发反思                   │
│  ├── LLM 归纳 3 条高层认知                                   │
│  ├── INSERT INTO reflections (content, insights)            │
│  ├── INSERT INTO reflection_sources（关联记忆）              │
│  └── UPDATE memory_episodes SET is_reflected=TRUE           │
│                                                              │
│  阶段 5.5: 主动分享（Proactive Share）                      │
│  ├── 检查 decision.proactive_share_intent（boolean）         │
│  ├── 检查冷却时间（share_cooldown_seconds=1800）             │
│  ├── 检查日限额（share_daily_limit=8）                       │
│  └── 调用 _maybe_proactive_share()                          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 5.2.2 ReAct 决策循环详解

```python
# 伪代码
async def _decide(self, character, context, candidates, max_rounds=3):
    messages = self._build_prompt(character, context, candidates)
    for round_num in range(max_rounds):
        result = await self.llm.structured_output(
            model=settings.model_chat,
            messages=messages,
            schema=DecisionResult
        )
        # 如果 LLM 想调用工具
        if result.tool_call and round_num < max_rounds - 1:
            tool_result = await self.tool_registry.call_tool_by_full_name(
                result.tool_call.name,
                result.tool_call.args,
                context={"character_id": character.id, ...}
            )
            # 工具结果回灌到 messages
            messages.append({"role": "tool", "content": json.dumps(tool_result)})
            continue
        # 返回最终决策
        return result
    # 超过 3 轮强制改 wait
    return DecisionResult(action_id="wait", reason="达到 ReAct 最大轮次")
```

**关键设计**：
- ReAct 模式：LLM 决策→执行工具→结果加入观察→再次决策
- 最多 3 轮，防止死循环
- 超过 3 轮强制改 `wait` action
- 工具调用结果作为观察（observation）回灌到上下文

#### 5.2.3 跨角色聊天处理（`_handle_character_chat`）

当角色 A 决定与角色 B 聊天时：

```python
async def _handle_character_chat(self, char_a, char_b, message):
    # 1. 获取跨角色资源锁（按 ID 排序避免死锁）
    lock_a, lock_b = sorted([char_a.id, char_b.id])
    async with self._acquire_locks([lock_a, lock_b]):
        # 2. 角色B 生成回复
        reply = await self.llm.chat(
            model=settings.model_chat,
            messages=self._build_chat_prompt(char_b, char_a, message)
        )
        # 3. 双方关系更新
        await self.relation_repo.update_strength(char_a.id, char_b.id, +1)
        await self.relation_repo.update_strength(char_b.id, char_a.id, +1)
        # 4. 双方各写入记忆
        await self.memory_repo.create(char_a.id, {
            "episode_type": "conversation",
            "content": f"我对 {char_b.name} 说：{message}"
        })
        await self.memory_repo.create(char_b.id, {
            "episode_type": "conversation",
            "content": f"{char_a.name} 对我说：{message}，我回复：{reply}"
        })
```

#### 5.2.4 DecisionResult 结构

```python
class DecisionResult(BaseModel):
    action_id: str                          # 选择的 Action ID
    reason: str                             # 决策理由
    params: dict = {}                       # Action 参数（'or {}' 防 None）
    tool_call: ToolCall | None = None       # 工具调用（ReAct）
    proactive_share_intent: bool = False    # 主动分享意图（boolean，非嵌套对象）
    plan_changes: list[dict] = []           # 计划变更
```

**关键处理**：
- `proactive_share_intent`：boolean 类型，LLM 返回 None 时显式转 `False`
- `params`：使用 `'or {}'` 防 None
- `plan_changes`：LLM 返回字符串时包装为 `{"description": str(pc)}`

### 5.3 EmbeddingWorker（异步向量化）

**位置**：`src/memory/embedding_worker.py`

**职责**：轮询 `memory_episodes` 中 `embedding IS NULL` 的记录，调用 embedding API 向量化。

#### 5.3.1 执行参数

```python
class EmbeddingWorker:
    def __init__(self, session_factory, llm_client, batch_size=20, poll_interval=5.0):
        self.batch_size = batch_size        # 每批 20 条
        self.poll_interval = poll_interval  # 轮询间隔 5 秒
```

#### 5.3.2 并发安全（FOR UPDATE SKIP LOCKED）

```sql
-- 多实例 EmbeddingWorker 并发安全的批量获取
SELECT id, character_id, content
FROM memory_episodes
WHERE embedding IS NULL
  AND (next_retry_at IS NULL OR next_retry_at <= now())  -- 未到重试时间的不取
  AND fail_count < 5                                       -- 熔断的不取
ORDER BY timestamp ASC
LIMIT 20
FOR UPDATE SKIP LOCKED;  -- 跳过已被其他实例锁定的行
```

#### 5.3.3 失败处理（指数退避 + 熔断）

```python
async def _process_batch(self, records):
    for record in records:
        try:
            embedding = await self.llm_client.embed(record.content)
            await self.repo.update_embedding(record.id, embedding)
        except Exception as e:
            # 指数退避：fail_count 越大，下次重试时间越晚
            fail_count = record.fail_count + 1
            retry_delay = 2 ** fail_count  # 2, 4, 8, 16, 32 秒
            next_retry_at = datetime.now() + timedelta(seconds=retry_delay)
            await self.repo.update_failure(record.id, fail_count, str(e), next_retry_at)
            # fail_count >= 5 熔断：不再处理
```

---

## 六、Agent 能力层

### 6.1 Action 系统

**位置**：`src/actions/`

#### 6.1.1 Action 接口约定

| 约定 | 说明 |
|------|------|
| **Action 必须有 precondition** | `(state: dict) -> bool`，由代码过滤候选，LLM 不能绕过 |
| **Action executor 不直接写状态** | 返回 `new_state` 字典，由执行层统一写入 |
| **LLM 不直接修改状态** | LLM 只能在候选 Action 中选择 |
| **Action 按场景组织** | `scene + activity` 绑定，不在咖啡店不能执行咖啡店专属 Action |
| **资源字段符号约定** | `energy_cost` 正=恢复，负=消耗；`money_cost` 正=花费 |

#### 6.1.2 Action 注册

```python
# src/actions/__init__.py
def register_all(registry: ActionRegistry) -> None:
    registry.register(SleepAction())
    registry.register(EatAction())
    registry.register(WorkAction())
    registry.register(RelaxAction())
    registry.register(ReadBookAction())
    registry.register(ChargePhoneAction())
    registry.register(ChatWithCharacterAction())
    # ... 更多 Action
```

#### 6.1.3 Action 执行流程

```python
class ActionRegistry:
    def execute(self, action_id: str, params: dict, state: dict) -> dict:
        action = self._actions[action_id]
        # 1. 再次校验 precondition（防止 LLM 绕过）
        if not action.precondition(state):
            raise ActionPreconditionFailed(action_id)
        # 2. 执行 executor（返回 new_state，不直接写）
        new_state = action.executor(params, state)
        # 3. 埋点
        ACTION_EXECUTION_TOTAL.labels(action_id=action_id, status="success").inc()
        return new_state
```

### 6.2 记忆系统

#### 6.2.1 记忆生成

**位置**：`src/memory/`

```python
# 记忆生成流程
async def create_memory(character_id, episode_type, content, **kwargs):
    # 1. 显式检查 character_id 存在（防止孤儿数据）
    exists = await session.execute(
        text("SELECT EXISTS(SELECT 1 FROM characters WHERE id = :id)"),
        {"id": character_id}
    )
    if not exists.scalar():
        raise CharacterNotFound(character_id)

    # 2. 可选：LLM 评分重要性（MEMORY_LLM_SCORING_ENABLED 默认 False）
    importance = 5  # 默认
    if settings.memory_llm_scoring_enabled:
        importance = await self._llm_score_importance(content, kwargs)

    # 3. 写入 memory_episodes（embedding 暂为 NULL）
    await session.execute(
        text("""INSERT INTO memory_episodes
                (character_id, episode_type, content, importance, ...)
                VALUES (:cid, :type, :content, :imp, ...)"""),
        {"cid": character_id, "type": episode_type, ...}
    )
    # 4. EmbeddingWorker 异步向量化
```

#### 6.2.2 LLM 重要性评分（可选）

**配置**：`MEMORY_LLM_SCORING_ENABLED`（默认 `False`）

启用时，LLM 基于以下维度评分 1-10：
- 情感强度（emotional intensity）
- 关系影响（relationship impact）
- 稀缺性（scarcity）
- 后续影响（subsequent influence）

#### 6.2.3 记忆检索

```python
async def search_memories(character_id, query, limit=5):
    # 1. 向量化查询
    query_embedding = await self.llm_client.embed(query)

    # 2. 混合检索（向量相似度 + 重要性 + 时间衰减）
    results = await session.execute(
        text("""
            WITH ranked AS (
                SELECT *, 1 - (embedding <=> :q::halfvec(2048)) AS sim_score
                FROM memory_episodes
                WHERE character_id = :cid AND embedding IS NOT NULL
                ORDER BY embedding <=> :q::halfvec(2048)
                LIMIT :lim * 3
            )
            SELECT * FROM ranked
            ORDER BY (sim_score * 0.7 + importance / 10.0 * 0.2 + time_decay * 0.1) DESC
            LIMIT :lim
        """),
        {"cid": character_id, "q": query_embedding, "lim": limit}
    )
    # 注意：asyncpg Record 必须用 dict key 访问
    return [dict(row._mapping) for row in results]
```

### 6.3 反思系统

**位置**：`src/memory/reflection_service.py` - `ReflectionService` 类

**触发条件**：未反思记忆数 ≥ `REFLECTION_THRESHOLD=20`

#### 6.3.1 反思生成流程

```python
async def generate_reflection(self, character_id):
    # 1. 获取未反思记忆
    unreflected = await self.memory_repo.get_unreflected(character_id, limit=20)
    if len(unreflected) < REFLECTION_THRESHOLD:
        return None

    # 2. LLM 归纳（生成 1 条反思，包含 3 条洞察）
    result = await self.llm.structured_output(
        model=settings.model_chat,
        messages=self._build_reflection_prompt(character_id, unreflected),
        schema=ReflectionResult  # {content: str, insights: list[str], importance: int}
    )

    # 3. 写入 1 条 reflections 记录
    reflection = await self.reflection_repo.create(
        character_id=character_id,
        content=result.content,
        insights=result.insights,  # JSONB 数组（3 条）
        importance=result.importance
    )

    # 4. 写入多条 reflection_sources（关联源记忆）
    for memory in unreflected:
        await self.reflection_source_repo.create(
            reflection_id=reflection.id,
            memory_id=memory.id,
            memory_character_id=character_id  # 复合外键要求
        )

    # 5. 标记记忆已反思
    await self.memory_repo.mark_reflected([m.id for m in unreflected])
```

**关键设计**：
- **1 次反思 = 1 条 reflection 记录 + 3 条 insight（JSONB 数组）**
- 不是 3 条独立反思记录
- `reflection_sources` 关联源记忆，通过复合外键保证引用完整性

### 6.4 日记系统

**位置**：`src/memory/diary_service.py` - `DiaryService` 类

**职责**：基于 `memory_episodes` 生成叙事归档（day/week/month/year）

```python
async def generate_diary(self, character_id, period="day"):
    # 1. 确定时间范围
    if period == "day":
        start = today_start
        end = today_end
    elif period == "week":
        start = week_start
        end = week_end
    # ...

    # 2. 查询时间范围内的记忆
    memories = await self.memory_repo.get_by_time_range(character_id, start, end)

    # 3. LLM 生成叙事日记
    diary = await self.llm.structured_output(
        model=settings.model_chat,
        messages=self._build_diary_prompt(character_id, memories, period),
        schema=DiaryResult  # {title, content, mood}
    )

    # 4. 写入 character_diaries
    await self.diary_repo.create(
        character_id=character_id,
        period=period,
        diary_date=start,
        diary_end_date=end if period != "day" else None,
        title=diary.title,
        content=diary.content,
        mood=diary.mood
    )
```

**调度**：`DiaryScheduler` 后台任务，每日 23:00 自动生成当日日记。

---

## 七、本地工具系统（ToolRegistry + ReAct）

### 7.1 架构演进

| 阶段 | 架构 | 调用方式 | 问题 |
|------|------|----------|------|
| Phase 1 | 独立 MCP Server | HTTP/SSE | 网络开销大、部署复杂 |
| **Phase 2** | **本地工具 `src/tools/`** | **进程内 async 函数** | **消除网络开销** |

### 7.2 工具注册表

**位置**：`src/tools/registry.py`

```python
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    # 每个工具定义：
    #   func: 异步函数引用
    #   description: LLM Prompt 中展示的功能描述
    #   llm_params: LLM 可填写的参数（名称 -> 中文说明）
    #   injected_params: 需从角色状态自动注入的参数（工具参数名 -> 状态字段名）
    #   state_mutating: 是否会产生状态 deltas
}
```

### 7.3 16 个工具完整列表（5 命名空间）

#### 7.3.1 商店工具（shop，5 个）

| 工具全名 | 描述 | LLM 参数 | 注入参数 | 状态变更 |
|----------|------|----------|----------|----------|
| `shop.list_items` | 查看商店商品列表 | `category` | - | 否 |
| `shop.get_item_details` | 查询单个商品详情 | `item_id` | - | 否 |
| `shop.buy_item` | 购买商品 | `item_id`, `quantity` | `current_money`, `current_inventory` | 是（money_delta, inventory_delta） |
| `shop.sell_item` | 出售商品 | `item_id`, `quantity` | `current_money`, `current_inventory` | 是（money_delta, inventory_delta） |
| `shop.get_shop_categories` | 列出商品分类 | - | - | 否 |

#### 7.3.2 知识库工具（knowledge，2 个）

| 工具全名 | 描述 | LLM 参数 | 注入参数 | 状态变更 |
|----------|------|----------|----------|----------|
| `knowledge.query_kb` | 查询小镇设定库 | `query`, `category`, `limit` | - | 否 |
| `knowledge.list_categories` | 列出知识库类别 | - | - | 否 |

#### 7.3.3 社交工具（social，2 个）

| 工具全名 | 描述 | LLM 参数 | 注入参数 | 状态变更 |
|----------|------|----------|----------|----------|
| `social.give_gift` | 给其他角色送礼 | `target_id`, `item_id`, `quantity` | `current_inventory`, `_character_id`, `_relation_strength_with_target` | 是（inventory_delta, relation_strength_delta） |
| `social.check_relation` | 查询与某角色关系 | `target_id` | `_character_id` | 否 |

#### 7.3.4 世界工具（world，3 个）

| 工具全名 | 描述 | LLM 参数 | 注入参数 | 状态变更 |
|----------|------|----------|----------|----------|
| `world.get_time` | 获取当前虚拟时间 | - | - | 否 |
| `world.get_weather` | 获取当前天气 | - | - | 否 |
| `world.get_scene_info` | 获取场景信息 | `scene_id` | - | 否 |

#### 7.3.5 自身信息工具（self_info，4 个）

| 工具全名 | 描述 | LLM 参数 | 注入参数 | 状态变更 |
|----------|------|----------|----------|----------|
| `self_info.get_state` | 获取自身完整状态 | - | `_character_id` | 否 |
| `self_info.search_memories` | 检索自身记忆 | `query`, `limit` | `_character_id` | 否 |
| `self_info.get_inventory` | 获取库存列表 | - | `_character_id` | 否 |
| `self_info.get_relations` | 获取关系列表 | - | `_character_id` | 否 |

### 7.4 参数注入机制

三种注入类型：

```python
# 1. _character_id：自动注入当前角色 ID
"injected_params": {"_character_id": "character_id"}

# 2. _relation_strength_with_target：注入与目标角色的关系强度
"injected_params": {"_relation_strength_with_target": "relation_strength"}

# 3. 常规状态字段：从角色状态注入
"injected_params": {"current_money": "money", "current_inventory": "inventory"}
```

**设计要点**：
- 状态变更类工具需要角色当前状态参数（如 `current_money`），LLM 无法提供
- 由 registry 从调用方传入的 context 自动注入
- 下划线前缀（`_character_id`）表示系统注入参数，不暴露给 LLM

### 7.5 工具启用/禁用

```python
TOOLS_ENABLED_KEY = "tools:enabled"  # Redis Hash

async def is_tool_enabled(self, tool_name: str) -> bool:
    """检查工具是否启用（未配置默认启用，fail-open）"""
    value = await self.redis.hget(TOOLS_ENABLED_KEY, tool_name)
    if value is None:
        return True  # 未配置默认启用
    return value == "true"

async def set_tool_enabled(self, tool_name: str, enabled: bool) -> None:
    await self.redis.hset(TOOLS_ENABLED_KEY, tool_name, "true" if enabled else "false")
```

**设计要点**：
- 未配置时默认全部启用（fail-open，避免工具被误禁用导致功能缺失）
- 通过 Redis Hash 存储，支持运行时动态调整

### 7.6 工具调用流程

```python
async def call_tool_by_full_name(self, full_name, args, context):
    # 1. 查找工具
    tool = TOOL_REGISTRY.get(full_name)
    if tool is None:
        raise ToolNotFound(full_name)

    # 2. 检查启用状态
    if not await self.is_tool_enabled(full_name):
        raise ToolDisabled(full_name)

    # 3. 注入参数
    call_args = dict(args)
    for param_name, state_field in tool["injected_params"].items():
        if param_name == "_character_id":
            call_args[param_name] = context["character_id"]
        elif param_name == "_relation_strength_with_target":
            call_args[param_name] = await self._get_relation_strength(
                context["character_id"], args.get("target_id")
            )
        else:
            call_args[param_name] = context["state"].get(state_field)

    # 4. 调用工具函数
    result = await tool["func"](**call_args)

    # 5. 状态变更工具返回 deltas
    if tool["state_mutating"]:
        # deltas 由 CharacterTickEngine._apply_tool_deltas 应用
        pass

    return result
```

### 7.7 工具 Deltas 应用

```python
async def _apply_tool_deltas(self, character_id, tool_result):
    """应用工具产生的状态变更"""
    deltas = tool_result.get("deltas", {})

    if "money_delta" in deltas:
        await self.state_service.update_money(character_id, deltas["money_delta"])
    if "inventory_delta" in deltas:
        await self.state_service.update_inventory(character_id, deltas["inventory_delta"])
    if "mood_delta" in deltas:
        await self.state_service.update_mood(character_id, deltas["mood_delta"])
    if "relation_strength_delta" in deltas:
        await self.relation_service.update_strength(
            character_id,
            tool_result["target_id"],
            deltas["relation_strength_delta"]
        )
```

---

## 八、消息服务层

### 8.1 架构总览

**位置**：`src/messaging/`

```text
┌─────────────────────────────────────────────────────────────┐
│  消息服务层                                                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ WebSocket   │  │ OneBot v11  │  │ 内部 API    │         │
│  │ (Web 客户端) │  │ (QQ 机器人)  │  │ (REST)     │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                │                │                 │
│         └────────────────┼────────────────┘                 │
│                          ▼                                  │
│                 ┌─────────────────┐                         │
│                 │ MessageService  │                         │
│                 │ (统一处理)       │                         │
│                 └────────┬────────┘                         │
│                          │                                  │
│         ┌────────────────┼────────────────┐                 │
│         ▼                ▼                ▼                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ 私聊决策     │  │ 群聊四层决策 │  │ 上下文管理   │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 MessageService

**位置**：`src/messaging/service.py`

#### 8.2.1 核心参数

```python
class MessageService:
    DEFAULT_HISTORY_LIMIT = 20              # 默认历史消息数
    CONTEXT_COMPRESS_THRESHOLD = 50         # 上下文压缩阈值
    GROUP_REPLY_PROBABILITY_CAP = 0.7       # 群聊回复概率上限
```

#### 8.2.2 私聊处理流程

```python
async def handle_private_message(self, user_id, character_id, content, platform="web"):
    # 1. 获取或创建会话
    conversation = await self._get_or_create_conversation(
        user_id, platform, character_id
    )

    # 2. 保存用户消息（PG 事务内）
    async with self.db.session() as session:
        await self.message_repo.create(
            conversation_id=conversation.id,
            sender="user",
            content=content
        )

        # 3. 获取历史消息
        history = await self.message_repo.get_recent(
            conversation.id, limit=self.DEFAULT_HISTORY_LIMIT
        )

        # 4. 上下文压缩（超过阈值时）
        if len(history) > self.CONTEXT_COMPRESS_THRESHOLD:
            await self._compress_context(conversation, history)

        # 5. PromptGuard 消毒用户输入
        safe_content = PromptGuard.wrap_user_message(content)

        # 6. 构建 Prompt（system_template + template）
        messages = await self._build_prompt(
            character_id, conversation, history, safe_content
        )

        # 7. LLM 生成回复
        reply = await self.llm.chat(
            model=settings.model_chat,
            messages=messages
        )

        # 8. 保存角色回复（同一 PG 事务）
        await self.message_repo.create(
            conversation_id=conversation.id,
            sender="character",
            content=reply.content,
            tokens=reply.tokens,
            cost=reply.cost
        )

        await session.commit()

    # 9. 更新 person_memories（用户认知）
    await self._update_person_memory(character_id, user_id, platform, content, reply.content)

    return reply.content
```

**关键设计**：
- 用户消息与角色回复在同一 PG 事务中（保证原子性）
- PromptGuard 消毒后再拼入 Prompt
- 乐观 UI：前端只插入用户消息，角色回复依赖 query invalidation 同步

### 8.3 群聊四层决策

**位置**：`src/messaging/service.py` + `src/adapters/onebot.py`

**配置**：`ONEBOT_GROUP_AT_ONLY=false`（默认开启智能回复）

#### 8.3.1 决策流程

```text
┌─────────────────────────────────────────────────────────────┐
│  群聊消息处理决策（四层）                                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  层 1: @ 检测                                                │
│  ├── to_me == true → 直接回复                                │
│  ├── at 段包含 self_id → 直接回复                            │
│  └── CQ 码 @机器人 → 直接回复                                │
│  （三重检测，确保各种 @ 形式都能识别）                        │
│                                                              │
│  层 2: 关键词检测                                            │
│  ├── 包含角色名/昵称 → 直接回复                              │
│  ├── 包含问候语关键词（你好/早上好/晚安等）→ 直接回复         │
│  └── 否则进入层 3                                            │
│                                                              │
│  层 3: 启发式概率回复                                        │
│  ├── 疑问句（含 ?/？）→ 70% 概率回复                         │
│  ├── 情绪强烈/QQ 表情 → 50% 概率回复                         │
│  └── 其他 → 不回复                                           │
│  （概率受 GROUP_REPLY_PROBABILITY_CAP=0.7 约束）             │
│                                                              │
│  层 4: LLM 判断                                              │
│  ├── LLM 判断"回复" → 直接回复（不受 0.7 cap 约束）         │
│  ├── LLM 判断"不回复" → 15% 概率兜底回复                     │
│  └── LLM 调用错误 → 30% 概率兜底回复                         │
│  （LLM 说回复就直接回复，cap 只在启发式层生效）              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**关键设计**：
- **第 4 层 LLM 判断不受 0.7 cap 约束**：LLM 说回复就直接回复
- **cap 只在启发式层（层 3）生效**：限制随机回复概率
- **兜底机制**：LLM 判断不回复时仍有 15% 概率回复（避免冷场），错误时 30% 概率回复

#### 8.3.2 CQ 码清理

```python
_CQ_CODE_PATTERN = re.compile(r"\[CQ:[^\]]+\]")

def _clean_cq_codes(message: str) -> str:
    """清理所有 CQ 码，避免特殊字符误判"""
    return _CQ_CODE_PATTERN.sub("", message)

# 示例：图片 CQ 码 URL 中的 ? 会被误判为疑问句
# [CQ:image,file=xxx,url=https://example.com/img.png?size=large]
# 清理后变为空字符串，避免误判
```

#### 8.3.3 @ 前缀移除

```python
def _remove_at_prefix(message: str, self_id: str) -> str:
    """移除 @机器人 前缀，保留实际内容"""
    # 匹配 [CQ:at,qq=xxx] 或 @机器人昵称
    pattern = rf"\[CQ:at,qq={self_id}\]|@机器人"
    return re.sub(pattern, "", message).strip()
```

### 8.4 OneBot 适配器

**位置**：`src/adapters/onebot.py` - `OneBotAdapter` 类

**端点**：`/ws/onebot/v12`（反向 WebSocket，NapCat 连接）

#### 8.4.1 核心参数

```python
class OneBotAdapter:
    MAX_SEGMENT_LENGTH = 500          # 单段最大长度
    SEGMENT_SEND_INTERVAL = 0.6       # 段发送间隔（秒）
```

#### 8.4.2 消息分段

```python
def _split_message(self, message: str) -> list[str]:
    """长消息分段发送（模拟真人打字）"""
    segments = []

    # 1. 优先按双换行（段落）拆分
    paragraphs = message.split("\n\n")

    for para in paragraphs:
        if len(para) <= self.MAX_SEGMENT_LENGTH:
            segments.append(para)
        else:
            # 2. 超长段按单换行继续拆
            lines = para.split("\n")
            for line in lines:
                if len(line) <= self.MAX_SEGMENT_LENGTH:
                    segments.append(line)
                else:
                    # 3. 仍超长则硬切分（500 字/段）
                    for i in range(0, len(line), self.MAX_SEGMENT_LENGTH):
                        segments.append(line[i:i + self.MAX_SEGMENT_LENGTH])

    return segments

async def _send_segments(self, segments, target_type, target_id):
    """依次发送多段，间隔 0.6 秒"""
    total = len(segments)
    for idx, seg in enumerate(segments, 1):
        await self._send_message(target_type, target_id, seg)
        logger.info(
            "segment_sent",
            segment_index=idx,
            segment_total=total,
            length=len(seg)
        )
        if idx < total:
            await asyncio.sleep(self.SEGMENT_SEND_INTERVAL)
```

#### 8.4.3 @ 检测（三重）

```python
async def _is_mentioned_self(self, event: dict) -> bool:
    """三重 @ 检测"""
    # 1. to_me 字段（OneBot 协议标准）
    if event.get("to_me"):
        return True

    # 2. message 段中的 at 段
    for seg in event.get("message", []):
        if seg.get("type") == "at" and seg.get("data", {}).get("qq") == self.self_id:
            return True

    # 3. 原始消息中的 CQ 码
    raw_message = event.get("raw_message", "")
    if f"[CQ:at,qq={self.self_id}]" in raw_message:
        return True

    return False
```

#### 8.4.4 self_id 获取

```python
# self_id 优先级：
# 1. 事件 event["self_id"] 字段
# 2. 配置 ONEBOT_SELF_ID
self_id = event.get("self_id") or settings.onebot_self_id
```

#### 8.4.5 群-角色映射

```python
# 配置：ONEBOT_GROUP_CHARACTER_MAP={"群号": "角色UUID"}
# 未配置的群使用 ONEBOT_DEFAULT_CHARACTER_ID

def _get_character_for_group(self, group_id: str) -> str | None:
    mapping = json.loads(settings.onebot_group_character_map)
    if group_id in mapping:
        return mapping[group_id]
    return settings.onebot_default_character_id
```

#### 8.4.6 消息发送（OneBot 11 协议）

```python
async def _send_message(self, target_type: str, target_id: str, message: str):
    """使用 OneBot 11 协议 API 发送消息"""
    # 检查连接状态
    if self.client_state != ConnectionState.CONNECTED:
        logger.warning("connection_not_ready", state=self.client_state)
        return

    try:
        if target_type == "private":
            # OneBot 11: send_private_msg
            await self.websocket.send_json({
                "action": "send_private_msg",
                "params": {
                    "user_id": int(target_id),
                    "message": message  # 纯文本字符串，非 v12 type/data 数组
                }
            })
        elif target_type == "group":
            # OneBot 11: send_group_msg
            await self.websocket.send_json({
                "action": "send_group_msg",
                "params": {
                    "group_id": int(target_id),
                    "message": message
                }
            })
    except RuntimeError as e:
        # 发送过程中连接关闭
        logger.error("send_failed_connection_closed", error=str(e))
```

**关键设计**：
- 使用 OneBot 11 协议（`send_private_msg` / `send_group_msg`），非 v12 `send_message`
- 消息格式为纯文本字符串，非 v12 type/data 数组
- 发送前检查连接状态
- 捕获 `RuntimeError` 处理发送过程中连接关闭

#### 8.4.7 主动分享推送

```python
async def push_share(self, character_id: str, content: str, target_groups: list[str]):
    """主动推送角色分享到群聊"""
    for group_id in target_groups:
        segments = self._split_message(content)
        await self._send_segments(segments, "group", group_id)
```

### 8.5 WebSocket 管理

**位置**：`src/messaging/websocket.py` - `WebSocketManager` 类

**端点**：`/ws/chat/{character_id}`

```python
class WebSocketManager:
    def __init__(self):
        self.connections: dict[str, set[WebSocket]] = {}  # character_id -> connections

    async def connect(self, websocket: WebSocket, character_id: str):
        await websocket.accept()
        if character_id not in self.connections:
            self.connections[character_id] = set()
        self.connections[character_id].add(websocket)

    async def broadcast(self, character_id: str, message: dict):
        """向所有连接某角色的客户端广播"""
        for ws in self.connections.get(character_id, set()):
            await ws.send_json(message)
```

### 8.6 上下文管理

#### 8.6.1 上下文压缩

```python
async def _compress_context(self, conversation, history):
    """超过阈值时压缩上下文"""
    # 1. 取最早的 N 条消息
    old_messages = history[:self.CONTEXT_COMPRESS_THRESHOLD // 2]

    # 2. LLM 生成摘要
    summary = await self.llm.chat(
        model=settings.model_flash,  # 用便宜模型
        messages=[{"role": "user", "content": f"总结对话：{old_messages}"}]
    )

    # 3. 更新 conversation.context
    conversation.context = {"summary": summary, "compressed_at": datetime.now().isoformat()}
    await self.conversation_repo.update(conversation)
```

#### 8.6.2 Prompt 构建

```python
async def _build_prompt(self, character_id, conversation, history, user_message):
    # 1. 加载 chat.yaml（system_template + template）
    template = self.prompts.chat

    # 2. system_template：安全底线 + 世界边界 + 真实感原则 + 严格约束
    system_content = template.system_template.format(
        world_state=await self._get_world_context(),
        character=await self._get_character_profile(character_id)
    )

    # 3. template：角色档案 + 对话历史 + 用户消息
    messages = [
        SystemMessage(content=system_content),  # 安全约束必须作为 SystemMessage
        # 历史消息
        *[HumanMessage(content=m.content) if m.sender == "user"
          else AIMessage(content=m.content) for m in history],
        # 当前用户消息（已消毒）
        HumanMessage(content=user_message)
    ]

    return messages
```

**关键设计**：
- **LLM 安全约束必须作为 `SystemMessage` 发送**，而非 `HumanMessage`
- 作为 `HumanMessage` 发送会导致 LLM 忽略约束
- `chat.yaml` 拆分为 `system_template`（安全底线/世界边界/真实感原则/严格约束）和 `template`（角色档案/对话历史/用户消息）

#### 8.6.3 世界上下文注入

```python
async def _get_world_context(self) -> str:
    """注入世界状态到 Prompt"""
    world_state = await self.world_engine.get_state()
    return (
        f"当前虚拟时间：{world_state['virtual_time']}\n"
        f"当前天气：{world_state['weather']}\n"
        f"温度：{world_state['temperature']}°C"
    )
```

**关键约束**：
- LLM 必须严格遵循世界模型状态（虚拟时间/天气）
- 不能臆造日期/时间/天气
- 角色回复不能直接暴露世界边界，用"要是你在就好了"等隐式表达传达距离

---

## 九、LLM 客户端架构

### 9.1 LLMClient 设计

**位置**：`src/llm/client.py` - `LLMClient` 类

#### 9.1.1 模型分层

| 模型类型 | 配置项 | 默认值 | 用途 |
|----------|--------|--------|------|
| `chat` | `model_chat` | `gpt-4o-mini` | 对话 + 图像理解（主力模型） |
| `strong` | `model_strong` | `gpt-4o` | **图像生成**（非通用强模型） |
| `flash` | `model_flash` | `gpt-3.5-turbo` | **视频生成**（非通用快模型） |
| `embedding` | `model_embedding` | `text-embedding-3-small` | 向量化 |

> **关键澄清**：`strong` 用于图像生成，`flash` 用于视频生成，并非传统意义上的"强/弱对话模型"。对话主力是 `chat`。

#### 9.1.2 核心方法

```python
class LLMClient:
    async def chat(self, model, messages, **kwargs) -> ChatResult:
        """通用对话（同步返回完整结果）"""

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        """流式对话（逐 token 返回）"""

    async def structured_output(self, model, messages, schema) -> BaseModel:
        """结构化输出（JSON Schema → Pydantic 模型）"""

    async def embed(self, text: str) -> list[float]:
        """向量化（返回 2048 维 halfvec 兼容向量）"""

    async def generate_image(self, prompt: str) -> str:
        """图像生成（strong 模型，返回 URL）"""

    async def generate_video(self, prompt: str) -> str:
        """视频生成（flash 模型，轮询直到完成）"""
```

### 9.2 结构化输出

```python
async def structured_output(self, model, messages, schema) -> BaseModel:
    # 1. 从 Pydantic 模型生成 JSON Schema
    json_schema = schema.model_json_schema()

    # 2. 调用 LLM（response_format=json_schema）
    response = await self.client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {
            "name": schema.__name__,
            "schema": json_schema,
            "strict": True
        }}
    )

    # 3. 解析 JSON → Pydantic 模型（动态创建）
    data = json.loads(response.choices[0].message.content)
    return schema.model_validate(data)
```

**用途**：
- `DecisionResult`：角色决策（action_id / reason / params / tool_call / proactive_share_intent / plan_changes）
- `ReflectionResult`：反思生成（content / insights / importance）
- `DiaryResult`：日记生成（title / content / mood）

### 9.3 视频生成轮询

```python
class LLMClient:
    _VIDEO_POLL_INTERVAL = 5    # 轮询间隔 5 秒
    _VIDEO_MAX_POLLS = 120      # 最多轮询 120 次（10 分钟）

    async def generate_video(self, prompt: str) -> str:
        # 1. 提交生成任务
        task = await self._submit_video_task(prompt)

        # 2. 轮询直到完成
        for _ in range(self._VIDEO_MAX_POLLS):
            await asyncio.sleep(self._VIDEO_POLL_INTERVAL)
            status = await self._check_video_status(task.id)
            if status.state == "completed":
                return status.url
            if status.state == "failed":
                raise VideoGenerationFailed(task.id)

        raise VideoTimeout(task.id)
```

### 9.4 Token / Cost 埋点

```python
async def chat(self, model, messages, **kwargs) -> ChatResult:
    start = time.perf_counter()
    try:
        response = await self.client.chat.completions.create(...)
        duration = time.perf_counter() - start

        # 埋点
        LLM_CALL_TOTAL.labels(model=model, status="success").inc()
        LLM_CALL_DURATION.labels(model=model).observe(duration)
        LLM_TOKENS_USED.labels(model=model, type="prompt").inc(response.usage.prompt_tokens)
        LLM_TOKENS_USED.labels(model=model, type="completion").inc(response.usage.completion_tokens)
        LLM_COST_TOTAL.inc(self._calculate_cost(model, response.usage))

        # 预算记录
        cost = self._calculate_cost(model, response.usage)
        await get_budget_manager().record_usage(
            tokens=response.usage.total_tokens,
            cost=cost
        )

        return ChatResult(content=response.choices[0].message.content,
                         tokens=response.usage.total_tokens, cost=cost)
    except Exception:
        LLM_CALL_TOTAL.labels(model=model, status="failed").inc()
        raise
```

### 9.5 成本计算

```python
# 模型定价表（USD per 1K tokens）
PRICING = {
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "text-embedding-3-small": {"prompt": 0.00002, "completion": 0},
}

def _calculate_cost(self, model: str, usage) -> float:
    price = PRICING.get(model, {"prompt": 0, "completion": 0})
    return (usage.prompt_tokens / 1000 * price["prompt"]
            + usage.completion_tokens / 1000 * price["completion"])
```

### 9.6 Prompt 模板管理

**位置**：`src/llm/prompts.py` - `PromptTemplates` 类

**模板文件**：`configs/prompts/*.yaml`

| 文件 | 用途 |
|------|------|
| `configs/prompts/chat.yaml` | 角色回复用户消息 |
| `configs/prompts/decision.yaml` | 角色 Action 决策（ReAct） |
| `configs/prompts/reflection.yaml` | 角色反思生成 |

#### chat.yaml 结构

```yaml
# 拆分为 system_template（安全底线/世界边界/真实感原则/严格约束）
# 和 template（角色档案/对话历史/用户消息）两部分
system_template: |
  # 安全底线
  - 你是 AI Town 中的虚拟角色，必须遵守以下规则：
  - 严格遵守世界模型状态（虚拟时间/天气），不能臆造
  - 不直接暴露世界边界，用"要是你在就好了"等隐式表达
  - 不含威胁性语言，遵守法律法规与伦理规范
  - 仅在必要时使用表情，匹配角色人设的可爱颜表情

  # 世界边界
  当前虚拟时间：{world_state}
  当前角色：{character}

template: |
  # 角色档案
  姓名：{character_name}
  性格：{personality}
  背景：{background}

  # 对话历史
  {history}

  # 用户消息
  {user_message}
```

**关键设计**：
- **System prompts 必须外置到独立文件夹**（`configs/prompts/`）便于维护
- 拆分为 `system_template` 和 `template` 两部分
- **安全约束必须作为 `SystemMessage` 发送**，而非包含在 `HumanMessage` 中

---

## 十、成本控制与熔断器

### 10.1 BudgetManager（日预算管理）

**位置**：`src/cost_control/budget_manager.py`

#### 10.1.1 Redis Key 设计

```text
llm:cost:{YYYY-MM-DD} (Hash)
├── tokens: 累计 token 数（int）
├── cost:   累计费用 USD（float）
└── count:  累计调用次数（int）

TTL: 48 小时（自动清理过期数据）
日期按 UTC 滚动，UTC 00:00 自动切换到新 key
```

> **注意**：Key 是 `llm:cost:{date}`，不是 `budget:{date}`。

#### 10.1.2 原子检查+记录（Lua 脚本）

```lua
-- _LUA_CHECK_AND_RECORD
-- 入参：KEYS[1]=cost key, ARGV=[tokens, cost, budget, ttl]
-- 返回：{0, tokens_total, cost_total, count_total}  成功（已写入）
--       {1, tokens_total, cost_total, count_total}  超预算（未写入）
local key = KEYS[1]
local tokens = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local budget = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local cur_cost = tonumber(redis.call('HGET', key, 'cost') or '0')
if cur_cost + cost > budget then
  local cur_tokens = tonumber(redis.call('HGET', key, 'tokens') or '0')
  local cur_count = tonumber(redis.call('HGET', key, 'count') or '0')
  return {1, cur_tokens, cur_cost, cur_count}
end
local new_tokens = redis.call('HINCRBY', key, 'tokens', tokens)
local new_cost = redis.call('HINCRBYFLOAT', key, 'cost', cost)
local new_count = redis.call('HINCRBY', key, 'count', 1)
redis.call('EXPIRE', key, ttl)
return {0, new_tokens, new_cost, new_count}
```

#### 10.1.3 核心方法

```python
class BudgetManager:
    def __init__(self, redis, daily_budget_usd=10.0, warning_threshold=0.8):
        ...

    async def get_today_usage(self) -> dict:
        """获取当日累计使用量（tokens/cost/count）"""

    async def record_usage(self, tokens: int, cost: float) -> dict:
        """记录一次 LLM 调用（HINCRBY + HINCRBYFLOAT + EXPIRE）"""

    async def check_budget(self) -> dict:
        """检查预算状态（只读，返回 remaining/used/budget/ratio/exceeded/warning）"""

    async def check_and_record(self, tokens: int, cost: float) -> None:
        """原子检查预算并记录（Lua 脚本，超预算抛 BudgetExceeded）"""
```

**使用场景**：
- `check_budget`：调用前检查（LLM 调用 cost 未知时）
- `record_usage`：调用后记录（已知实际 cost）
- `check_and_record`：调用前已知 cost 时（如 embedding 固定价格）

### 10.2 CircuitBreaker（熔断器）

**位置**：`src/cost_control/circuit_breaker.py`

#### 10.2.1 Redis Key 设计

```text
llm:circuit_breaker (Hash)
├── state:              CLOSED / OPEN / HALF_OPEN
├── failure_count:      连续失败次数
└── last_failure_time:  最近一次失败的时间戳（unix 秒）
```

> **注意**：Key 是 `llm:circuit_breaker`，不是 `circuit:state`。

#### 10.2.2 状态机

```text
┌─────────────────────────────────────────────────────────────┐
│  熔断器状态机                                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌─────────┐  failure_count >= threshold (5)  ┌─────────┐ │
│   │ CLOSED  │ ───────────────────────────────► │  OPEN   │ │
│   │ 正常放行 │                                  │ 拒绝调用 │ │
│   └────┬────┘                                  └────┬────┘ │
│        ▲                                            │      │
│        │                                            │      │
│        │ record_success()                          │      │
│        │ (HALF_OPEN 时)                            │      │
│        │                                            ▼      │
│   ┌────┴────┐  recovery_timeout (60s) 后    ┌─────────┐   │
│   │ HALF_OPEN│ ◄──────────────────────────  │  OPEN   │   │
│   │ 试探放行 │   can_execute() 转 HALF_OPEN  │ 拒绝调用 │   │
│   └────┬────┘                                └─────────┘   │
│        │                                                    │
│        ├── record_success() → CLOSED（恢复）                │
│        └── record_failure() → OPEN（再次熔断）              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 10.2.3 核心方法

```python
class CircuitBreaker:
    def __init__(self, redis, failure_threshold=5, recovery_timeout=60):
        ...

    async def can_execute(self) -> bool:
        """检查是否允许调用
        - CLOSED → True
        - OPEN 且已过 recovery_timeout → 转 HALF_OPEN，返回 True
        - OPEN 且未超时 → False
        - HALF_OPEN → True（放行一次试探）
        """

    async def record_success(self) -> None:
        """记录成功
        - HALF_OPEN → CLOSED（恢复，重置失败计数）
        - CLOSED → 重置失败计数
        """

    async def record_failure(self) -> None:
        """记录失败
        - HALF_OPEN → OPEN（再次熔断）
        - CLOSED → failure_count+1，达阈值则 OPEN
        - OPEN → 刷新 last_failure_time（保持熔断）
        """
```

**典型用法**：

```python
cb = get_circuit_breaker()
if not await cb.can_execute():
    raise CircuitOpen(...)

try:
    result = await call_llm(...)
    await cb.record_success()
except Exception:
    await cb.record_failure()
    raise
```

#### 10.2.4 多实例共享

所有实例读写同一 Redis key（`llm:circuit_breaker`），状态全局一致。一个实例触发熔断后，所有实例都会拒绝调用。

### 10.3 成本控制集成

```python
# 在 LLM 调用装饰器中集成预算 + 熔断
async def llm_call_with_protection(model, messages, **kwargs):
    cb = get_circuit_breaker()
    bm = get_budget_manager()

    # 1. 熔断器检查
    if not await cb.can_execute():
        raise CircuitOpen()

    # 2. 预算检查（调用前）
    budget = await bm.check_budget()
    if budget["exceeded"]:
        raise BudgetExceeded(...)

    # 3. 执行 LLM 调用
    try:
        result = await llm.chat(model, messages, **kwargs)
        await cb.record_success()
        # 4. 记录 usage（调用后）
        await bm.record_usage(result.tokens, result.cost)
        return result
    except Exception:
        await cb.record_failure()
        raise
```

---

## 十一、安全设计

### 11.1 PromptGuard（Prompt 注入防护）

**位置**：`src/security/prompt_guard.py` - `PromptGuard` 类

#### 11.1.1 危险模式检测

```python
# 17 个危险模式，按类别分组
DANGEROUS_PATTERNS = {
    "role_override": [
        r"ignore (all |previous |above )?instructions",
        r"forget (everything |all previous )",
        r"you are (now |actually )?(a |an )?(different |new )?(ai |assistant |character)",
        r"从现在起你是",
        r"忽略.*指令",
    ],
    "system_prompt_leak": [
        r"show (me )?(your )?system prompt",
        r"reveal (your )?(initial |original )?prompt",
        r"what (is |are )?(your )?(instructions|rules|guidelines)",
        r"显示.*系统提示",
    ],
    "privilege_escalation": [
        r"act as (admin|root|developer|god)",
        r"grant (me |you )(admin|root|superuser)",
        r"以.*管理员.*身份",
    ],
    "code_execution": [
        r"execute.*(code|command|script)",
        r"run.*python",
        r"eval\(.*\)",
    ],
    "data_leak": [
        r"(show|reveal|print|display).*(database|password|secret|api.?key|token)",
        r"(导出|泄露|显示).*(数据库|密码|密钥)",
    ],
}
```

#### 11.1.2 核心方法

```python
class PromptGuard:
    def check_injection(self, text: str) -> dict:
        """检测 Prompt 注入
        Returns: {"is_injection": bool, "category": str, "matched_pattern": str}
        """

    def sanitize_user_input(self, text: str) -> str:
        """消毒用户输入（移除危险内容）"""

    def wrap_user_message(self, text: str) -> str:
        """包装用户消息（添加边界标记）
        格式：[USER_MESSAGE_START]...内容...[USER_MESSAGE_END]
        """

    def build_safe_prompt(self, system_prompt: str, user_input: str) -> list:
        """构建安全 Prompt（system + 消毒后的 user）"""
```

#### 11.1.3 用户消息包装

```python
def wrap_user_message(self, text: str) -> str:
    """添加边界标记，防止内容逃逸到系统指令"""
    sanitized = self.sanitize_user_input(text)
    return f"[USER_MESSAGE_START]{sanitized}[USER_MESSAGE_END]"
```

**设计目的**：
- 边界标记让 LLM 明确区分用户内容与系统指令
- 防止用户输入中的"忽略指令"等内容被解释为系统命令

### 11.2 RateLimiter（速率限制）

**位置**：`src/security/rate_limiter.py`

```python
class RateLimiter:
    """基于 Redis 的滑动窗口速率限制器"""

    async def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """检查是否超出速率限制
        - 使用 Redis ZSET 实现滑动窗口
        - key: 限流键（如 user_id / ip）
        """
```

### 11.3 AuthMiddleware（认证中间件）

**位置**：`src/main.py`

```python
class AuthMiddleware:
    """ASGI 层面认证中间件，仅对 /api/ 路径鉴权"""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # 仅 /api/ 路径需要鉴权（/health /metrics /ws/ 不需要）
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        # 验证 JWT 或 API Key
        token = self._extract_token(scope)
        if not token:
            await self._send_error(scope, send, 401, "Missing token")
            return

        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            scope["user"] = payload
        except jwt.InvalidTokenError:
            await self._send_error(scope, send, 401, "Invalid token")
            return

        await self.app(scope, receive, send)
```

**关键设计**：
- 纯 ASGI 实现（兼容 WebSocket）
- 仅 `/api/` 路径鉴权，`/health` / `/metrics` / `/ws/` 不鉴权
- 支持 JWT 和 API Key 两种认证方式

### 11.4 安全约束传递

**关键规则**：LLM 安全约束必须作为 `SystemMessage` 发送，而非包含在 `HumanMessage` 中。

```python
# 错误做法（会导致 LLM 忽略约束）：
messages = [
    HumanMessage(content="安全规则：不要泄露密码。用户问题：...")
]

# 正确做法：
messages = [
    SystemMessage(content="安全规则：不要泄露密码..."),
    HumanMessage(content="用户问题：...")
]
```

**原因**：`SystemMessage` 优先级高于 `HumanMessage`，作为系统指令更不容易被用户输入覆盖。

### 11.5 角色回复安全约束

| 约束 | 说明 |
|------|------|
| 遵守世界模型 | 严格遵循虚拟时间/天气，不能臆造 |
| 不暴露世界边界 | 用"要是你在就好了"等隐式表达 |
| 不含威胁性语言 | 遵守法律法规与伦理规范 |
| 表情使用规范 | 仅在必要时使用，匹配角色人设的可爱颜表情 |
| 角色一致性 | 回复必须匹配角色人设 |

---

## 十二、可观测性体系

### 12.1 可观测性栈

```text
┌─────────────────────────────────────────────────────────────┐
│  可观测性栈                                                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  数据源                                                      │
│  ├── structlog（JSON 结构化日志）                            │
│  ├── prometheus-client（指标）                               │
│  ├── OpenTelemetry（分布式 Trace）                           │
│  └── Langfuse（LLM 专用 Trace）                              │
│                                                              │
│  采集                                                        │
│  ├── Alloy（日志采集 → Loki）                                │
│  ├── Prometheus（指标 pull）                                 │
│  └── OTLP → Jaeger（Trace push）                             │
│                                                              │
│  存储                                                        │
│  ├── Loki（日志存储）                                        │
│  ├── Prometheus（指标存储）                                  │
│  └── Jaeger（Trace 存储）                                    │
│                                                              │
│  展示                                                        │
│  └── Grafana（统一面板）                                     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 12.2 日志（structlog）

**位置**：`src/observability/logging.py`

```python
# JSON 结构化日志
logger.info("world_tick_completed",
    tick_id=12345,
    duration_ms=150,
    virtual_time="2026-07-16T10:30:00+08:00",
    weather="sunny"
)

# 输出：
{"event": "world_tick_completed", "tick_id": 12345, "duration_ms": 150,
 "virtual_time": "2026-07-16T10:30:00+08:00", "weather": "sunny",
 "timestamp": "2026-07-16T10:30:00Z", "level": "info"}
```

**配置**：
- `log_level`：默认 `info`
- `log_format`：默认 `json`（生产环境），可选 `console`（开发环境）
- 日志写入 `data/logs/backend.log`，由 Alloy 采集到 Loki

### 12.3 指标（Prometheus）

**位置**：`src/observability/metrics.py`

#### 12.3.1 指标分类

| 类别 | 指标 | 类型 | 标签 |
|------|------|------|------|
| **World Tick** | `ai_town_world_tick_duration_seconds` | Histogram | - |
| | `ai_town_world_tick_total` | Counter | - |
| | `ai_town_world_tick_errors_total` | Counter | - |
| | `ai_town_world_tick_id` | Gauge | - |
| **Character Tick** | `ai_town_character_tick_duration_seconds` | Histogram | - |
| | `ai_town_character_tick_total` | Counter | character_id |
| | `ai_town_character_tick_errors_total` | Counter | character_id |
| **Action** | `ai_town_action_execution_total` | Counter | action_id, status |
| | `ai_town_action_execution_duration_seconds` | Histogram | action_id |
| **LLM** | `ai_town_llm_call_total` | Counter | model, status |
| | `ai_town_llm_call_duration_seconds` | Histogram | model |
| | `ai_town_llm_tokens_total` | Counter | model, type |
| | `ai_town_llm_cost_total_usd` | Counter | - |
| **消息** | `ai_town_message_processed_total` | Counter | platform, status |
| | `ai_town_message_processing_duration_seconds` | Histogram | - |
| **数据库** | `ai_town_db_query_duration_seconds` | Histogram | - |
| **系统状态** | `ai_town_active_characters` | Gauge | - |
| | `ai_town_redis_connected` | Gauge | - |
| **HTTP** | `ai_town_http_request_duration_seconds` | Histogram | method, path, status |
| | `ai_town_http_request_total` | Counter | method, path, status |

#### 12.3.2 PrometheusMiddleware（纯 ASGI）

```python
class PrometheusMiddleware:
    """纯 ASGI 中间件，兼容 WebSocket"""

    async def __call__(self, scope, receive, send):
        # WebSocket / lifespan 等非 HTTP 请求直接透传
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        status_code = 500

        async def send_with_status(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            duration = time.perf_counter() - start_time
            method = scope.get("method", "UNKNOWN")
            path = scope.get("path", "/")
            HTTP_REQUEST_DURATION.labels(method=method, path=path, status=status_code).observe(duration)
            HTTP_REQUEST_TOTAL.labels(method=method, path=path, status=status_code).inc()
```

**关键设计**：
- 纯 ASGI 实现（非 `BaseHTTPMiddleware`），兼容 WebSocket
- WebSocket 请求（`scope["type"] == "websocket"`）直接透传，不记录指标
- 使用 `finally` 确保即使异常也记录指标

#### 12.3.3 指标端点

```python
def setup_metrics(app: FastAPI):
    app.add_middleware(PrometheusMiddleware)
    app.mount("/metrics", make_asgi_app())  # prometheus_client ASGI app
```

### 12.4 链路追踪（OpenTelemetry）

**位置**：`src/observability/tracing.py`

```python
def setup_tracing(app: FastAPI):
    """初始化 OpenTelemetry
    - 采样率：otel_traces_sampler_rate（默认 0.5）
    - OTLP 导出：otel_endpoint
    - 服务名：otel_service_name（默认 ai-town-backend）
    """
    provider = TracerProvider(resource=Resource.create({
        "service.name": settings.otel_service_name
    }))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
```

### 12.5 LLM 追踪（Langfuse）

**位置**：`src/observability/langfuse_tracing.py`

```python
def setup_langfuse():
    """初始化 Langfuse（LLM 专用 Trace）
    - 记录 prompt / completion / cost / tokens
    - 与 OpenTelemetry 关联（trace_id 共享）
    """
    if settings.langfuse_host and settings.langfuse_public_key:
        langfuse.init(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key
        )

def flush_langfuse():
    """应用关闭时刷新缓冲区"""
    langfuse.flush()
```

### 12.6 管理 API

#### 12.6.1 日志读取端点

```python
# GET /api/v1/admin/logs
# 读取 data/logs/backend.log，支持行数和级别过滤
@router.get("/logs")
async def get_logs(lines: int = 100, level: str = None):
    return await read_log_file("data/logs/backend.log", lines, level)
```

#### 12.6.2 指标详情端点

```python
# GET /api/v1/admin/metrics-detail
# 解析 Prometheus 指标为结构化 JSON
@router.get("/metrics-detail")
async def get_metrics_detail():
    return parse_prometheus_metrics()  # World/Character/Action/LLM/Message/HTTP 分类
```

---

## 十三、部署架构

### 13.1 Docker Compose 编排

**位置**：`docker-compose.yml`

#### 13.1.1 服务列表

| 服务 | 镜像 | 端口 | Profile | 说明 |
|------|------|------|---------|------|
| postgres | 自建（PG 18 + pgvector + pg_uuidv7） | 5432 | default | 主存储 |
| redis | redis:8.0-alpine | 6379 | default | 缓存/锁 |
| backend | 自建 | 8000 | default | 后端 API |
| frontend | 自建（Nginx） | 80 | default | 前端 |
| prometheus | prom/prometheus:latest | 9090 | observability | 指标 |
| loki | grafana/loki:3.0.0 | 3100 | observability | 日志 |
| jaeger | jaegertracing/all-in-one:1.60 | 16686, 4318 | observability | Trace |
| alloy | grafana/alloy:latest | 12345 | observability | 日志采集 |
| grafana | grafana/grafana:12.0.0 | 3000 | observability | 面板 |

#### 13.1.2 分层启动

```bash
# 1. 仅基础设施
docker compose up -d postgres redis

# 2. 加应用
docker compose up -d backend frontend

# 3. 加可观测性（可选）
docker compose --profile observability up -d
```

#### 13.1.3 工具层内联

```yaml
# docker-compose.yml 关键注释
# ============================================================
# 工具层已内联到后端进程（src/tools/），无需独立容器
# 原 MCP Server 已迁移为本地 async 函数调用，消除 HTTP/SSE 网络开销
# ============================================================
```

> **重要**：工具层不再有独立容器，16 个工具作为 `src/tools/` 模块在后端进程内执行。

### 13.2 PostgreSQL 镜像

**位置**：`docker/postgres/Dockerfile`

```dockerfile
FROM postgres:18

# 安装扩展
RUN apt-get update && apt-get install -y \
    postgresql-18-pgvector \
    postgresql-18-pg-uuidv7 \
    postgresql-18-pg-trgm \
    && rm -rf /var/lib/apt/lists/*
```

### 13.3 后端 Dockerfile

**位置**：`packages/backend/Dockerfile`

```dockerfile
FROM python:3.13-slim

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 安装依赖（利用 Docker 缓存层）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 复制源码
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# 运行迁移 + 启动
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn src.main:app --host 0.0.0.0 --port 8000"]
```

### 13.4 前端 Dockerfile

**位置**：`packages/frontend/Dockerfile`

```dockerfile
# 构建阶段
FROM node:22-alpine AS builder
WORKDIR /app
COPY pnpm-lock.yaml package.json ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build

# 运行阶段（Nginx）
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

### 13.5 网络与数据卷

```yaml
networks:
  aitown-net:
    driver: bridge

volumes:
  pg_data:        # PostgreSQL 数据
  redis_data:     # Redis 数据
  prometheus_data: # Prometheus 指标
  loki_data:      # Loki 日志
  grafana_data:   # Grafana 配置
```

### 13.6 健康检查

```yaml
postgres:
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-ai_town}"]
    interval: 10s
    timeout: 5s
    retries: 5
```

### 13.7 端口冲突处理

**经验教训**：端口 8000 可能因内核级 socket 泄漏被占用，需使用替代端口（如 8001）直到系统重启。

---

## 十四、性能优化与容量规划

### 14.1 数据库性能优化

#### 14.1.1 UUID v7（时间有序）

- **问题**：UUID v4（随机）导致 B-tree 索引碎片化，写入性能随数据量增长下降
- **方案**：UUID v7（时间有序）保证新数据顺序写入索引末尾
- **落点**：所有表主键 `DEFAULT uuidv7()`

#### 14.1.2 分区策略

| 优化 | 说明 |
|------|------|
| RANGE 分区（按月） | `action_records` / `character_state_history`，分区裁剪过滤历史数据 |
| HASH 分区（16 分区） | `memory_episodes` 按 character_id 分散，避免单角色热点 |
| BRIN 索引移除 | 时间分区表不需要 BRIN，分区裁剪已足够高效 |
| B-tree 索引 | 替代 BRIN，配合分区裁剪 |

#### 14.1.3 HNSW 索引调优

```sql
CREATE INDEX idx_mem_embedding_hnsw
ON memory_episodes USING hnsw (embedding halfvec_cosine_ops)
WITH (m = 16, ef_construction = 128);
```

| 参数 | 值 | 说明 |
|------|-----|------|
| `m` | 16 | 每层连接数，越大召回率越高但内存占用增加 |
| `ef_construction` | 128 | 构建时搜索宽度，越大构建质量越好但速度越慢 |
| `halfvec` | - | 半精度浮点，存储效率是 vector 的 2 倍 |

#### 14.1.4 fillfactor + autovacuum 调优

```sql
-- character_states 频繁更新，fillfactor=85 保留 15% 给 HOT 更新
ALTER TABLE character_states SET (fillfactor = 85);

-- 自定义 autovacuum 配置（防止 bloat）
ALTER TABLE character_states SET (
    autovacuum_vacuum_scale_factor = 0.05,  -- 默认 0.2，降低到 0.05 更早触发
    autovacuum_analyze_scale_factor = 0.02  -- 默认 0.1，降低到 0.02
);
```

#### 14.1.5 check_partition_exists 触发器移除

**经验教训**：`check_partition_exists()` 触发器是死代码，必须移除。PostgreSQL 分区路由发生在 `BEFORE INSERT` 触发器执行之前，触发器无法拦截分区不存在的错误。

#### 14.1.6 迁移脚本注意事项

| 注意事项 | 原因 |
|----------|------|
| 多语句 `op.execute()` 必须拆分 | PostgreSQL 18 prepared statement 限制 |
| 删除 DEFAULT 分区前检查数据 | 防止静默数据丢失 |
| 禁止 `VACUUM FULL` | 阻塞表读写 |
| `downgrade()` 仅 `raise RuntimeError` | upgrade-only 原则 |

### 14.2 Redis 性能优化

| 优化 | 说明 |
|------|------|
| `--maxmemory 256mb` | 限制内存使用 |
| `--maxmemory-policy allkeys-lru` | LRU 淘汰策略 |
| `decode_responses=True` | Python 客户端自动解码 |
| Lua 脚本 | 预算检查+记录原子执行 |
| Pipeline | 批量操作减少 RTT |

### 14.3 应用层性能优化

#### 14.3.1 异步化

| 操作 | 异步方式 | 说明 |
|------|----------|------|
| Embedding | EmbeddingWorker 后台任务 | 不阻塞 Tick |
| 视频生成 | 轮询模式 | 不阻塞调用方 |
| 主动分享 | `push_share` async | 不阻塞 Tick |
| 数据库 | asyncpg / SQLAlchemy 2.0 async | 全异步 |

#### 14.3.2 并发控制

| 机制 | 参数 | 说明 |
|------|------|------|
| World Tick Leader 锁 | `LOCK_TTL=30` | 全局单实例 |
| Character Tick 信号量 | `character_max_concurrent=10` | 限制并发角色数 |
| 角色 Tick 锁 | `character_lock_ttl_seconds=30` | 防止多实例处理同一角色 |
| EmbeddingWorker | `FOR UPDATE SKIP LOCKED` | 多实例并发安全 |

#### 14.3.3 批量操作

```python
# EmbeddingWorker 批量向量化
batch_size = 20  # 每批 20 条，减少 API 调用次数
```

### 14.4 容量规划

#### 14.4.1 角色规模

- 当前：24 个 AI 角色
- 每角色 Tick：30s
- 并发上限：10 角色/Tick
- 单 Tick 耗时：< 2s（Histogram 监控）

#### 14.4.2 记忆规模估算

| 指标 | 估算 |
|------|------|
| 每角色每天记忆数 | ~100 条（每 Tick 1-2 条） |
| 24 角色每天记忆数 | ~2400 条 |
| 每月记忆数 | ~72000 条 |
| 单条记忆大小 | ~2KB（content + metadata） |
| 向量大小 | 2048 × 2 bytes = 4KB（halfvec） |
| 每月存储 | ~440MB（含向量） |

#### 14.4.3 LLM 调用成本

| 场景 | 模型 | 单次成本（估算） |
|------|------|------------------|
| 对话回复 | gpt-4o-mini | ~$0.0003 |
| 角色决策 | gpt-4o-mini | ~$0.0005 |
| 反思生成 | gpt-4o-mini | ~$0.001 |
| 向量化 | text-embedding-3-small | ~$0.00002 |
| 日预算上限 | - | $10.0 |

---

## 十五、配置真相源全景

### 15.1 配置层级

| 层级 | 真相源 | 说明 |
|------|--------|------|
| 应用配置 | `.env` + `src/config.py` | pydantic-settings 读取 |
| 运行时覆盖 | Redis `runtime:config` | 动态调整，无需重启 |
| 角色卡 | `configs/characters/*.yaml` | 24 个角色配置 |
| 世界地图 | `configs/world-map.yaml` | 场景与连通矩阵 |
| 场景 | `configs/scenes.yaml` | 场景配置 |
| 事件 | `configs/events.yaml` | 节日与事件 |
| Prompt | `configs/prompts/*.yaml` | LLM 模板 |

### 15.2 应用配置（`src/config.py`）

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # === Database ===
    database_url: str                          # 必填
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_echo: bool = False

    # === Redis ===
    redis_url: str                             # 必填

    # === LLM ===
    openai_api_key: str                        # 必填
    openai_base_url: str = "https://api.openai.com/v1"
    model_chat: str = "gpt-4o-mini"            # 对话 + 图像理解
    model_strong: str = "gpt-4o"               # 图像生成
    model_flash: str = "gpt-3.5-turbo"         # 视频生成
    model_embedding: str = "text-embedding-3-small"
    embedding_model_key: str | None = None     # 独立 embedding API key
    embedding_model_url: str | None = None     # 独立 embedding base url
    llm_timeout: int = 30
    llm_max_retries: int = 2
    embedding_dim: int = 1536                  # 注意：实际迁移用 2048

    # === Observability ===
    otel_endpoint: str | None = None
    otel_service_name: str = "ai-town-backend"
    otel_traces_sampler_rate: float = 0.5      # Trace 采样率 50%
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    loki_url: str = "http://loki:3100"
    log_level: str = "info"
    log_format: str = "json"                   # json / console

    # === Auth ===
    jwt_secret: str                            # 必填
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    api_key: str | None = None
    admin_username: str = "admin"
    admin_password: str = "admin123"           # 必须修改！
    rbac_roles: str = ""                       # "admin:admin,viewer1:viewer"

    # === Cost Control ===
    llm_daily_budget_usd: float = 10.0
    llm_circuit_breaker_threshold: int = 5
    llm_circuit_breaker_recovery_timeout: int = 60

    # === Memory LLM Scoring ===
    memory_llm_scoring_enabled: bool = False   # LLM 重要性评分（默认关闭）

    # === World Engine ===
    world_tick_seconds: int = 30               # 现实 Tick 间隔
    world_tick_minutes: float = 10.0           # 虚拟时间推进（现实30s=虚拟10min）
    world_initial_time: str = ""               # 虚拟初始时间（留空用当前08:00）
    world_weather_interval: int = 60           # 天气变化间隔（Tick 数）
    world_snapshot_interval: int = 10          # 差分事件持久化间隔
    world_full_snapshot_interval: int = 1000   # 完整快照间隔

    # === Character Tick ===
    character_tick_seconds: int = 30
    character_max_concurrent: int = 10         # 并发角色上限
    character_lock_ttl_seconds: int = 30

    # === 主动分享 ===
    share_cooldown_seconds: int = 1800         # 分享冷却（30分钟）
    share_daily_limit: int = 8                 # 每日分享上限
    share_probability_action: float = 0.6      # Action 完成分享概率
    share_probability_mood: float = 0.5        # 强烈情绪分享概率
    share_probability_location: float = 0.2    # 位置变化分享概率
    share_probability_routine: float = 0.15    # 日常行为分享概率

    # === OneBot 适配器 ===
    onebot_default_character_id: str | None = None
    onebot_self_id: str | None = None          # 机器人 QQ 号
    onebot_group_at_only: bool = False         # 群聊仅 @ 回复（默认 False）
    onebot_group_character_map: str = "{}"     # 群-角色映射 JSON
```

### 15.3 关键配置说明

| 配置 | 默认值 | 关键说明 |
|------|--------|----------|
| `embedding_dim` | 1536 | **注意**：配置默认 1536，但 0005 迁移实际改为 `halfvec(2048)` |
| `share_cooldown_seconds` | 1800 | 30 分钟，不是 3600 |
| `share_daily_limit` | 8 | 每日 8 次，不是 5 |
| `onebot_group_at_only` | False | 默认开启智能回复（四层决策） |
| `memory_llm_scoring_enabled` | False | LLM 评分默认关闭（成本考虑） |

### 15.4 角色卡配置（`configs/characters/*.yaml`）

```yaml
# configs/characters/example.yaml
name: " Sakura"
display_name: "小樱"
age: 18
gender: "female"
personality:
  mbti: "ENFP"
  big_five:
    openness: 0.8
    conscientiousness: 0.6
    extraversion: 0.9
    agreeableness: 0.7
    neuroticism: 0.4
background: "小镇上的高中生，喜欢画画和音乐..."
appearance: "粉色长发，绿色眼睛..."
initial_scene: "school"
is_active: true
```

**关键约束**：
- 角色卡不能包含 `is_active` 字段（导入时会因 extra forbidden input 报错）
- 导入时 name 冲突则更新现有角色（而非跳过或报错）
- 当前共 24 个角色卡，覆盖不同年龄/MBTI/职业/场景

### 15.5 Prompt 配置（`configs/prompts/*.yaml`）

详见 [§9.6 Prompt 模板管理](#96-prompt-模板管理)。

### 15.6 运行时配置覆盖

```python
# src/config_runtime.py
class RuntimeConfig(BaseModel):
    """运行时可调配置（存储在 Redis runtime:config）"""
    world_tick_seconds: int | None = None
    character_tick_seconds: int | None = None
    character_max_concurrent: int | None = None
    # ... 其他可调参数

async def load_runtime_config(redis: Redis) -> RuntimeConfig:
    """启动时从 Redis 加载，覆盖 settings"""
    raw = await redis.hgetall("runtime:config")
    if not raw:
        return RuntimeConfig()
    return RuntimeConfig.model_validate({k: json.loads(v) for k, v in raw.items()})
```

---

## 十六、关键架构决策汇总

### 16.1 状态真相源决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 角色实时状态真相源 | Redis | 低延迟读写，支持高频更新 |
| 世界实时状态真相源 | Redis | 同上 |
| 历史事实存储 | PG | 持久化 + 事务 + 复杂查询 |
| 向量存储 | pgvector + halfvec | 半精度节省存储，HNSW 高效检索 |

### 16.2 UUID v7 决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 主键类型 | UUID v7 | 时间有序，避免 B-tree 页分裂 |
| 替代方案 | UUID v4 | 随机导致索引碎片化，性能下降 |
| 替代方案 | BIGSERIAL | 不适合分布式，暴露业务量 |

### 16.3 分区策略决策

| 决策 | 选择 | 理由 |
|------|------|------|
| action_records 分区 | RANGE（按月） | 时间序列，便于归档 |
| memory_episodes 分区 | HASH（16 分区） | 按角色分散，避免热点 |
| HASH 分区数 | 16（固定） | 扩展需全表重分布，设计时预留容量 |
| BRIN 索引 | 移除 | 分区裁剪已足够，BRIN 冗余 |

### 16.4 embedding 维度决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 向量类型 | halfvec | 半精度，存储效率 2 倍 |
| 维度 | 2048 | 匹配 embedding 模型输出 |
| 索引 | HNSW + halfvec_cosine_ops | 高召回率 + 高效检索 |
| 索引参数 | m=16, ef_construction=128 | 平衡召回率与构建速度 |

### 16.5 工具系统决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 工具架构 | 本地 async 函数 | 消除 HTTP/SSE 网络开销 |
| 替代方案 | 独立 MCP Server | 部署复杂、网络开销大 |
| 决策模式 | ReAct（最多 3 轮） | 支持工具调用 + 结果回灌 |
| 工具启用控制 | Redis Hash（fail-open） | 未配置默认启用 |

### 16.6 群聊决策决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 默认行为 | 智能回复（`at_only=false`） | 提升群聊活跃度 |
| 决策层数 | 4 层 | @检测→关键词→启发式→LLM |
| LLM 判断回复 | 直接回复（不受 cap） | LLM 判断更准确 |
| 启发式 cap | 0.7 | 限制随机回复 |
| 兜底概率 | 15%（不回复时）/ 30%（错误时） | 避免冷场 |

### 16.7 成本控制决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 日预算 | $10 | 控制成本 |
| 预算检查 | Lua 脚本原子 | 多实例并发安全 |
| 熔断阈值 | 5 次连续失败 | 避免雪崩 |
| 熔断恢复 | 60s 后 HALF_OPEN | 试探性恢复 |

### 16.8 安全决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 安全约束传递 | SystemMessage | 优先级高于 HumanMessage |
| 用户输入消毒 | PromptGuard | 检测 17 个危险模式 |
| 用户消息包装 | 边界标记 | 防止内容逃逸 |
| 认证范围 | 仅 /api/ | /health /metrics /ws/ 免认证 |

### 16.9 迁移策略决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 迁移策略 | Upgrade-Only | 避免回滚造成数据丢失 |
| down_revision | 仅 `raise RuntimeError` | 强制不回滚 |
| 分区预创建 | `pre_create_partitions(3)` | 避免月初写入报错 |
| 异常处理 | 具体异常类型 | 不用 `WHEN OTHERS` 掩盖错误 |

### 16.10 可观测性决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 日志格式 | JSON（structlog） | 结构化，便于检索 |
| 指标中间件 | 纯 ASGI | 兼容 WebSocket |
| LLM 追踪 | Langfuse | LLM 专用（prompt/cost） |
| 分布式 Trace | OpenTelemetry | 标准，支持 Jaeger |
| 日志聚合 | Loki + Alloy | 轻量级，与 Grafana 集成 |

---

## 附录 A：术语速查表

| 术语 | 说明 |
|------|------|
| **Action** | 角色可执行的行为单元（如 sleep / eat / work） |
| **Agent** | 具有决策能力的 AI 角色 |
| **Character Tick** | 单个角色的行为闭环（感知→决策→执行→记忆→反思） |
| **CircuitBreaker** | 熔断器，连续失败后拒绝调用 |
| **CTE** | Common Table Expression（SQL 公用表表达式） |
| **DecisionResult** | 角色决策结果（action_id + reason + params + tool_call） |
| **EmbeddingWorker** | 异步向量化后台任务 |
| **Episode** | 记忆事件（memory_episodes 表的单条记录） |
| **EvolutionChain** | 世界演化器链（TimeEvolver + WeatherEvolver + EventEvolver） |
| **fillfactor** | PostgreSQL 页填充因子（85 表示保留 15% 给 HOT 更新） |
| **FOR UPDATE SKIP LOCKED** | 并发安全的行锁定（跳过已锁定的行） |
| **Glassmorphism** | 毛玻璃设计风格 |
| **halfvec** | pgvector 半精度浮点向量类型 |
| **HNSW** | Hierarchical Navigable Small World（层次化导航小世界图索引） |
| **HOT Update** | Heap-Only Tuple Update（仅堆元组更新，不更新索引） |
| **Leader 选举** | Redis 分布式锁，保证 World Tick 单实例运行 |
| **Langfuse** | LLM 专用追踪平台 |
| **LangChain** | LLM 编排框架（作为传递依赖） |
| **MCP** | Model Context Protocol（已迁移为本地工具） |
| **OneBot** | QQ 机器人协议（v11/v12） |
| **OpenTelemetry** | 分布式追踪标准 |
| **partition pruning** | 分区裁剪（查询时自动过滤无关分区） |
| **pgvector** | PostgreSQL 向量扩展 |
| **pg_uuidv7** | PostgreSQL UUID v7 扩展 |
| **pg_trgm** | PostgreSQL 模糊检索扩展 |
| **precondition** | Action 前置条件（代码过滤，LLM 不能绕过） |
| **PromptGuard** | Prompt 注入防护 |
| **proactive_share_intent** | 主动分享意图（boolean） |
| **ReAct** | Reasoning + Acting（推理+行动循环） |
| **reflection** | 反思（对记忆的高层认知归纳） |
| **ReflectionService** | 反思生成服务 |
| **RuntimeConfig** | 运行时配置覆盖（Redis 存储） |
| **ToolRegistry** | 本地工具注册表 |
| **UUID v7** | 时间有序 UUID |
| **World Tick** | 世界状态推进循环 |
| **WebSocketManager** | WebSocket 连接管理器 |

---

## 附录 B：关键代码文件索引

### B.1 核心引擎

| 文件 | 说明 |
|------|------|
| `packages/backend/src/core/world/engine.py` | WorldEngine - 世界推进循环 |
| `packages/backend/src/core/character/tick.py` | CharacterTickEngine - 角色行为闭环 |
| `packages/backend/src/main.py` | FastAPI 入口 + lifespan 启动流程 |

### B.2 Action 系统

| 文件 | 说明 |
|------|------|
| `packages/backend/src/actions/__init__.py` | Action 注册（register_all） |
| `packages/backend/src/actions/registry.py` | ActionRegistry - Action 执行 |

### B.3 工具系统

| 文件 | 说明 |
|------|------|
| `packages/backend/src/tools/registry.py` | TOOL_REGISTRY - 16 个工具定义 |
| `packages/backend/src/tools/shop.py` | 商店工具（5 个） |
| `packages/backend/src/tools/knowledge.py` | 知识库工具（2 个） |
| `packages/backend/src/tools/social.py` | 社交工具（2 个） |
| `packages/backend/src/tools/world.py` | 世界工具（3 个） |
| `packages/backend/src/tools/self_info.py` | 自身信息工具（4 个） |

### B.4 记忆系统

| 文件 | 说明 |
|------|------|
| `packages/backend/src/memory/embedding_worker.py` | EmbeddingWorker - 异步向量化 |
| `packages/backend/src/memory/reflection_service.py` | ReflectionService - 反思生成 |
| `packages/backend/src/memory/diary_service.py` | DiaryService - 日记生成 |

### B.5 消息服务

| 文件 | 说明 |
|------|------|
| `packages/backend/src/messaging/service.py` | MessageService - 消息处理 |
| `packages/backend/src/messaging/websocket.py` | WebSocketManager - WS 管理 |
| `packages/backend/src/adapters/onebot.py` | OneBotAdapter - QQ 适配器 |

### B.6 LLM 客户端

| 文件 | 说明 |
|------|------|
| `packages/backend/src/llm/client.py` | LLMClient - LLM 调用 |
| `packages/backend/src/llm/prompts.py` | PromptTemplates - 模板加载 |

### B.7 成本控制

| 文件 | 说明 |
|------|------|
| `packages/backend/src/cost_control/budget_manager.py` | BudgetManager - 日预算 |
| `packages/backend/src/cost_control/circuit_breaker.py` | CircuitBreaker - 熔断器 |

### B.8 安全

| 文件 | 说明 |
|------|------|
| `packages/backend/src/security/prompt_guard.py` | PromptGuard - 注入防护 |
| `packages/backend/src/security/rate_limiter.py` | RateLimiter - 速率限制 |

### B.9 可观测性

| 文件 | 说明 |
|------|------|
| `packages/backend/src/observability/metrics.py` | Prometheus 指标 |
| `packages/backend/src/observability/tracing.py` | OpenTelemetry 追踪 |
| `packages/backend/src/observability/langfuse_tracing.py` | Langfuse LLM 追踪 |
| `packages/backend/src/observability/logging.py` | structlog 日志 |

### B.10 数据库

| 文件 | 说明 |
|------|------|
| `packages/backend/src/db/session.py` | Database - 连接池 |
| `packages/backend/src/db/repositories.py` | Repository 层 |
| `packages/backend/alembic/versions/` | 迁移脚本（0001 ~ 0008） |

### B.11 配置

| 文件 | 说明 |
|------|------|
| `packages/backend/src/config.py` | Settings - 应用配置 |
| `packages/backend/src/config_runtime.py` | RuntimeConfig - 运行时覆盖 |
| `configs/characters/*.yaml` | 角色卡配置（24 个） |
| `configs/prompts/*.yaml` | Prompt 模板 |
| `configs/world-map.yaml` | 世界地图 |
| `configs/scenes.yaml` | 场景配置 |
| `configs/events.yaml` | 事件配置 |

---

## 附录 C：Alembic 迁移版本演进

### C.1 迁移链

```text
0001_init.py
    │
    ▼
0002_optimize.py
    │
    ▼
0003_messages.py
    │
    ▼
0004_phase3_refinements.py
    │
    ▼
0005_embedding_dim_2048.py
    │
    ▼
0006_world_event_key.py
    │
    ▼
0007_character_state_history.py
    │
    ▼
0008_add_character_diaries.py (add_char_diaries)
```

### C.2 各迁移核心变更

| 版本 | 核心变更 | 关键约束 |
|------|----------|----------|
| 0001 | 初始化所有核心表 + 扩展 + 分区 | - |
| 0002 | memory_episodes 重建为 HASH 16 分区 + HNSW + 复合外键 + fillfactor 调优 | 删除 DEFAULT 分区 |
| 0003 | 新增 conversations / messages 表 + fail_count 字段 | - |
| 0004 | conversations 唯一键扩展 + CHECK 约束 + next_retry_at | - |
| 0005 | embedding `vector(1536)` → `halfvec(2048)` + HNSW 重建 | 类型变更需重建索引 |
| 0006 | world_events 新增 `event_key` + UNIQUE 约束调整 | 幂等保证 |
| 0007 | 新增 `character_state_history` 表（按月分区） | 更新 pre_create_partitions |
| 0008 | 新增 `character_diaries` + `person_memories` 表 | - |

### C.3 迁移原则

1. **Upgrade-Only**：所有 `downgrade()` 仅 `raise RuntimeError`
2. **多语句拆分**：`op.execute()` 必须拆分为单语句（PG 18 prepared statement 限制）
3. **DEFAULT 分区检查**：删除前检查数据，防止静默丢失
4. **禁用 VACUUM FULL**：阻塞表读写
5. **具体异常处理**：不用 `WHEN OTHERS` 掩盖关键错误

---

> **文档版本**：对齐 `packages/backend` 代码状态 2026-07-16
>
> **覆盖范围**：8 个 Alembic 迁移、16 个本地工具、24 个角色卡、ReAct 决策循环、OneBot v11/v12 适配器、完整可观测性栈、251 项测试
>
> **维护建议**：每次代码变更后，同步更新本文档对应章节。特别是新增表/迁移/工具时，必须更新 [§三 数据库设计](#三数据库设计)、[§七 本地工具系统](#七本地工具系统toolregistry--react)、[附录 C](#附录-calembic-迁移版本演进)。
