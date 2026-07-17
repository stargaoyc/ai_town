# 部署与运维

> 本文档定义 AI Town 的部署架构、容器化、环境变量、容量规划、备份与高可用。
>
> 📌 **Docker 一键部署**：如需完整的 Docker Compose 编排（含多阶段构建、Nginx 反代、Profile 按需启动、生产环境配置），请参阅 [Docker 部署指南](docker-deployment.md)。本文档侧重部署架构设计与运维策略。

---

## 一、部署架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                        用户访问                                 │
│          Web Browser  │  QQ  │  飞书                           │
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
| PostgreSQL        | `pgvector/pgvector:pg17` + pg_uuidv7   | 5432        | 主数据库                |
| PgBouncer         | `edoburu/pgbouncer`                    | 6432        | 连接池                  |
| Redis             | `redis:8.0-alpine`                     | 6379        | 缓存/队列/工具开关      |
| Jaeger            | `jaegertracing/all-in-one`             | 16686       | 链路追踪                |
| Prometheus        | `prom/prometheus`                      | 9090        | 指标                    |
| Grafana           | `grafana/grafana:12.x`                 | 3000        | 可视化                  |
| OTel Collector    | `otel/opentelemetry-collector-contrib` | 4318        | 收集器                  |
| Langfuse          | `langfuse/langfuse:3`                  | 3001        | LLM 追踪                |
| **Loki**          | **`grafana/loki:3.x`**                 | **3100**    | **日志聚合**            |
| **Grafana Alloy** | **`grafana/alloy`**                    | **12345**   | **统一可观测性收集器**  |

---

## 二、容器化部署

### 2.1 PostgreSQL Dockerfile（pgvector + pg_uuidv7）

官方 `pgvector/pgvector:pg17` 镜像仅含 pgvector，需自定义镜像补装 `pg_uuidv7`：

```dockerfile
# docker/postgres/Dockerfile
FROM pgvector/pgvector:pg17

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential postgresql-server-dev-17 && \
    git clone --depth 1 https://github.com/fboulnois/pg_uuidv7.git /tmp/pg_uuidv7 && \
    cd /tmp/pg_uuidv7 && make && make install && \
    rm -rf /tmp/pg_uuidv7 && \
    apt-get purge -y git build-essential postgresql-server-dev-17 && \
    apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
```

> 应用层兜底：即使未安装 `pg_uuidv7` 扩展，Python `uuid6` 库的 `uuid7()` 也能在应用层生成 UUID v7，DB 列类型仍为 `UUID`。

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

### 2.4 docker-compose.yml

```yaml
# docker-compose.yml (节选)
version: "3.9"

services:
  postgres:
    build:
      context: ./docker/postgres # 自定义镜像: pgvector + pg_uuidv7
      dockerfile: Dockerfile
    environment:
      POSTGRES_DB: ai_town
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s

  # 生产环境推荐启用 PgBouncer (transaction 模式)
  pgbouncer:
    image: edoburu/pgbouncer:latest
    environment:
      DB_USER: ${DB_USER}
      DB_PASSWORD: ${DB_PASSWORD}
      DB_HOST: postgres
      DB_NAME: ai_town
      POOL_MODE: transaction
      MAX_CLIENT_CONN: 1000
      DEFAULT_POOL_SIZE: 25
    ports: ["6432:5432"]
    depends_on:
      postgres: { condition: service_healthy }

  redis:
    image: redis:8.0-alpine
    ports: ["6379:6379"]
    volumes: [redis_data:/data]

  backend:
    build: ./packages/backend
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_started }
    ports: ["8000:8000"]

  frontend:
    build: ./packages/frontend
    depends_on: [backend]
    ports: ["80:80"]

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    volumes: ["./otel-collector.yaml:/etc/otelcol/config.yaml"]
    ports: ["4318:4318"]

  jaeger:
    image: jaegertracing/all-in-one:1.60
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
    ports: ["16686:16686", "4318:4318"]

  prometheus:
    image: prom/prometheus:latest
    volumes: ["./docker/observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro"]
    ports: ["9090:9090"]

  loki:
    image: grafana/loki:3.0.0
    volumes: ["./docker/observability/loki-config.yml:/etc/loki/local-config.yaml:ro"]
    ports: ["3100:3100"]
    command: -config.file=/etc/loki/local-config.yaml

  grafana:
    image: grafana/grafana:12.0.0
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: admin123
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./docker/observability/grafana/datasources:/etc/grafana/provisioning/datasources:ro
      - ./docker/observability/grafana/dashboards.yml:/etc/grafana/provisioning/dashboards/dashboards.yml:ro
      - ./docker/observability/grafana/dashboards:/var/lib/grafana/dashboards:ro
    depends_on: [prometheus, loki, jaeger]

  # 统一可观测性收集器（取代 Promtail）
  alloy:
    image: grafana/alloy:latest
    volumes:
      - ./docker/observability/alloy.config.alloy:/etc/alloy/config.alloy:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    command: run --server.http.listen-addr=0.0.0.0:12345 /etc/alloy/config.alloy
    ports: ["12345:12345"]
    depends_on: [loki]

  langfuse:
    image: langfuse/langfuse:3
    env_file: .env
    ports: ["3001:3001"]

volumes:
  pg_data:
  redis_data:
  grafana_data:
  loki_data:
  prometheus_data:
```

> **实际配置文件**：可观测性组件的完整配置位于 `docker/observability/` 目录，包含 Prometheus 采集规则、Loki 存储配置、Alloy 采集管道、Grafana 数据源与 3 个预置 Dashboard（Overview / LLM / Character Tick）。本地开发使用 `docker-compose-win.infra.yml`，生产使用 `docker-compose.infra.yml`。详见 [可观测性设计](observability.md#十二部署实现docker-compose)。

---

## 三、环境变量清单

```bash
# .env.example

# ===== 数据库 =====
# 生产环境通过 PgBouncer 连接 (端口 6432), 直连 PG 用 5432
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:6432/ai_town
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
# PgBouncer transaction 模式下需关闭 prepared statements
DB_PREPARED_STATEMENT_CACHE_SIZE=0

# 主键: UUID v7 (时间有序, 索引友好)
# DB 端通过 pg_uuidv7 扩展生成, 应用层用 uuid6 库兜底

# pgvector
EMBEDDING_DIM=1536
EMBEDDING_MODEL=text-embedding-3-small

# ===== Redis =====
REDIS_URL=redis://localhost:6379/0

# ===== LLM 配置 =====
OPENAI_API_KEY=xxx
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_CHAT=gpt-4o-mini
MODEL_STRONG=gpt-4o
MODEL_FLASH=gpt-3.5-turbo

# ===== 本地工具 =====
# 工具已内联到后端进程，无需独立服务地址。
# 工具命名空间开关持久化到 Redis hash `tools:enabled`，未配置时默认全部启用。

# ===== 可观测性 =====
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_SERVICE_NAME=ai-town-backend
LANGFUSE_PUBLIC_KEY=xxx
LANGFUSE_SECRET_KEY=xxx
LANGFUSE_HOST=http://localhost:3001

# ===== 消息平台 =====
ONE_BOT_WS_URL=ws://localhost:6700
LARK_APP_ID=xxx
LARK_APP_SECRET=xxx

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
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;   -- 时间有序 UUID v7 (主键)
CREATE EXTENSION IF NOT EXISTS "vector";    -- pgvector 向量检索
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- 文本模糊检索
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

### 7.1 备份策略

| 对象       | 方式                      | 频率                |
| ---------- | ------------------------- | ------------------- |
| PostgreSQL | `pg_dump` 全量 + WAL 归档 | 每日全量 + 实时归档 |
| Redis      | RDB 快照 + AOF            | 每 10 分钟 RDB      |

### 7.2 恢复演练

- 每月一次恢复演练；
- RTO ≤ 1 小时，RPO ≤ 5 分钟。

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
