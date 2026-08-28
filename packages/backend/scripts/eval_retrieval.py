"""检索质量离线评估 CLI

用法（在 packages/backend 目录）：
    uv run python -m scripts.eval_retrieval                    # 真实 embedding 模式
    uv run python -m scripts.eval_retrieval --stub             # Stub 模式（管道验证）
    uv run python -m scripts.eval_retrieval --top-k 20         # 自定义 k
    uv run python -m scripts.eval_retrieval --qrels <path>     # 自定义标注集

流程：
1. 读取标注集 YAML（默认 eval_data/retrieval_qrels.yaml）
2. 逐条 query：embed → search_hybrid（同线上检索路径）
3. 计算 Recall@k / Precision@k / MRR / Hit@k / NDCG@k
4. 输出逐查询明细 + 宏观聚合报告

模式说明：
- 默认真实模式：调用 LLMClient.embed()（依赖 embedding 模型可用）
- --stub：用 one-hot 单位向量替代真实 embedding——仅供管道验证
  （此时 sim_score 无语义，指标仅证明链路通），不用于基线决策

不进入运行时/API/Tick，独立离线工具。
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import yaml

from src.config import settings
from src.db.repositories import MemoryRepository
from src.db.session import db
from src.memory.retrieval_eval import evaluate_ranking
from src.memory.retrieval_service import RetrievalService

_DEFAULT_QRELS = Path(__file__).resolve().parent.parent / "eval_data" / "retrieval_qrels.yaml"


class _StubEmbedder:
    """Stub embedding：one-hot 单位向量（仅管道验证，无语义）"""

    async def embed(self, text: str) -> list[float]:
        vec = [0.0] * settings.embedding_dim
        vec[hash(text) % settings.embedding_dim] = 1.0
        return vec

    async def query_vector(self, query_id: int) -> list[float]:
        vec = [0.0] * settings.embedding_dim
        vec[query_id % settings.embedding_dim] = 1.0
        return vec


def _load_qrels(path: Path) -> list[dict[str, Any]]:
    """读取标注集并做基础结构校验（存在性/格式漂移由检索时校验）"""
    if not path.is_file():
        sys.exit(f"标注集不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    queries = (data or {}).get("queries")
    if not isinstance(queries, list) or not queries:
        sys.exit(f"标注集格式错误或为空: {path}")
    for q in queries:
        if not q.get("query") or "relevant_ids" not in q or not q.get("character_id"):
            sys.exit(f"标注条目缺少字段 query/relevant_ids/character_id: {q}")
        try:
            UUID(q["character_id"])
        except ValueError:
            sys.exit(f"character_id 非 UUID: {q['character_id']}")
    return queries


async def _run_query(service: RetrievalService, character_id: UUID, query: str, top_k: int) -> list[dict[str, Any]]:
    """与线上完全一致：embed + search_hybrid"""
    return await service.search(character_id, query, top_k=top_k)


async def _run_with_stub(
    service: RetrievalService,
    character_id: UUID,
    query: str,
    top_k: int,
    query_index: int,
) -> list[dict[str, Any]]:
    """Stub 管道验证：指定向量直接检索（跳过 embed 的语义性）"""
    vec = await _StubEmbedder().query_vector(query_index)
    return await service.search_with_vec(character_id, vec, top_k)


async def _main(args: argparse.Namespace) -> int:
    qrels_path = Path(args.qrels)
    queries = _load_qrels(qrels_path)
    top_k = args.top_k

    print("=" * 88)
    print(f"检索质量评估 - {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"标注集: {qrels_path}  条目数: {len(queries)}  top_k: {top_k}")
    print(f"模式: {'STUB（管道验证，无语义）' if args.stub else 'REAL（真实 embedding）'}")
    print("=" * 88)

    from src.llm.client import LLMClient

    rows: list[dict[str, Any]] = []
    skipped: list[tuple[int, str]] = []
    async with db.session() as session:
        repo = MemoryRepository(session)
        # Stub 只走 search_with_vec（不触 self.llm.embed），cast 满足构造签名
        llm = cast(LLMClient, _StubEmbedder()) if args.stub else LLMClient()
        service = RetrievalService(llm, repo)

        for idx, item in enumerate(queries):
            character_id = UUID(item["character_id"])
            query = str(item["query"])
            relevant = {str(x) for x in item["relevant_ids"]}

            try:
                if args.stub:
                    results = await _run_with_stub(service, character_id, query, top_k, idx)
                else:
                    results = await _run_query(service, character_id, query, top_k)
            except Exception as e:
                # 检索失败（如 embedding 不可用/超时/熔断）：该条跳过并记录
                skipped.append((idx, f"{query[:40]}... -> {str(e)[:120]}"))
                continue

            retrieved = [str(r["id"]) for r in results]
            metrics = evaluate_ranking(retrieved, relevant, k=top_k)
            rows.append(
                {
                    "idx": idx,
                    "character_id": str(character_id),
                    "query": query,
                    "retrieved": len(retrieved),
                    "relevant": len(relevant),
                    "hits": sum(1 for x in retrieved if x in relevant),
                    **metrics,
                }
            )

    if skipped:
        print("\n[警告] 以下条目检索失败已跳过（embedding 不可用或标注漂移）：")
        for idx, msg in skipped:
            print(f"  #{idx}: {msg}")
        print()

    if not rows:
        print("\n无任何成功检索的条目。评估未完成。")
        print("提示：--stub 可验证管道；真实模式依赖 embedding 模型可用。")
        return 1

    # 明细
    header = (
        f"{'#':<4} {'char_id':<38} {'recall@k':>9} {'precision@k':>12} "
        f"{'mrr':>6} {'hit@k':>7} {'ndcg@k':>7} {'hits':>6} {'query':<28}"
    )
    print(header)
    print("-" * 110)
    for r in rows:
        print(
            f"{r['idx']:<4} {r['character_id'][:36]:<38} "
            f"{r['recall@k']:>9.3f} {r['precision@k']:>12.3f} {r['mrr']:>6.3f} "
            f"{r['hit@k']:>7.1f} {r['ndcg@k']:>7.3f} {r['hits']:>6} "
            f"{(r['query'] or '')[:26]:<28}"
        )

    # 宏观聚合
    avg = {key: sum(r[key] for r in rows) / len(rows) for key in ("recall@k", "precision@k", "mrr", "hit@k", "ndcg@k")}
    print("-" * 110)
    print(
        f"{'AVG':<4} {'':<38} {avg['recall@k']:>9.3f} {avg['precision@k']:>12.3f} "
        f"{avg['mrr']:>6.3f} {avg['hit@k']:>7.1f} {avg['ndcg@k']:>7.3f}"
    )
    print("\n汇总：")
    for key, val in avg.items():
        print(f"  {key:<12} {val:.4f}")
    print(f"\n完成：{len(rows)}/{len(queries)} 条成功评估，{len(skipped)} 条跳过。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="记忆检索质量离线评估")
    parser.add_argument("--qrels", default=str(_DEFAULT_QRELS), help="标注集 YAML 路径")
    parser.add_argument("--top-k", type=int, default=10, help="评估截断 k（默认 10）")
    parser.add_argument("--stub", action="store_true", help="Stub 模式：one-hot 向量，仅验证管道")
    args = parser.parse_args()

    if args.top_k <= 0:
        sys.exit("top-k 必须为正整数")
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
