# packages/backend/Dockerfile
# AI Town 后端镜像 - Python 3.13 + uv
# 多阶段构建：builder 阶段安装依赖，运行阶段仅复制 .venv 和源码

# ===== Builder 阶段 =====
FROM python:3.13-slim AS builder

# 安装系统依赖（编译 psycopg2 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 包管理器
RUN pip install --no-cache-dir uv

WORKDIR /app

# 先复制依赖文件（利用 Docker 层缓存）
COPY pyproject.toml uv.lock ./

# 安装依赖到 .venv（--frozen 确保与 lockfile 一致）
RUN uv sync --frozen --no-dev

# ===== 运行阶段 =====
FROM python:3.13-slim

# 安装运行时系统依赖（libpq for asyncpg, libpq5）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 aitown

WORKDIR /app

# 从 builder 复制虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制源码
COPY --chown=aitown:aitown . .

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 切换非 root 用户
USER aitown

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=3)" || exit 1

# 启动前执行数据库迁移（alembic upgrade head），保证容器一键启动即可用；
# 多副本部署时迁移需改为独立 Job/Init 容器，避免并发 upgrade
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 1"]
