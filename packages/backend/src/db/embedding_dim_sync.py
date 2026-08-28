"""Embedding 向量列维度同步 - 幂等运维函数

将 memory_episodes / reflections 的 halfvec 物理列维度对齐到
settings.embedding_dim（.env EMBEDDING_DIM，唯一真相源）。

设计动机：维度对齐是「配置驱动的数据维护」，不是 schema 演进——
alembic 迁移链只保留真正的结构变更（建表/加列/索引策略），
维度变更由本函数在启动时幂等执行（与 rehydration / partition_scheduler 同类）。

语义：
- 幂等：目标维度与当前物理维度一致时跳过（每次启动零开销检查一条 SQL）
- 维度变化时：DROP HNSW 索引 → 清空旧向量（语义不兼容，由 worker 重算）
  → ALTER TYPE halfvec(N) → 重建 HNSW 索引（halfvec_cosine_ops, m=16/ef=128）
- 列不存在（全新库未跑迁移）时补建列 + 索引
- 返回实际变更列表；无变更返回空列表

与 startup_checks.check_embedding_dim 配套：sync 尝试修复，check 修复失败时 fail-fast。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from structlog import get_logger

from src.config import settings

logger = get_logger(__name__)

# (表, 向量列, HNSW 索引名)：与 startup_checks._VECTOR_COLUMNS 保持同步
_VECTOR_TABLES: tuple[tuple[str, str, str], ...] = (
    ("memory_episodes", "embedding", "idx_mem_embedding_hnsw"),
    ("reflections", "embedding", "idx_reflections_embedding"),
)

_INDEX_PARAMS = "WITH (m = 16, ef_construction = 128)"


async def sync_embedding_dim(session_factory: Any) -> list[str]:
    """将全部向量列维度对齐到 settings.embedding_dim

    Args:
        session_factory: 异步会话工厂（async context manager），如 db.session

    Returns:
        实际执行的变更描述列表（无变更时为空列表）
    """
    target = settings.embedding_dim
    changes: list[str] = []

    async with session_factory() as session:
        for table, column, index_name in _VECTOR_TABLES:
            current = await _physical_dim(session, table, column)
            if current is None:
                await _create_column_and_index(session, table, column, index_name, target)
                changes.append(f"{table}.{column}: created halfvec({target})")
                continue
            if current == target:
                logger.info("embedding_dim_sync_skip", table=table, column=column, dim=target)
                continue  # 幂等：维度一致不动

            await _rebuild_column(session, table, column, index_name, current, target)
            changes.append(f"{table}.{column}: halfvec({current}) -> halfvec({target})")

    if changes:
        logger.info("embedding_dim_sync_done", changes=changes)
    return changes


async def _create_column_and_index(session: Any, table: str, column: str, index_name: str, target: int) -> None:
    """列不存在（全新库未跑对应迁移）：补建列 + HNSW 索引"""
    await session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} halfvec({target})"))
    await session.execute(
        text(f"CREATE INDEX {index_name} ON {table} USING hnsw ({column} halfvec_cosine_ops) {_INDEX_PARAMS}")
    )


async def _rebuild_column(session: Any, table: str, column: str, index_name: str, current: int, target: int) -> None:
    """维度不一致：DROP 索引 → 清空旧向量 → ALTER 类型 → 重建索引"""
    await session.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
    # 清空旧向量（换模型后旧向量语义失效，由 worker 重新生成）
    await session.execute(text(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL"))
    await session.execute(
        text(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE halfvec({target}) "
            f"USING CASE WHEN {column} IS NOT NULL THEN {column}::halfvec({target}) ELSE NULL END"
        )
    )
    await session.execute(
        text(f"CREATE INDEX {index_name} ON {table} USING hnsw ({column} halfvec_cosine_ops) {_INDEX_PARAMS}")
    )
    logger.info("embedding_dim_synced", table=table, column=column, from_dim=current, to_dim=target)


async def _physical_dim(session: Any, table: str, column: str) -> int | None:
    """读取列的物理维度（halfvec 维度在 typmod 上，经 pg_attribute 读取）"""
    result = await session.execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = CAST(:table AS regclass) AND a.attname = :column"
        ),
        {"table": table, "column": column},
    )
    type_str = result.scalar_one_or_none()
    if type_str is None or "(" not in str(type_str):
        return None
    return int(str(type_str).split("(")[1].rstrip(")"))
