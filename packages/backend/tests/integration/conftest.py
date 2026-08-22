"""集成测试基座 - 连接真实 PostgreSQL 与 Redis

环境策略：
- 复用运行中的 PG/Redis（CI 的服务容器或本地 docker compose），不额外起 testcontainers
- PG 使用独立数据库 `ai_town_it`：每次测试会话先 DROP 再经 alembic 迁移重建 schema
  （分区表 / pgvector / 触发器都在迁移的原生 SQL 里，create_all 无法覆盖），
  会话结束删除，不污染开发数据
- Redis 使用独立 DB 15，每个测试前 flushdb

服务不可达时整组集成测试自动跳过（本地未起 Docker 也能跑纯单元测试）。
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 集成库/Redis 固定配置：优先取 CI 注入的环境变量，缺省回落到本地 docker compose 端口
_PG_HOST = os.environ.get("IT_PG_HOST", "localhost")
_PG_PORT = os.environ.get("IT_PG_PORT", "5433")
_PG_USER = os.environ.get("IT_PG_USER", "ai_town")
_PG_PASSWORD = os.environ.get("IT_PG_PASSWORD", "password")

_IT_DB_NAME = "ai_town_it"
_ADMIN_URL = f"postgresql+asyncpg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/postgres"
_IT_URL = f"postgresql+asyncpg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_IT_DB_NAME}"

_REDIS_URL = os.environ.get("IT_REDIS_URL", "redis://localhost:6379/15")


def _services_reachable() -> bool:
    for port in {int(_PG_PORT), 6379}:
        try:
            with socket.create_connection((_PG_HOST, port), timeout=1):
                pass
        except OSError:
            return False
    return True


def _skip_if_unreachable() -> None:
    if not _services_reachable():
        pytest.skip("integration services (PostgreSQL/Redis) not reachable")


def _rebuild_database() -> str:
    """重建集成数据库（DROP 库 → CREATE → alembic 迁移到 head）

    迁移链含 uuidv7()/pgvector/pg_trgm 扩展与分区表 DDL，要求 PG18 镜像。
    alembic env.py 从 src.config.settings 读连接串且自带 asyncio.run()，
    故这里必须同步执行、并临时改写 settings.database_url。
    """

    async def _drop_create() -> None:
        admin_engine = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
        try:
            async with admin_engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{_IT_DB_NAME}" WITH (FORCE)'))
                await conn.execute(text(f'CREATE DATABASE "{_IT_DB_NAME}"'))
        finally:
            await admin_engine.dispose()

    asyncio.run(_drop_create())

    from src.config import settings

    original_url = settings.database_url
    settings.database_url = _IT_URL
    try:
        alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(alembic_cfg, "head")
    finally:
        settings.database_url = original_url

    return _IT_URL


@pytest.fixture(scope="session")
def it_db_url() -> str:
    _skip_if_unreachable()
    return _rebuild_database()


@pytest_asyncio.fixture
async def it_session(it_db_url: str) -> AsyncIterator[AsyncSession]:
    """每测试一个独立事务 session（NullPool 免跨事件循环问题），测后回滚——测试间零残留"""
    engine = create_async_engine(it_db_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def it_redis() -> AsyncIterator[AsyncRedis]:
    _skip_if_unreachable()
    r = AsyncRedis.from_url(_REDIS_URL, decode_responses=True)
    await r.flushdb()
    try:
        yield r
    finally:
        await r.aclose()
