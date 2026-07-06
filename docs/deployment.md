# 部署与运维

> 本文档定义 AI Town 的部署架构、容器化、环境变量、容量规划、备份与高可用。

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
│   静态文件/CDN      │     │   World Engine + LangGraph │
└─────────────────────┘     └────────────┬──────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
           ┌────────▼────────┐  ┌────────▼────────┐  ┌──────▼──────┐
           │   PostgreSQL    │  │     Redis       │  │  对象存储   │
           │   (+pgvector)   │  │  (缓存/队列)    │  │  (MinIO)    │
           └─────────────────┘  └─────────────────┘  └─────────────┘
                    │
           ┌────────▼────────┐
           │   MCP Servers   │
           │  (多进程/Docker) │
           └─────────────────┘
```

### 组件清单

| 组件 | 镜像/版本 | 端口 | 说明 |
|------|-----------|------|------|
| Nginx | `nginx:alpine` | 80/443 | 反向代理 |
| 前端 | 自构 | 80 (容器内) | 静态文件 |
| 后端 | 自构 (Python 3.12) | 8000 | FastAPI |
| PostgreSQL | `postgres:17` + pgvector | 5432 | 主数据库 |
| Redis | `redis:7.4` | 6379 | 缓存/队列 |
| MinIO | `minio/minio` | 9000/9001 | 对象存储 |
| MCP Servers | 自构 | 8001–8005 | 工具服务 |
| Jaeger | `jaegertracing/all-in-one` | 16686 | 链路追踪 |
| Prometheus | `prom/prometheus` | 9090 | 指标 |
| Grafana | `grafana/grafana` | 3000 | 可视化 |
| OTel Collector | `otel/opentelemetry-collector` | 4318 | 收集器 |
| Langfuse | `langfuse/langfuse` | 3001 | LLM 追踪 |

---

## 二、容器化部署

### 2.1 后端 Dockerfile

```dockerfile
# packages/backend/Dockerfile
FROM python:3.12-slim AS builder

RUN pip install uv
WORKDIR /app
COPY pyproject.toml .
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 2.2 前端 Dockerfile

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

### 2.3 docker-compose.yml

```yaml
# docker-compose.yml (节选)
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg17
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

  redis:
    image: redis:7.4-alpine
    ports: ["6379:6379"]
    volumes: [redis_data:/data]

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
    ports: ["9000:9000", "9001:9001"]
    volumes: [minio_data:/data]

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

  mcp-code-executor:
    build: ./packages/mcp-servers/code-executor
    ports: ["8001:8001"]

  mcp-web-search:
    build: ./packages/mcp-servers/web-search
    ports: ["8002:8002"]

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    volumes: ["./otel-collector.yaml:/etc/otelcol/config.yaml"]
    ports: ["4318:4318"]

  jaeger:
    image: jaegertracing/all-in-one:latest
    ports: ["16686:16686"]

  prometheus:
    image: prom/prometheus
    volumes: ["./prometheus.yml:/etc/prometheus/prometheus.yml"]
    ports: ["9090:9090"]

  grafana:
    image: grafana/grafana
    ports: ["3000:3000"]
    volumes: [grafana_data:/var/lib/grafana]

  langfuse:
    image: langfuse/langfuse:latest
    env_file: .env
    ports: ["3001:3001"]

volumes:
  pg_data:
  redis_data:
  minio_data:
  grafana_data:
```

完整编排文件位于仓库根目录 `docker-compose.yml`。

---

## 三、环境变量清单

```bash
# .env.example

# ===== 数据库 =====
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_town
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10

# pgvector
EMBEDDING_DIM=1536
EMBEDDING_MODEL=text-embedding-3-small

# ===== Redis =====
REDIS_URL=redis://localhost:6379/0

# ===== 对象存储 =====
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=xxx
MINIO_SECRET_KEY=xxx
MINIO_BUCKET=ai-town

# ===== LLM 配置 =====
OPENAI_API_KEY=xxx
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_CHAT=gpt-4o-mini
MODEL_STRONG=gpt-4o
MODEL_FLASH=gpt-3.5-turbo

# ===== MCP Servers =====
MCP_CODE_SERVER=http://localhost:8001
MCP_SEARCH_SERVER=http://localhost:8002
MCP_WEATHER_SERVER=http://localhost:8003
MCP_SHOP_SERVER=http://localhost:8004
MCP_KB_SERVER=http://localhost:8005

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
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
```

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

| 方案 | 说明 |
|------|------|
| 流复制 | 1 主 + 2 从，同步复制 |
| Patroni | 自动故障转移 |
| PgBouncer | 连接池中间件 |
| 异地备份 | 每日全量 + WAL 归档到对象存储 |

### 5.2 Redis 高可用

| 方案 | 说明 |
|------|------|
| Redis Sentinel | 主从 + 哨兵自动切换 |
| Redis Cluster | 数据分片（数据量大时） |

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

**注意**：World Tick 与 Character Tick 循环需**单实例运行**（避免重复推进）。方案：

- 使用 Redis 分布式锁选举 leader，仅 leader 运行 World Tick；
- 或将引擎循环拆分为独立服务（`engine`），与 API 服务（`api`）分开部署。

### 5.4 MCP Server 扩展

各 MCP Server 无状态，可水平扩容，通过 Nginx/HAProxy 负载均衡。

---

## 六、容量规划

### 6.1 数据库

| 表 | 月增量（50 角色） | 年增量 | 存储估算 |
|----|-------------------|--------|----------|
| `action_records` | ~150 万 | ~1800 万 | ~50 GB/年 |
| `memory_episodes` | ~150 万 | ~1800 万 | ~80 GB/年（含向量） |
| `messages` | 视用户量 | — | ~10 GB/年 |
| `reflections` | ~2500 | ~3 万 | < 100 MB |
| 其他 | 稳定 | — | < 1 GB |

**建议**：PG 实例内存 ≥ 16GB，`shared_buffers` ≥ 4GB，HNSW 索引内存预留 2GB。

### 6.2 Redis

主要存储实时状态与缓存，50 角色约 50MB，连接池上限 1000。

### 6.3 对象存储

头像、生成图片、附件，按需扩容。

### 6.4 LLM 成本

| 模型 | 单价（参考） | 单次决策成本 |
|------|--------------|--------------|
| gpt-4o | $2.5/1M in, $10/1M out | ~$0.01 |
| gpt-4o-mini | $0.15/1M in, $0.6/1M out | ~$0.001 |

50 角色 × 30s/Tick × 24h = 14.4 万次决策/天。日预算约 $200（强模型）或 $20（mini）。

---

## 七、备份与恢复

### 7.1 备份策略

| 对象 | 方式 | 频率 |
|------|------|------|
| PostgreSQL | `pg_dump` 全量 + WAL 归档 | 每日全量 + 实时归档 |
| Redis | RDB 快照 + AOF | 每 10 分钟 RDB |
| MinIO | 跨区域复制 | 实时 |

### 7.2 恢复演练

- 每月一次恢复演练；
- RTO ≤ 1 小时，RPO ≤ 5 分钟。

---

## 八、监控告警

### 8.1 告警规则

| 告警 | 条件 | 严重度 |
|------|------|--------|
| PG 不可用 | `pg_up == 0` 持续 1min | Critical |
| Redis 不可用 | `redis_up == 0` 持续 1min | Critical |
| 后端错误率 | `http_5xx_rate > 1%` 持续 5min | High |
| LLM 调用失败率 | `llm_error_rate > 5%` 持续 5min | High |
| Tick 延迟 | `character_tick_p95 > 5s` 持续 10min | Medium |
| DB 连接池 | `pool_usage > 80%` 持续 5min | Medium |
| 磁盘使用 | `disk_usage > 80%` | High |
| 模块不健康 | `module_unhealthy > 0` 持续 5min | Medium |

### 8.2 告警通道

- 飞书机器人（默认）
- 邮件（严重告警）
- PagerDuty（升级）

---

## 九、相关文档

| 主题 | 文档 |
|------|------|
| 配置参考 | [config-reference.md](config-reference.md) |
| 可观测性 | [observability.md](observability.md) |
| 数据模型 | [data-model.md](data-model.md) |
| 开发指南 | [development-guide.md](development-guide.md) |
