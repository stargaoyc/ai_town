"""Embedding 向量列维度手动同步 CLI（应急/离线用）

用法（在 packages/backend 目录）：
    uv run python -m scripts.sync_embedding_dim

读 settings.embedding_dim（.env EMBEDDING_DIM），幂等对齐
memory_episodes / reflections 两表的 halfvec 列与 HNSW 索引。
维度一致时无操作退出；不一致时重建列并清空旧向量（由 worker 重算）。
"""

import asyncio

from src.db.embedding_dim_sync import sync_embedding_dim
from src.db.session import db


async def main() -> int:
    changes = await sync_embedding_dim(db.session)
    if not changes:
        print("维度已一致，无变更。")
        return 0
    print("执行变更：")
    for change in changes:
        print(f"  - {change}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
