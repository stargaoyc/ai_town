"""Embedding 维度调整 1536 → 2048 (halfvec)

原因：OpenRouter nvidia/llama-nemotron-embed-vl-1b-v2:free 返回 2048 维向量。
pgvector 的 halfvec 类型（半精度 float16）支持最多 4000 维 + HNSW 索引，
无需截断即可完整存储 2048 维向量。

变更内容：
1. 删除 memory_episodes 旧 HNSW 索引
2. ALTER COLUMN embedding vector(1536) → halfvec(2048)
3. 重建 HNSW 索引（halfvec_cosine_ops，2048 维在限制内）

注意：降级脚本仅 raise RuntimeError，遵循"upgrade only"原则。
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_embedding_dim_2048"
down_revision = "0004_phase3_refinements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 删除旧 HNSW 索引（类型变更需重建）
    op.execute("DROP INDEX IF EXISTS idx_mem_embedding_hnsw")

    # 2. 修改 embedding 列类型 vector(1536) → halfvec(2048)
    # pgvector halfvec 类型（float16）支持最多 4000 维 + HNSW 索引
    op.execute(
        "ALTER TABLE memory_episodes "
        "ALTER COLUMN embedding TYPE halfvec(2048) "
        "USING CASE WHEN embedding IS NOT NULL THEN embedding::halfvec(2048) ELSE NULL END"
    )

    # 3. 重建 HNSW 索引（使用 halfvec_cosine_ops）
    op.execute(
        "CREATE INDEX idx_mem_embedding_hnsw "
        "ON memory_episodes USING hnsw (embedding halfvec_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade not supported. Follow upgrade-only principle.")
