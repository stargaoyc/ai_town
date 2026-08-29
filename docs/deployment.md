# 部署与运维

> 本文档定义 AI Town 的部署架构、容器化、环境变量、容量规划、备份与高可用。
>
> 📌 **Docker 一键部署**：如需完整的 Docker Compose 编排（含多阶段构建、Nginx 反代、Profile 按需启动、生产环境配置），请参阅 [Docker 部署指南](docker-deployment.md)。本文档侧重部署架构设计与运维策略。

---

## 一、部署架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                        用户访问                                 │
│          Web Browser  │  QQ  │（飞书规划中）                    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                    Nginx (反向代理/负载均衡)                     │
│             静态资源缓存 / SSL终止 / 路由分发                    │
└──────────┬─────────────────────────────┬───────────────────────┘
           │                             │
┌──────────▼──────────┐     ┌────────────▼──────────────┐
│   前端 (Vite build) │     │     后端 (FastAPI)         │
│   静态文件/CDN      │     │   World Engine + LangChain │
└─────────────────────┘     │   本地工具（ToolRegistry） │
                            └────────────┬──────────────┘
                                         │
                    ┌────────────────────┐
                    │                    │
           ┌────────▼────────┐  ┌────────▼────────┐
           │   PostgreSQL    │  │     Redis       │
           │   (+pgvector)   │  │  (缓存/队列)    │
           └─────────────────┘  └─────────────────┘
```

> 工具已内联到后端进程（`src/tools/`）。工具启用状态存储在 Redis hash `tools:enabled`。

### 组件清单

| 组件              | 镜像/版本                              | 端口        | 说明                    |
| ----------------- | -------------------------------------- | ----------- | ----------------------- |
| Nginx             | `nginx:alpine`                         | 80/443      | 反向代理                |
| 前端              | 自构 (Node 22)                         | 80 (容器内) | 静态文件                |
| 后端              | 自构 (Python 3.13)                     | 8000        | FastAPI（含本地工具层） |
| PostgreSQL        | `pgvector/pgvector:pg18`（官方镜像，PG18 内建 `uuidv7()`） | 5433 (宿主) | 主数据库 |
| Redis             | `redis:8-alpine`                       | 6379        | 缓存/队列/工具开关      |
| Jaeger            | `jaegertracing/all-in-one`             | 16686       | 链路追踪                |
| Prometheus        | `prom/prometheus`                      | 9090        | 指标                    |
| Grafana           | `grafana/grafana:12.x`                 | 3000        | 可视化                  |
| Langfuse          | `langfuse/langfuse:3`                  | 3001        | LLM 追踪（可选外部服务，自部署于 compose 之外） |
| **Loki**          | **`grafana/loki:3.x`**                 | **3100**    | **日志聚合**            |
| **Grafana Alloy** | **`grafana/alloy`**                    | **12345**   | **统一可观测性收集器**  |

> 基础设施与可观测性端口在 docker-compose.yml 中一律绑定 `127.0.0.1` 回环。

---

## 二、容器化部署

### 2.1 PostgreSQL 镜像

直接使用官方 `pgvector/pgvector:pg18` 镜像：向量检索由 pgvector 扩展提供，
UUID v7 由 PG18 内建的 `uuidv7()` 函数生成——无需再维护补装 pg_uuidv7 的自定义镜像
（历史方案 `docker/postgres/Dockerfile` 已删除）。

### 2.2 后端 Dockerfile

```dockerfile
# packages/backend/Dockerfile
FROM python:3.13-slim AS builder

RUN pip install uv
WORKDIR /app
COPY pyproject.toml .
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 2.3 前端 Dockerfile

```dockerfile
# packages/frontend/Dockerfile
FROM node:22-alpine AS builder
RUN npm install -g pnpm
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

### 2.4 docker-compose.yml（节选）

唯一编排文件为根目录 `docker-compose.yml`。关键点：官方 pgvector 镜像、
凭据 `${VAR:?}` 插值强制必填、基础设施端口绑定回环、可观测性走 profile：

```yaml
# docker-compose.yml (节选)
services:
  postgres:
    image: pgvector/pgvector:pg18
    environment:
      POSTGRES_DB: ai_town
      POSTGRES_USER: ai_town
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD_required}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5433:5432" # 基础设施端口一律绑定回环

  redis:
    image: redis:8-alpine
    command: ["redis-server", "--appendonly", "yes", "--requirepass", "${REDIS_PASSWORD:?REDIS_PASSWORD_required}"]
    ports:
      - "127.0.0.1:6379:6379"

  backend:
    build: ./packages/backend # CMD 内置 alembic upgrade head，启动即自动迁移
    environment:
      DATABASE_URL: postgresql+asyncpg://ai_town:${POSTGRES_PASSWORD:?POSTGRES_PASSWORD_required}@postgres:5432/ai_town
      REDIS_URL: redis://:${REDIS_PASSWORD:?REDIS_PASSWORD_required}@redis:6379/0
    ports:
      - "8000:8000"

  frontend:
    build: ./packages/frontend
    depends_on: [backend]
    ports:
      - "80:8080"

  prometheus:
    image: prom/prometheus:v2.53.0
    profiles: [observability]
    ports:
      - "127.0.0.1:9090:9090"

  grafana:
    image: grafana/grafana:12.0.0
    profiles: [observability]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD_required}
    ports:
      - "127.0.0.1:3000:3000"

  db-backup:
    image: pgvector/pgvector:pg18
    profiles: [backup] # 定时 pg_dump 备份，详见附：运维增补
```

> 完整服务清单与分层启动见 [Docker 部署指南](docker-deployment.md)。

> **实际配置文件**：可观测性组件的完整配置位于 `docker/observability/` 目录，包含 Prometheus 采集规则、Loki 存储配置、Alloy 采集管道、Grafana 数据源与 3 个预置 Dashboard（Overview / LLM / Character Tick）。统一使用根目录 `docker-compose.yml`（可观测性组件通过 `--profile observability` 启用）。详见 [可观测性设计](observability.md#十二部署实现docker-compose)。

---

## 三、环境变量清单

```bash
# .env.example

# ===== 数据库 =====
DATABASE_URL=postgresql+asyncpg://ai_town:password@localhost:5432/ai_town
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10

# 主键: UUID v7 (时间有序, 索引友好)
# PG18 内建 uuidv7() 函数直接生成, 应用层用 uuid6 库兜底

# pgvector
EMBEDDING_DIM=4000          # 与 halfvec 列对齐（halfvec 上限 4000，启动自动同步）
MODEL_EMBEDDING=text-embedding-3-small
EMBEDDING_MODEL_URL=        # Embedding 专用 API URL（如本地 Qwen3-Embedding-8B / OpenRouter）
EMBEDDING_MODEL_KEY=        # Embedding 专用 API Key

# ===== Redis =====
REDIS_URL=redis://localhost:6379/0

# ===== LLM 配置 =====
OPENAI_API_KEY=xxx
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_CHAT=gpt-4o-mini
MODEL_IMAGE=agnes-image-2.1-flash   # 图像生成
MODEL_VIDEO=agnes-video-v2.0        # 视频生成
LLM_FALLBACK_SOURCES=[]             # 多源 fallback（chat/embed 共用）
LLM_TIMEOUT=30
LLM_MAX_RETRIES=2

# ===== 本地工具 =====
# 工具已内联到后端进程，无需独立服务地址。
# 工具命名空间开关持久化到 Redis hash `tools:enabled`，未配置时默认全部启用。

# ===== 可观测性 =====
# 本地裸机运行填 http://localhost:4318；Docker Compose 内 backend 需填 http://jaeger:4318（服务名直连）
OTEL_ENDPOINT=http://localhost:4318
OTEL_SERVICE_NAME=ai-town-backend
LANGFUSE_PUBLIC_KEY=xxx
LANGFUSE_SECRET_KEY=xxx
LANGFUSE_HOST=http://localhost:3001

# ===== 消息平台（OneBot v11/v12）=====
ONEBOT_DEFAULT_CHARACTER_ID=
ONEBOT_SELF_ID=
ONEBOT_GROUP_AT_ONLY=false
ONEBOT_GROUP_CHARACTER_MAP={}
ONEBOT_ACCESS_TOKEN=

# ===== 鉴权 =====
JWT_SECRET=xxx
API_KEY=xxx

# ===== 世界引擎 =====
WORLD_TICK_SECONDS=30
WORLD_TICK_MINUTES=10
CHARACTER_MAX_CONCURRENT=10
```

详细配置项说明见 [配置参考](config-reference.md)。

---

## 四、数据库初始化

### 4.1 启用扩展

```sql
CREATE EXTENSION IF NOT EXISTS "vector";    -- pgvector 向量检索
-- 注意: PG 18 内建 uuidv7() 生成时间有序 UUID v7，无需第三方扩展
```

> 不再使用 `uuid-ossp`（UUID v4 随机性导致 B-tree 索引碎片化）。详见 [架构设计 - 主键选型](architecture.md#51-主键选型uuid-v7时间有序-uuid)。

### 4.2 执行迁移

```bash
cd packages/backend
alembic upgrade head
```

### 4.3 预创建分区

```bash
# 通过管理 API 预创建未来 12 个月分区
curl -X POST http://localhost:8000/api/v1/admin/partitions/precreate \
  -H "X-API-Key: $API_KEY"
```

详见 [数据模型设计](data-model.md#分区表维护)。

---

## 五、生产环境高可用

### 5.1 PostgreSQL 高可用

| 方案      | 说明                  |
| --------- | --------------------- |
| 流复制    | 1 主 + 2 从，同步复制 |
| Patroni   | 自动故障转移          |
| PgBouncer | 连接池中间件          |
| 异地备份  | 每日全量 + WAL 归档   |

### 5.2 Redis 高可用

| 方案           | 说明                   |
| -------------- | ---------------------- |
| Redis Sentinel | 主从 + 哨兵自动切换    |
| Redis Cluster  | 数据分片（数据量大时） |

### 5.3 后端水平扩展

后端无状态（状态在 PG/Redis），可水平扩容：

```text
                    ┌─────────────┐
                    │   Nginx     │
                    │  (LB/RR)    │
                    └──┬───┬───┬──┘
                       │   │   │
                ┌──────▼┐ ┌▼───┐┌▼──────┐
                │ BE-1  │ │BE-2││ BE-3  │
                └───────┘ └────┘└───────┘
```

**注意**：World Tick 与 Character Tick 循环需**单实例运行**（避免重复推进）。方案详见 [架构设计 - World Tick 单实例运行](architecture.md#54-world-tick-单实例运行)：

- **方案 A（推荐）**：Redis 分布式锁选主，仅持锁实例运行 Tick，锁过期自动故障转移；
- **方案 B**：服务拆分，`engine` 单实例运行 Tick 循环，`api` 多实例处理请求。

### 5.4 本地工具扩展

工具为进程内 async 函数调用，无独立服务进程，因此：

- **无单点故障**：随 backend 实例存活，backend 多实例时每个实例都内置完整工具集；
- **无网络开销**：直接函数调用，延迟在微秒级；
- **开关状态共享**：所有 backend 实例读取同一 Redis hash `tools:enabled`，开关变更对所有实例生效。

---

## 六、容量规划

### 6.1 数据库

| 表                | 月增量（50 角色） | 年增量   | 存储估算            |
| ----------------- | ----------------- | -------- | ------------------- |
| `action_records`  | ~150 万           | ~1800 万 | ~50 GB/年           |
| `memory_episodes` | ~150 万           | ~1800 万 | ~80 GB/年（含向量） |
| `messages`        | 视用户量          | —        | ~10 GB/年           |
| `reflections`     | ~2500             | ~3 万    | < 100 MB            |
| 其他              | 稳定              | —        | < 1 GB              |

**建议**：PG 实例内存 ≥ 16GB，`shared_buffers` ≥ 4GB，HNSW 索引内存预留 2GB。

### 6.2 Redis

主要存储实时状态与缓存，50 角色约 50MB，连接池上限 1000。

### 6.3 LLM 成本

| 模型        | 单价（参考）             | 单次决策成本 |
| ----------- | ------------------------ | ------------ |
| gpt-4o      | $2.5/1M in, $10/1M out   | ~$0.01       |
| gpt-4o-mini | $0.15/1M in, $0.6/1M out | ~$0.001      |

50 角色 × 30s/Tick × 24h = 14.4 万次决策/天。日预算约 $200（强模型）或 $20（mini）。

---

## 七、备份与恢复

### 7.1 备份策略（R4-H6 实际实现）

| 对象 | 方式 | 频率 | 服务 |
| ---- | ---- | ---- | ---- |
| PostgreSQL | `pg_dump --format=custom`（自带压缩，支持 `pg_restore --jobs` 并行恢复） | 每 `BACKUP_INTERVAL_HOURS`（默认 6h） | db-backup（profile: backup） |
| Redis | `redis-cli --rdb` 一致性 RDB 快照 + 常驻 AOF | 同上；AOF 实时落盘 ./data/redis | redis-backup（profile: backup） |

- 备份统一写入 `./data/backups`（`.part` 原子改名），按 `BACKUP_RETENTION_DAYS`（默认 14 天）自动清理；
- **无 WAL 归档/PITR**：崩溃时最多丢失一个备份间隔内的 PG 数据（RPO ≤ 6h）；
- **同主机风险**：备份与数据同宿主机存放——务必把 `./data/backups` 挂载/同步到异机或对象存储，
  否则磁盘故障会同时摧毁数据与备份。

### 7.2 恢复与演练

- 恢复命令：`pg_restore --jobs=4 --dbname=<目标库> ai_town_<ts>.dump`；
  Redis：停止实例 → 用 `.rdb` 覆盖 `./data/redis/dump.rdb` → 启动；
- 自动演练脚本：`sh packages/backend/scripts/restore_drill.sh [dump] [rdb]`
  在一次性容器中真实恢复并校验核心表行数 / RDB 可加载数据集；
- 目标：RTO ≤ 1 小时；RPO ≤ `BACKUP_INTERVAL_HOURS`（默认 6h）。

---

## 八、监控告警

### 8.1 告警规则

| 告警           | 条件                                 | 严重度   |
| -------------- | ------------------------------------ | -------- |
| PG 不可用      | `pg_up == 0` 持续 1min               | Critical |
| Redis 不可用   | `redis_up == 0` 持续 1min            | Critical |
| 后端错误率     | `http_5xx_rate > 1%` 持续 5min       | High     |
| LLM 调用失败率 | `llm_error_rate > 5%` 持续 5min      | High     |
| Tick 延迟      | `character_tick_p95 > 5s` 持续 10min | Medium   |
| DB 连接池      | `pool_usage > 80%` 持续 5min         | Medium   |
| 磁盘使用       | `disk_usage > 80%`                   | High     |
| 模块不健康     | `module_unhealthy > 0` 持续 5min     | Medium   |

### 8.2 告警通道

- 飞书机器人（默认）
- 邮件（严重告警）
- PagerDuty（升级）

---

## 九、相关文档

| 主题            | 文档                                         |
| --------------- | -------------------------------------------- |
| Docker 部署指南 | [docker-deployment.md](docker-deployment.md) |
| 配置参考        | [config-reference.md](config-reference.md)   |
| 可观测性        | [observability.md](observability.md)         |
| 数据模型        | [data-model.md](data-model.md)               |
| 开发指南        | [development-guide.md](development-guide.md) |

---

## 附：2026-08-24 运维增补

- **数据库定时备份**：`docker compose --profile backup up -d` 启用 db-backup 服务
  （pg_dump|gzip 每 BACKUP_INTERVAL_HOURS 写入 ./data/backups，保留 BACKUP_RETENTION_DAYS 天）；
- **容器日志轮转**：compose 全服务 json-file 10MB x 3（锚点 x-default-logging）；
- **冷启动恢复演练**：`cd packages/backend && uv run python scripts/cold_start_drill.py`
  （清空 Redis 世界/角色键 -> rehydrate_states() -> 校验回灌；--world-only 仅世界层）。
