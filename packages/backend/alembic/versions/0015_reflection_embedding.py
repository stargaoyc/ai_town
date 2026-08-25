"""reflections 补充 embedding 列 + HNSW 索引

反思此前只有正文检索（recency / 关键词），无法参与语义检索。
补回 halfvec 向量列后，反思保存时即时生成 embedding（见
reflection_service._embed_saved），语义检索按余弦近邻召回；
生成失败的行 embedding 为 NULL，检索自动跳过（文档化退化，
回退 recency 路径）。

变更内容：
1. ALTER TABLE reflections ADD COLUMN embedding halfvec(2048)
2. 创建 HNSW 索引 idx_reflections_embedding（halfvec_cosine_ops，
   m=16, ef_construction=128，与 memory_episodes 0005 迁移参数一致）

维度对齐 0005：embedding 模型输出 2048 维，halfvec 半精度上限 4000 维。
reflections 为普通表（非分区），索引直接建在表上。

Revision ID: 0015_reflection_embedding
Revises: 0014_world_events_created_idx
Create Date: 2026-08-25
"""

from alembic import op

revision = "0015_reflection_embedding"
down_revision = "0014_world_events_created_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 新增向量列（存量反思行为 NULL，由后续保存路径逐步补齐）
    op.execute("ALTER TABLE reflections ADD COLUMN embedding halfvec(2048)")

    # 2. HNSW 余弦索引（参数与 0005 的 idx_mem_embedding_hnsw 一致）
    op.execute(
        "CREATE INDEX idx_reflections_embedding "
        "ON reflections USING hnsw (embedding halfvec_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )


def downgrade() -> None:
    op.drop_index("idx_reflections_embedding", table_name="reflections")
    op.execute("ALTER TABLE reflections DROP COLUMN embedding")
