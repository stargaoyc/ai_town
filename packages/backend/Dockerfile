# packages/backend/Dockerfile
# AI Town 后端镜像 - Python 3.13 + uv
# 多阶段构建：builder 阶段安装依赖，运行阶段仅复制 .venv 和源码

# ===== Builder 阶段 =====
FROM python:3.13-slim AS builder

# 中国网络加速：apt 换清华源（trixie 源清单在 sources.list.d/debian.sources，
# deb.debian.org 域名同时覆盖 main 与 debian-security，整域替换即可）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 包管理器（pip 走清华 PyPI 镜像）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

# uv 依赖下载走清华 PyPI 镜像（两个变量兼容不同 uv 版本）
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 先复制依赖文件（利用 Docker 层缓存）
COPY pyproject.toml uv.lock ./

# 安装依赖到 .venv（--frozen 确保与 lockfile 一致）
RUN uv sync --frozen --no-dev

# ===== 运行阶段 =====
FROM python:3.13-slim

# 中国网络加速：运行阶段 apt 同样换清华源
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
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
