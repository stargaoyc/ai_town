# packages/backend/Dockerfile
# AI Town 后端镜像 - Python 3.13 + uv
# 多阶段构建：builder 阶段安装依赖，运行阶段仅复制 .venv 和源码
#
# 中国网络加速改为可选构建参数（P1-19）：
#   docker build --build-arg USE_CN_MIRROR=true .
# 默认走官方源，保证非中国大陆环境的开箱可构建性

# ===== Builder 阶段 =====
FROM python:3.13-slim AS builder

ARG USE_CN_MIRROR=false

# trixie 源清单在 sources.list.d/debian.sources，
# deb.debian.org 域名同时覆盖 main 与 debian-security，整域替换即可
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# pip / uv 共用同一索引变量；镜像加速时由 USE_CN_MIRROR 推导
ARG PYPI_INDEX=https://pypi.org/simple
RUN if [ "$USE_CN_MIRROR" = "true" ]; then export PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple; fi \
    && pip install --no-cache-dir -i "$PYPI_INDEX" uv

ENV UV_DEFAULT_INDEX=$PYPI_INDEX \
    UV_INDEX_URL=$PYPI_INDEX

WORKDIR /app

# 先复制依赖文件（利用 Docker 层缓存）
COPY pyproject.toml uv.lock ./

# 安装依赖到 .venv（--frozen 确保与 lockfile 一致）
RUN uv sync --frozen --no-dev

# ===== 运行阶段 =====
FROM python:3.13-slim

ARG USE_CN_MIRROR=false
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources; \
    fi \
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

# P1-20：迁移从 CMD 移入 entrypoint 并以 RUN_MIGRATIONS 门控——
# 单副本默认执行；多副本部署时附加副本设 RUN_MIGRATIONS=0，
# 或彻底改为独立 Init Job，避免并发 alembic upgrade
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
