"""Embedding 向量列维度与 EMBEDDING_DIM 配置对齐

此前 memory_episodes（0005）/ reflections（0015）的 halfvec 维度硬编码 2048，
更换 embedding 模型（MODEL_EMBEDDING 输出维度变化）时需手写迁移重建列与索引。

本迁移将两表的向量列与 HNSW 索引重建到 settings.embedding_dim（.env 的
EMBEDDING_DIM，唯一真相源）。幂等：目标维度与当前物理维度一致时跳过
（不重复重建）。维度变化时：
1. DROP HNSW 索引（类型变更需重建）
2. 清空旧向量为 NULL——换模型后旧向量语义/维度与新模型不兼容，保留无意义，
   由 embedding worker 按新模型重新生成（materialized=false 自动重算）
3. ALTER COLUMN TYPE halfvec(<dim>) USING CAST
4. 重建 HNSW 索引（halfvec_cosine_ops，沿用 m=16/ef=128）

注意：迁移执行时读取 settings.embedding_dim，因此在运行 alembic 前必须已更新
.env 的 EMBEDDING_DIM。与 startup_checks.check_embedding_dim 的启动校验配套：
改 env 不跑迁移会 fail-fast 拦截。

Revision ID: 0021_embedding_dim_sync
Revises: 0020_memory_index_governance
Create Date: 2026-08-28
"""
from collections.abc import Sequence

from alembic import op

from src.config import settings

revision: str = "0021_embedding_dim_sync"
down_revision: str | None = "0020_memory_index_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# (表, 向量列, HNSW 索引名)：需要与 EMBEDDING_DIM 对齐的全部向量列
# 与 src/security/startup_checks.py 的 _VECTOR_COLUMNS 保持同步
_VECTOR_TABLES: tuple[tuple[str, str, str], ...] = (
    ("memory_episodes", "embedding", "idx_mem_embedding_hnsw"),
    ("reflections", "embedding", "idx_reflections_embedding"),
)


def _physical_dim(table: str, column: str) -> int | None:
    """读取列的物理维度（halfvec 维度在 typmod 上，经 pg_attribute 读取）"""
    from sqlalchemy import text

    row = op.get_bind().execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = CAST(:table AS regclass) AND a.attname = :column"
        ),
        {"table": table, "column": column},
    ).first()
    if row is None:
        return None
    type_str = row[0]
    if "(" not in type_str:
        return None
    return int(type_str.split("(")[1].rstrip(")"))


def upgrade() -> None:
    target = settings.embedding_dim
    for table, column, index_name in _VECTOR_TABLES:
        current = _physical_dim(table, column)
        if current is None:
            # 列不存在（全新库未跑对应迁移）：仍补建列+索引到目标维度
            op.execute(f"ALTER TABLE {table} ADD COLUMN {column} halfvec({target})")
            op.execute(
                f"CREATE INDEX {index_name} ON {table} USING hnsw "
                f"({column} halfvec_cosine_ops) WITH (m = 16, ef_construction = 128)"
            )
            continue
        if current == target:
            continue  # 幂等：维度一致不动

        # 1. DROP 旧 HNSW 索引（类型变更需重建）
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

        # 2. 清空旧向量（换模型后旧向量语义失效，由 worker 重新生成）
        op.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL")

        # 3. 修改列类型到目标维度
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE halfvec({target}) "
            f"USING CASE WHEN {column} IS NOT NULL THEN {column}::halfvec({target}) ELSE NULL END"
        )

        # 4. 重建 HNSW 索引
        op.execute(
            f"CREATE INDEX {index_name} ON {table} USING hnsw "
            f"({column} halfvec_cosine_ops) WITH (m = 16, ef_construction = 128)"
        )


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Follow upgrade-only principle.")
