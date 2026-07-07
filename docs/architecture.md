# 总体架构设计

> 本文档定义 AI Town 的分层架构、数据流闭环、技术栈选型，以及关键架构决策。

---

## 一、项目背景与目标

### 1.1 背景

随着大语言模型技术的快速发展，AI 陪伴产品正从简单的对话机器人向具备自主推理、工具调用和长期记忆能力的智能体（Agent）演进。本项目构建一个由 LLM 驱动的多智能体虚拟小镇，让 AI 角色拥有独立的记忆、反思、规划与社交能力，在持续运行的虚拟世界中自主生活。

### 1.2 目标

| 目标 | 说明 |
|------|------|
| 多角色共居 | 支持 10–50 个 AI 角色同时在小镇中生活、决策、交互 |
| 世界持续运行 | 世界状态推进不依赖用户消息，角色在用户不在时依然生活 |
| 记忆与演化 | 角色拥有记忆流、反思能力和长期规划，行为长期一致且可演化 |
| 可插拔能力 | 功能模块（代码执行、搜索、绘图等）可动态启用/禁用，热插拔 |
| 全链路可观测 | 每个决策周期可追踪、可审计、可调试 |
| 多端触达 | 支持 Web Dashboard、QQ、飞书等多渠道交互 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| 状态驱动 | LLM 是决策和生成能力，不是状态真相源；所有状态变更由代码执行 |
| 事实优先 | 所有可追溯事实必须落到行为记录或明确的状态字段中 |
| 闭环演化 | 行为沉淀为记忆 → 记忆影响未来决策 → 形成可追溯的生活轨迹 |
| 模块解耦 | 核心引擎与功能模块分离，模块可独立开关、独立升级 |
| 可观测性 | 埋点即契约，所有关键路径必须有 Trace 覆盖 |

---

## 二、分层架构

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           用户接入层 (Access Layer)                         │
│          Web Dashboard  │   QQ (OneBot)  │   飞书 (Lark)   │  (未来扩展)   │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                          消息服务层 (Messaging Layer)                       │
│         消息标准化  │  会话上下文管理  │  回复生成  │  主动推送调度          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                        世界引擎层 (World Engine Layer)                      │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐ │
│  │   世界状态推进       │  │   角色行为推进       │  │   多智能体调度       │ │
│  │  (World Tick)       │  │  (Character Tick)   │  │  (Multi-Agent)      │ │
│  │ 时间/天气/场景/资源  │  │ Action决策/执行/记录 │  │ 角色间通信/事件广播  │ │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                        能力层 (Capability Layer)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ 记忆系统  │ │ 反思系统  │ │ 规划系统  │ │ 情感引擎  │ │  模块管理器      │ │
│  │(pgvector)│ │(周期总结) │ │(目标分解) │ │(角色人设) │ │(开关/生命周期)   │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    MCP 工具调用层 (标准化接口)                         │ │
│  │    代码执行  │  网页搜索  │  天气查询  │  商店模拟  │  第三方API      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                   基础设施层 (Infrastructure Layer)                         │
│  ┌───────────────────────────┐  ┌─────────────┐  ┌───────────────────┐    │
│  │     PostgreSQL 17         │  │   Redis 8.0 │  │  对象存储          │    │
│  │  + pgvector (向量检索)    │  │ (缓存/队列/  │  │  (MinIO/S3)       │    │
│  │  + JSONB (灵活字段)       │  │  实时状态)   │  │                   │    │
│  │  + 分区表 (消息/行为历史) │  │             │  │                   │    │
│  │  + pg_uuidv7 (主键)       │  │             │  │                   │    │
│  └───────────────────────────┘  └─────────────┘  └───────────────────┘    │
│  └────────────────────────────────────────────────────────────────────────┐ │
│  │              可观测性 (OpenTelemetry + Langfuse)                       │ │
│  │   链路追踪(Jaeger)  │  指标(Prometheus+Grafana)  │  日志(Loki+Alloy)   │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 各层职责

| 层 | 职责 | 关键组件 |
|----|------|----------|
| 用户接入层 | 多平台消息收发与协议适配 | Web/QQ/飞书 适配器 |
| 消息服务层 | 消息标准化、会话上下文、回复生成、主动推送 | MessagingService |
| 世界引擎层 | 全局状态推进、角色行为闭环、多智能体调度 | WorldEngine、CharacterTick |
| 能力层 | 记忆/反思/规划/情感/MCP工具 | 各子系统能力模块 |
| 基础设施层 | 持久化、缓存、对象存储、可观测性 | PG / Redis / MinIO / OTel |

---

## 三、数据流闭环

### 3.1 核心闭环

```text
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   State       │────▶│   Decision    │────▶│   Action      │
│  Redis 实时态 │     │  LLM 在候选   │     │  Executor     │
│  PG 持久态    │     │  Action 中决策 │     │  执行副作用   │
│  历史经历     │     │               │     │               │
└───────▲───────┘     └───────────────┘     └───────┬───────┘
        │                                            │
        │            ┌───────────────┐              │
        └────────────│   Update      │◀──────────────┘
                     │  单一 PG 事务 │
                     │  - 状态快照    │
                     │  - ActionRecord│
                     │  - MemoryEpisode│
                     │  - 触发反思/计划│
                     └───────────────┘
```

**事务化保证**：Action 执行后的"写行为记录 + 写记忆向量 + 更新状态"在**同一个 PG 事务**中完成，任一失败则整体回滚。

### 3.2 五阶段角色 Tick

```text
① 感知环境
   ├─ 读取角色状态（位置/精力/情绪/当前行为）
   ├─ 读取世界状态（时间/天气/场景）
   ├─ 读取周围角色（同位置的其他角色）
   └─ 记忆检索（从 pgvector 检索 Top-K 相关记忆）
        ↓
② 候选 Action 过滤
   └─ 遍历所有 Action，检查 precondition，生成候选列表
        ↓
③ LLM 决策
   ├─ 输入: 角色状态 + 世界状态 + 候选列表 + 检索到的记忆
   ├─ 模型: strong 类型（复杂决策）
   └─ 输出: 结构化决策 { action, reason, params, duration }
        ↓
④ Action 执行（单一 PG 事务）
   ├─ 更新 Redis 状态（位置/精力/行为）
   ├─ 写入 action_records
   └─ 生成 memory_episodes 存入 pgvector
        ↓
⑤ 记忆沉淀与反思触发
   ├─ 检查是否触发反思（如记忆数量达到阈值）
   └─ 检查是否需要调整计划
```

---

## 四、技术栈总览

### 4.1 后端

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 语言 | Python | 3.13 | 性能与错误信息改进 |
| 包管理 | uv | 最新 | 极速依赖解析与虚拟环境管理 |
| Agent 框架 | LangGraph | 1.x | 多智能体编排 |
| Web 框架 | FastAPI | 0.118+ | 异步 API |
| 异步驱动 | asyncpg | 0.30+ | 原生 PG 异步协议 |
| ORM | SQLAlchemy | 2.0+ | 仅用于模型/迁移/简单 CRUD（混合策略，见 §5.7） |
| 迁移工具 | alembic | 1.14+ | Schema 版本化 |
| 向量扩展 | pgvector | 0.8+ | HNSW 索引 |
| UUID 扩展 | pg_uuidv7 | 最新 | 时间有序主键 |
| 可观测 SDK | OpenTelemetry | 1.28+ | Traces/Metrics/Logs |
| LLM 追踪 | Langfuse | 3.x | Prompt/Token/Cost |
| MCP SDK | mcp (官方) | 1.x | 工具调用协议 |

### 4.2 前端

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| UI 渲染 | React | 19.2 | 并发特性 + Actions |
| 类型 | TypeScript | 7.0 | 类型系统 |
| 构建 | Vite (Rolldown) | 8.1 | Rust 内核极速构建 |
| 编译器 | React Compiler | 1.0 | 自动记忆化优化，免手写 useMemo |
| 路由 | TanStack Router | 1.170 | 类型安全路由 |
| 数据 | TanStack Query | 5.101 | 服务端状态 |
| 校验 | Zod | 4.4 | 表单与运行时校验 |
| 客户端状态 | Zustand | 5.0 | 轻量全局状态 |
| 组件库 | shadcn/ui | 最新 | 可定制 Radix 基础组件 |
| 样式 | Tailwind CSS | v4 | 原子化 CSS |
| Lint | oxlint | 最新 | Rust 内核极速 lint |
| 格式化 | oxfmt | 最新 | Rust 内核（替代 Prettier） |
| 包管理 | pnpm | 11 | 硬链接节省磁盘 |
| 图表 | Recharts | 3.x | 数据可视化 |
| 动效 | Framer Motion | 12.x | 二次元风格动效 |

### 4.3 数据存储与中间件

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 主数据库 | PostgreSQL | 17 | + pgvector + pg_uuidv7 + JSONB + 分区表 |
| 缓存/实时 | Redis | 8.0 | 缓存/队列/实时状态/锁 |
| 对象存储 | MinIO | 最新 | S3 兼容，头像/图片/附件 |
| 消息队列 | Redis Streams | — | 入站消息/推送/事件总线 |
| 连接池 | PgBouncer | 1.23+ | transaction 模式 |

### 4.4 可观测性

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 链路追踪 | OpenTelemetry + Jaeger | 最新 | 分布式 Trace |
| LLM 追踪 | Langfuse | 3.x | Prompt/Token/Cost 审计 |
| 指标 | Prometheus + Grafana | 最新 | 时序指标与告警 |
| **日志聚合** | **Loki + Grafana Alloy** | **3.x** | **结构化日志聚合，与 Grafana 统一面板** |
| 采集器 | OTel Collector | 最新 | OTLP 接收/批处理/导出 |

---

## 五、关键架构决策

### 5.1 主键选型：UUID v7（时间有序 UUID）

#### 问题：为什么不用 UUID v4？

UUID v4 完全随机，作为聚簇主键时存在严重问题：

| 问题 | 影响 |
|------|------|
| B-tree 页分裂 | 随机插入导致频繁页分裂，索引碎片化 |
| 缓存局部性差 | 新数据散落在不同数据页，cache hit rate 低 |
| 写入性能衰减 | 数据量增大后插入性能明显下降（可达 30%–50%） |
| 索引体积大 | 16 字节 vs BIGINT 8 字节，索引更大 |
| WAL 写放大 | 随机写入产生更多 WAL 日志 |

#### 决策：使用 UUID v7

UUID v7 是 RFC 9562 定义的时间有序 UUID，前 48 位为毫秒级 Unix 时间戳，剩余位随机。

| 维度 | UUID v4 | UUID v7 | BIGINT IDENTITY |
|------|---------|---------|-----------------|
| 有序性 | 完全随机 | 时间单调递增 | 完全顺序 |
| 索引友好 | 差 | 好 | 最好 |
| 防枚举 | 是 | 是（部分） | 否 |
| 分布式友好 | 是 | 是 | 否（需中心化） |
| 体积 | 16 字节 | 16 字节 | 8 字节 |
| 信息泄露 | 无 | 创建时间（可接受） | 无 |

**选择 UUID v7 的理由**：
1. 索引友好——时间有序，B-tree 顺序追加，页分裂少；
2. 防枚举——后 80 位随机，无法通过 ID 遍历猜枚举；
3. 分布式友好——无需中心化 ID 生成器，多实例可独立生成；
4. 与外部 API 兼容——保持 UUID 字符串格式，前端/API 无感知。

#### 实现：pg_uuidv7 扩展

PG 18 内置 `uuidv7()` 函数；PG 17 使用 `pg_uuidv7` 扩展：

```sql
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;

-- 建表时使用
CREATE TABLE characters (
    id UUID PRIMARY KEY DEFAULT uuidv7(),
    ...
);
```

应用层兜底（Python `uuid6` 库）：

```python
from uuid6 import uuid7

class Character(Base):
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
```

#### 索引优化补充

| 表 | 主键 | 额外排序键 |
|----|------|------------|
| `action_records` | `(id, created_at)` 复合 | 按 `created_at` 分区 + 索引 |
| `messages` | `(id, created_at)` 复合 | 按 `created_at` 分区 + 索引 |
| `memory_episodes` | `id` | `(character_id, timestamp DESC)` 二级索引 |

分区表主键必须包含分区键，故用 `(id, created_at)` 复合主键。

### 5.2 时间字段统一为 TIMESTAMPTZ

#### 问题
原方案部分字段用 `BIGINT`（epoch ms）、部分用 `TIMESTAMPTZ`，导致：
- 查询时需在不同格式间转换；
- 无法直接使用 PG 时间函数（`date_trunc`、`extract`）；
- 时区处理不一致。

#### 决策：全部使用 `TIMESTAMPTZ`

- `created_at` / `updated_at` / `timestamp` / `due_at` 等业务时间字段统一为 `TIMESTAMPTZ`；
- 仅 Redis 实时态中保留 epoch ms（Redis 不支持原生时间类型）；
- 应用层通过 `datetime` 直接读写，无需手动转换。

### 5.3 向量检索：pgvector + HNSW

#### 决策：使用 pgvector 而非独立向量库

| 维度 | 独立向量库（Milvus/Chroma） | pgvector |
|------|------------------------------|----------|
| 事务一致 | 跨库，无法与结构化数据同事务 | 单 PG 事务 |
| 运维 | 多一套基础设施 | 复用 PG |
| 检索表达力 | 仅向量 | 单 SQL 完成过滤+向量+JSONB+JOIN |
| 性能（10M 级） | 略优 | HNSW p95 < 30ms，足够 |
| 性能（亿级） | 优 | 退化 |

当前数据量（50 角色 × 年千万级）下 pgvector 足够，且 `MemoryRepository` 已抽象，未来可平滑切换到独立向量库。

#### HNSW 参数调优

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `m` | 16 | 每层最大连接数，越大召回越高但内存占用增加 |
| `ef_construction` | 64 | 构建时搜索宽度，越大构建越慢但索引质量越好 |
| `ef_search` | 40 | 查询时搜索宽度（`SET hnsw.ef_search = 40`） |

#### 重新引入独立向量库的判定条件

满足任一条件时建议切换：
- 总记忆数 > 1 亿；
- HNSW 索引内存占用超过 PG `shared_buffers` 50%；
- 检索 p95 > 200ms 且调参无效。

### 5.4 World Tick 单实例运行

#### 问题
后端可水平扩展，但 World Tick 与 Character Tick 若多实例并发执行会导致重复推进。

#### 决策：Leader 选举 + 服务拆分

**方案 A（推荐，小规模）**：Redis 分布式锁选主
- 所有实例竞争 `world:leader` 锁（TTL 60s）；
- 仅持锁实例运行 World Tick 与 Character Tick；
- 锁过期后其他实例接管（故障转移）。

**方案 B（大规模）**：服务拆分
- `engine` 服务：单实例（或主备），运行 Tick 循环；
- `api` 服务：多实例，处理 API 请求；
- 通过 `docker-compose` 或 K8s 部署隔离。

### 5.5 连接池：PgBouncer

#### 决策：生产环境使用 PgBouncer

| 场景 | 是否需要 |
|------|----------|
| 开发环境 | 不需要，直接连接 |
| 生产单实例 | 可选，SQLAlchemy 池已够 |
| 生产多实例 | **必须**，避免连接数爆炸 |

PgBouncer 配置（transaction 模式）：

```ini
[databases]
ai_town = host=127.0.0.1 port=5432 dbname=ai_town

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
```

注意：transaction 模式下不支持 prepared statements，SQLAlchemy 需关闭 `prepared_statement_cache_size`。

### 5.6 向量维度可配置

不同 embedding 模型维度不同（OpenAI small=1536, large=3072, BGE=1024），向量维度应可配置：

```yaml
# config.yaml
llm:
  embedding:
    model: text-embedding-3-small
    dim: 1536
```

建表时通过 alembic 迁移动态生成 `vector(:dim)`，避免硬编码。

### 5.7 数据访问策略：ORM 与原生 SQL 混合

#### 问题：纯 ORM 还是纯原生 SQL？

本项目重度使用 pgvector、JSONB、数组、分区表、HNSW 索引，需在 ORM 与原生 SQL 间权衡：

| 维度 | 纯 ORM (SQLAlchemy) | 纯原生 SQL (asyncpg) | 混合方案 |
|------|---------------------|----------------------|----------|
| 开发效率 | 高（模型驱动） | 低（手写 SQL） | 高 |
| 类型安全 | 强 | 弱（需手动映射） | 强 |
| 迁移管理 | alembic 自动 | 手写脚本 | alembic |
| pgvector 算子 (`<=>`) | 支持（pgvector adapter） | 原生支持 | 原生 SQL |
| HNSW 索引创建 | ❌ 需 raw SQL | ✅ | 原生 SQL |
| `SET hnsw.ef_search` | ❌ 需 raw SQL | ✅ | 原生 SQL |
| 混合检索 CTE | 表达吃力，可读性差 | ✅ 清晰 | 原生 SQL |
| 分区表 DDL | ❌ 需 raw SQL | ✅ | 原生 SQL |
| 简单 CRUD | ✅ 简洁 | 啰嗦 | ORM |
| N+1 防护 | selectinload 等机制 | 手动 JOIN | ORM |
| 性能 | 中（对象水合开销） | 高 | 各取所长 |
| SQL 注入防护 | 自动参数化 | 需手动参数化 | 二者皆用 |

#### 决策：混合方案

**ORM（SQLAlchemy 2.0）负责：**
- 模型定义（`Declarative Base`）
- alembic 迁移版本管理
- 简单 CRUD（characters、module_configs、plans 等）
- 关系加载（`selectinload` 防 N+1）
- 类型推导与 IDE 提示

**原生 SQL（asyncpg / SQLAlchemy `text()`）负责：**
- 向量检索（Top-K、混合检索 CTE）
- HNSW 索引创建与 `ef_search` 调优
- 复杂分析查询（窗口函数、物化视图）
- 分区表 DDL 与维护
- 批量写入（`COPY` 协议）
- 性能热点路径

#### 代码组织

```python
# db/repositories/memory_repo.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ✅ 简单 CRUD 用 ORM
    async def add(self, ep: MemoryEpisode) -> MemoryEpisode:
        self.session.add(ep)
        await self.session.flush()
        return ep

    # ✅ 向量检索用原生 SQL (含 ef_search 调优 + 混合排序 CTE)
    async def search_hybrid(
        self, character_id: UUID, query_vec: list[float], top_k: int = 10
    ) -> list[dict]:
        stmt = text("""
            SET LOCAL hnsw.ef_search = :ef_search;
            WITH candidates AS (
                SELECT id, content, importance, timestamp,
                       1 - (embedding <=> :q_vec) AS sim_score
                FROM memory_episodes
                WHERE character_id = :cid
                ORDER BY embedding <=> :q_vec
                LIMIT :limit
            )
            SELECT id, content,
                   sim_score * 0.6 + importance * 0.05
                   + EXTRACT(EPOCH FROM (now() - timestamp)) / 86400.0 * (-0.05) AS final_score
            FROM candidates
            ORDER BY final_score DESC
            LIMIT :top_k;
        """)
        result = await self.session.execute(stmt, {
            "cid": character_id, "q_vec": str(query_vec),
            "limit": top_k * 3, "top_k": top_k, "ef_search": 40,
        })
        return [dict(r._mapping) for r in result]
```

#### 收益

- 向量与复杂查询**零抽象损失**，性能等同纯 asyncpg；
- 简单 CRUD 享受 ORM 的类型安全与迁移管理；
- HNSW/分区/CTE 等高级特性可直接使用，不被 ORM 限制；
- 团队心智负担可控：90% 代码用 ORM，10% 性能/向量热点用原生 SQL。

---

## 六、相关文档

| 主题 | 文档 |
|------|------|
| 角色设计 | [character-design.md](character-design.md) |
| 小镇与场景 | [town-design.md](town-design.md) |
| 世界引擎与角色 Tick | [world-engine.md](world-engine.md) |
| Action 系统与执行闭环 | [action-system.md](action-system.md) |
| 记忆系统与 pgvector | [memory-system.md](memory-system.md) |
| 模块管理与 MCP | [module-system.md](module-system.md) |
| 数据模型 DDL | [data-model.md](data-model.md) |
| 部署与高可用 | [deployment.md](deployment.md) |
