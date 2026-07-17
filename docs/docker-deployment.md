# Docker 部署指南

> 本文档详细说明 AI Town 项目的 Docker 化部署方案，涵盖镜像构建、编排启动、环境配置、多环境部署、故障排查与生产实践。

---

## 一、部署架构总览

```text
┌──────────────────────────────────────────────────────────────────┐
│                        用户访问                                   │
│          Web Browser  │  QQ (OneBot)  │  飞书 (Lark)             │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                    Nginx (前端容器)                               │
│             静态资源 / SPA路由 / API反代 / WebSocket              │
└──────────┬───────────────────────────────────┬──────────────────┘
           │                                   │
┌──────────▼──────────┐           ┌────────────▼──────────────┐
│   前端 (Nginx)      │           │     后端 (FastAPI)         │
│   静态文件服务       │           │   World Engine + APIs     │
└─────────────────────┘           │   本地工具（ToolRegistry） │
                                  └────────────┬──────────────┘
                                               │
                    ┌──────────────────────────┐
                    │                          │
           ┌────────▼────────┐      ┌──────────▼─────────┐
           │   PostgreSQL    │      │     Redis          │
           │  +pgvector      │      │  缓存/队列/锁      │
           │  +pg_uuidv7     │      │  tools:enabled     │
           └─────────────────┘      └────────────────────┘
```

> 工具已内联到后端进程（`src/tools/`）。工具启用状态存储在 Redis hash `tools:enabled`。

### 容器清单

| 容器         | 镜像                        | 端口  | 说明                         |
| ------------ | --------------------------- | ----- | ---------------------------- |
| `postgres`   | 自构 (pgvector + pg_uuidv7) | 5432  | 主数据库                     |
| `redis`      | `redis:8.0-alpine`          | 6379  | 缓存/队列/锁/工具开关        |
| `backend`    | 自构 (Python 3.13 + uv)     | 8000  | FastAPI 后端（含本地工具层） |
| `frontend`   | 自构 (Node 22 + Nginx)      | 80    | 前端静态服务                 |
| `prometheus` | `prom/prometheus`           | 9090  | 指标采集                     |
| `loki`       | `grafana/loki:3.0.0`        | 3100  | 日志聚合                     |
| `jaeger`     | `jaegertracing/all-in-one`  | 16686 | 链路追踪                     |
| `grafana`    | `grafana/grafana:12.0.0`    | 3000  | 可视化面板                   |
| `alloy`      | `grafana/alloy`             | 12345 | 日志收集器                   |

---

## 二、前置准备

### 2.1 系统要求

| 项目           | 最低要求 | 推荐          |
| -------------- | -------- | ------------- |
| CPU            | 2 核     | 4 核+         |
| 内存           | 4 GB     | 8 GB+         |
| 磁盘           | 20 GB    | 50 GB+（SSD） |
| Docker         | 24.0+    | 最新稳定版    |
| Docker Compose | v2.20+   | 最新稳定版    |

### 2.2 安装 Docker

**Linux (Ubuntu/Debian):**

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 重新登录使 docker 组生效
```

**Windows:**
安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，并启用 WSL 2 后端。

**macOS:**
安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。

### 2.3 克隆项目

```bash
git clone <repository-url> ai-town
cd ai-town
```

### 2.4 准备环境变量

```bash
cp .env.example .env
```

编辑 `.env`，**必须填写**以下配置：

```bash
# 数据库密码（生产环境必须修改）
DB_PASSWORD=your-secure-password

# LLM API Key
OPENAI_API_KEY=sk-your-api-key

# JWT 密钥（生产环境必须修改为随机字符串）
JWT_SECRET=$(openssl rand -hex 32)

# 管理员密码（生产环境必须修改）
ADMIN_PASSWORD=your-secure-admin-password
```

---

## 三、镜像构建

### 3.1 Dockerfile 说明

项目包含 3 个 Dockerfile：

| Dockerfile | 位置                           | 说明                                            |
| ---------- | ------------------------------ | ----------------------------------------------- |
| PostgreSQL | `docker/postgres/Dockerfile`   | 基于 `pgvector/pgvector:pg17`，补装 `pg_uuidv7` |
| 后端       | `packages/backend/Dockerfile`  | 多阶段构建，Python 3.13 + uv                    |
| 前端       | `packages/frontend/Dockerfile` | 多阶段构建，Node 22 + Nginx                     |


### 3.2 后端 Dockerfile（多阶段构建）

```dockerfile
# packages/backend/Dockerfile
# Builder 阶段：安装编译依赖 + uv sync
FROM python:3.13-slim AS builder
RUN apt-get update && apt-get install -y build-essential libpq-dev
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 运行阶段：仅复制 .venv 和源码
FROM python:3.13-slim
RUN apt-get update && apt-get install -y libpq5 && useradd -m -u 1000 aitown
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --chown=aitown:aitown . .
ENV PATH="/app/.venv/bin:$PATH"
USER aitown
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**关键设计**：

- 多阶段构建减小镜像体积（builder 阶段的编译工具不进入最终镜像）
- 使用 `uv sync --frozen` 确保依赖与 lockfile 完全一致
- 非 root 用户运行（`aitown`）
- `PYTHONUNBUFFERED=1` 确保日志即时输出

### 3.3 前端 Dockerfile（构建 + Nginx）

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
EXPOSE 80
```

**Nginx 配置**（`packages/frontend/nginx.conf`）：

- SPA 路由回退（`try_files $uri /index.html`）
- API 反向代理（`/api/` → `backend:8000`）
- WebSocket 反向代理（`/ws/` → `backend:8000`）
- 静态资源长期缓存（`/assets/` → `expires 1y`）

### 3.4 手动构建所有镜像

```bash
# 构建后端
docker build -t aitown/backend packages/backend/

# 构建前端
docker build -t aitown/frontend packages/frontend/

# 构建 PostgreSQL
docker build -t aitown/postgres docker/postgres/
```

---

## 四、Docker Compose 编排

### 4.1 编排文件说明

项目提供 3 个 Docker Compose 文件：

| 文件                           | 用途                                       |
| ------------------------------ | ------------------------------------------ |
| `docker-compose.yml`           | 完整生产部署（基础设施 + 应用 + 可观测性） |
| `docker-compose.infra.yml`     | 仅基础设施（本地开发用）                   |
| `docker-compose-win.infra.yml` | Windows 基础设施（路径兼容）               |

### 4.2 分层启动（Profile 机制）

`docker-compose.yml` 使用 Docker Compose Profiles 实现按需启动：

```bash
# 1. 仅启动基础设施（数据库 + 缓存）
docker compose up -d postgres redis

# 2. 启动应用层（后端 + 前端）
docker compose up -d backend frontend

# 3. 启动可观测性栈（按需）
docker compose --profile observability up -d
```


### 4.3 一键启动（最简部署）

```bash
# 复制环境变量模板
cp .env.example .env
# 编辑 .env 填写必要配置

# 启动核心服务（不含可观测性栈）
docker compose up -d

# 查看启动状态
docker compose ps

# 查看后端日志
docker compose logs -f backend
```

启动成功后：

- **前端**：http://localhost
- **后端 API 文档**：http://localhost:8000/docs
- **后端健康检查**：http://localhost:8000/health

### 4.4 数据库初始化

首次启动后需执行数据库迁移：

```bash
# 在后端容器中执行 Alembic 迁移
docker compose exec backend alembic upgrade head

# 验证扩展是否启用
docker compose exec postgres psql -U ai_town -d ai_town -c \
  "SELECT extname FROM pg_extension;"
# 应看到: vector, pg_uuidv7, pg_trgm
```

### 4.5 导入初始角色

```bash
# 导入角色卡 YAML
docker compose exec backend python -c "
import asyncio
from src.modules import CharacterImporter
from src.db.session import db

async def main():
    async with db.session() as session:
        importer = CharacterImporter(session)
        await importer.import_from_file('/app/configs/characters/kanade.yaml')
        await session.commit()
        print('角色导入成功')

asyncio.run(main())
"
```

---

## 五、环境变量配置

### 5.1 必填变量

| 变量             | 说明         | 示例            |
| ---------------- | ------------ | --------------- |
| `OPENAI_API_KEY` | LLM API Key  | `sk-xxx`        |
| `JWT_SECRET`     | JWT 签名密钥 | 随机 32 字节    |
| `ADMIN_PASSWORD` | 管理员密码   | `your-password` |
| `DB_PASSWORD`    | 数据库密码   | `your-password` |

### 5.2 Docker Compose 环境变量覆盖

`docker-compose.yml` 中 `backend` 服务会自动覆盖以下变量以使用容器网络：

```yaml
backend:
  environment:
    DATABASE_URL: postgresql+asyncpg://${DB_USER:-ai_town}:${DB_PASSWORD:-password}@postgres:5432/ai_town
    REDIS_URL: redis://redis:6379/0
```

> **注意**：`.env` 文件中的 `DATABASE_URL`、`REDIS_URL` 在容器中会被覆盖为容器网络地址。其他变量（如 `OPENAI_API_KEY`）从 `.env` 继承。

### 5.3 本地工具开关

工具已内联到后端进程，无需配置 Server URL 环境变量。每个工具命名空间（shop / knowledge / social / world / self_info）可独立启用/禁用：

- **前端 Dashboard**：设置页 `工具命名空间` 卡片 toggle 控件；
- **API 调用**：`PUT /api/v1/tools/servers/{namespace}/enabled`；
- **状态存储**：Redis hash `tools:enabled`，键为工具全名（如 `shop.buy_item`），值为 `"true"` / `"false"`，未配置时默认全部启用。

---

## 六、多环境部署

### 6.1 开发环境

```bash
# 使用基础设施编排 + 本地运行应用
docker compose -f docker-compose.infra.yml up -d

# 本地启动后端（热重载）
cd packages/backend
uv sync
uvicorn src.main:app --reload --port 8000

# 本地启动前端（热重载）
cd packages/frontend
pnpm dev
```

### 6.2 测试环境

```bash
# 使用完整编排，但限制资源
docker compose -f docker-compose.yml up -d

# 执行测试
docker compose exec backend pytest
```

### 6.3 生产环境

**生产环境建议**：

1. **使用 Docker Swarm 或 Kubernetes** 管理容器编排
2. **启用 PgBouncer** 连接池
3. **配置 TLS/SSL** 证书
4. **启用所有可观测性组件**
5. **配置定期备份**

```bash
# 生产环境启动（含可观测性栈）
docker compose --profile observability up -d

# 配置 PgBouncer（生产推荐）
# 在 docker-compose.yml 中取消 pgbouncer 服务注释
```

---

## 七、数据持久化与备份

### 7.1 数据卷说明

| 卷名              | 挂载点                     | 说明            |
| ----------------- | -------------------------- | --------------- |
| `pg_data`         | `/var/lib/postgresql/data` | PostgreSQL 数据 |
| `redis_data`      | `/data`                    | Redis 持久化    |
| `prometheus_data` | `/prometheus`              | Prometheus 指标 |
| `loki_data`       | `/loki`                    | Loki 日志       |
| `grafana_data`    | `/var/lib/grafana`         | Grafana 配置    |

### 7.2 数据库备份

```bash
# 手动备份
docker compose exec postgres pg_dump -U ai_town ai_town | gzip > backup_$(date +%Y%m%d).sql.gz

# 恢复备份
gunzip -c backup_20260101.sql.gz | docker compose exec -T postgres psql -U ai_town ai_town

# 自动备份（crontab）
# 每天凌晨 3 点备份
0 3 * * * cd /path/to/ai-town && docker compose exec postgres pg_dump -U ai_town ai_town | gzip > /backups/aitown_$(date +\%Y\%m\%d).sql.gz
```

### 7.3 Redis 备份

```bash
# 触发 RDB 快照
docker compose exec redis redis-cli BGSAVE

# 复制 RDB 文件
docker cp aitown-redis:/data/dump.rdb ./redis_backup.rdb
```

### 7.4 卷迁移

```bash
# 备份所有卷
docker run --rm -v aitown_pg_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/pg_data.tar.gz -C /data .

# 恢复卷
docker run --rm -v aitown_pg_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/pg_data.tar.gz -C /data
```

---

## 八、监控与可观测性

### 8.1 启动可观测性栈

```bash
docker compose --profile observability up -d
```

### 8.2 访问入口

| 服务       | 地址                   | 默认账号          |
| ---------- | ---------------------- | ----------------- |
| Grafana    | http://localhost:3000  | admin / admin123  |
| Prometheus | http://localhost:9090  | -                 |
| Jaeger     | http://localhost:16686 | -                 |
| Loki       | http://localhost:3100  | 通过 Grafana 查询 |

### 8.3 预置 Dashboard

Grafana 启动后自动加载 3 个预置 Dashboard（位于 `docker/observability/grafana/dashboards/`）：

| Dashboard        | 文件                          | 说明                                      |
| ---------------- | ----------------------------- | ----------------------------------------- |
| AI Town Overview | `ai-town-overview.json`       | 系统总览（Tick 状态、角色数、Redis、LLM） |
| LLM 监控         | `ai-town-llm.json`            | LLM 调用耗时、Token、成本、错误率         |
| Character Tick   | `ai-town-character-tick.json` | 角色 Tick 耗时、Action 分布、错误         |

### 8.4 日志查看

```bash
# 查看后端日志
docker compose logs -f backend

# 查看最近 100 行日志
docker compose logs --tail 100 backend

# 通过 API 查看结构化日志
curl http://localhost:8000/api/v1/admin/logs?lines=200&level=error
```

---

## 九、故障排查

### 9.1 常见问题

#### 后端无法连接数据库

```bash
# 检查 PostgreSQL 是否健康
docker compose ps postgres
# STATUS 应为 healthy

# 检查网络连通性
docker compose exec backend python -c "
import asyncio
import asyncpg
async def test():
    conn = await asyncpg.connect('postgresql://ai_town:password@postgres:5432/ai_town')
    print('Connected:', await conn.fetchval('SELECT version()'))
    await conn.close()
asyncio.run(test())
"
```

#### 前端无法访问后端 API

```bash
# 检查 Nginx 配置
docker compose exec frontend cat /etc/nginx/conf.d/default.conf

# 检查后端是否可达
docker compose exec frontend wget -qO- http://backend:8000/health
```

#### 本地工具调用失败

```bash
# 检查后端容器是否运行
docker compose ps backend

# 检查工具命名空间健康状态（本地工具为进程内调用，始终在线）
curl http://localhost:8000/api/v1/tools/servers/health

# 列出所有可用工具
curl http://localhost:8000/api/v1/tools/tools

# 手动调用工具测试（如查询商店商品列表）
curl -X POST http://localhost:8000/api/v1/tools/tools/shop.list_items/invoke \
  -H "Content-Type: application/json" \
  -d '{}'
```

#### 数据库迁移失败

```bash
# 查看当前迁移版本
docker compose exec backend alembic current

# 查看迁移历史
docker compose exec backend alembic history

# 回滚到上一版本（仅开发环境）
docker compose exec backend alembic downgrade -1
```

### 9.2 日志诊断

```bash
# 查看所有容器状态
docker compose ps

# 查看异常退出的容器日志
docker compose logs --tail 200 <service_name>

# 进入容器调试
docker compose exec backend bash

# 检查资源使用
docker stats
```

### 9.3 清理与重建

```bash
# 停止所有容器
docker compose down

# 停止并删除卷（⚠️ 会删除所有数据）
docker compose down -v

# 重新构建镜像
docker compose build --no-cache

# 重新启动
docker compose up -d
```

---

## 十、性能优化

### 10.1 镜像优化

- **多阶段构建**：Builder 阶段的编译工具不进入最终镜像
- **`.dockerignore`**：排除 `__pycache__/`、`.venv/`、`node_modules/` 等
- **层缓存**：先 `COPY pyproject.toml uv.lock` 再 `COPY .` ，依赖不变时复用缓存层

### 10.2 运行时优化

```yaml
# docker-compose.yml 中可添加资源限制
backend:
  deploy:
    resources:
      limits:
        cpus: "2"
        memory: 2G
      reservations:
        cpus: "1"
        memory: 512M
```

### 10.3 数据库优化

```bash
# 调整 PostgreSQL 配置
docker compose exec postgres psql -U ai_town -c "
  ALTER SYSTEM SET shared_buffers = '1GB';
  ALTER SYSTEM SET effective_cache_size = '4GB';
  ALTER SYSTEM SET maintenance_work_mem = '512MB';
  ALTER SYSTEM SET random_page_cost = 1.1;
  SELECT pg_reload_conf();
"
```

---

## 十一、安全加固

### 11.1 生产环境清单

- [ ] 修改默认数据库密码（`.env` 中 `DB_PASSWORD`）
- [ ] 修改 JWT 密钥（`.env` 中 `JWT_SECRET` 设为随机 32 字节）
- [ ] 修改管理员密码（`.env` 中 `ADMIN_PASSWORD`）
- [ ] 修改 Grafana 密码（`.env` 中 `GRAFANA_PASSWORD`）
- [ ] 配置 TLS/SSL 证书
- [ ] 限制端口暴露（生产环境仅暴露 80/443）
- [ ] 配置防火墙规则

### 11.2 网络隔离

```yaml
# docker-compose.yml 中可定义多个网络隔离服务
networks:
  frontend-net: # 前端 + 后端
    driver: bridge
  backend-net: # 后端 + 数据库/Redis
    driver: bridge
  observability-net: # 可观测性组件
    driver: bridge
```

### 11.3 密钥管理

生产环境建议使用 Docker Secrets 或外部密钥管理服务：

```bash
# 使用 Docker Secrets
echo "your-password" | docker secret create db_password -

# 在 docker-compose.yml 中引用
secrets:
  db_password:
    external: true
services:
  postgres:
    secrets:
      - db_password
```

---

## 十二、升级与维护

### 12.1 滚动升级

```bash
# 拉取最新代码
git pull origin main

# 重新构建镜像
docker compose build

# 滚动重启（逐个服务）
docker compose up -d --no-deps --build backend
docker compose up -d --no-deps --build frontend
```

### 12.2 数据库迁移

```bash
# 升级前备份
docker compose exec postgres pg_dump -U ai_town ai_town > backup_pre_upgrade.sql

# 执行迁移
docker compose exec backend alembic upgrade head

# 验证迁移
docker compose exec backend alembic current
```

### 12.3 健康检查

所有容器均配置了 `HEALTHCHECK`：

```bash
# 查看健康状态
docker compose ps
# STATUS 列显示 (healthy) / (unhealthy) / (health: starting)

# 手动触发健康检查
docker inspect --format='{{.State.Health.Status}}' aitown-backend
```

---

## 十三、相关文档

| 主题               | 文档                                       |
| ------------------ | ------------------------------------------ |
| 部署与运维（通用） | [deployment.md](deployment.md)             |
| 可观测性设计       | [observability.md](observability.md)       |
| 配置参考           | [config-reference.md](config-reference.md) |
| 数据模型           | [data-model.md](data-model.md)             |
| 项目不足与改进     | [gap-analysis.md](gap-analysis.md)         |
