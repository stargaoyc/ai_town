"""检索质量评估指标 - 纯函数（无 IO，可单测）

按业界标准定义（binary relevance）：
- Recall@k  = 前 k 个结果中命中的相关文档数 / 相关文档总数
- Precision@k = 前 k 个结果中命中的相关文档数 / k
- MRR      = 1 / 首个相关文档的排名位置（无相关则 0）
- Hit@k    = 前 k 个结果中是否存在任一相关文档（0 或 1）
- NDCG@k   = DCG@k / IDCG@k（binary relevance：rel_i ∈ {0,1}，
            DCG@k = Σ rel_i/log2(i+1)，i 从 1 起；IDCG 为理想排序下的 DCG）

约定：
- retrieved 为按分排序的文档 ID 列表（排在前面的更相关），元素为 str
- relevant  为相关文档 ID 集合
- k 截断：超出列表长度的部分不参与计算
- 相关文档为空时各指标返回 0.0（无正样本无从评估，宏观聚合时跳过）
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable


def _truncate_ids(retrieved: Collection[str], k: int) -> list[str]:
    """截断到前 k 个并去重保序（模拟系统返回的 Top-K）"""
    seen: set[str] = set()
    out: list[str] = []
    for doc_id in retrieved:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
        if len(out) >= k:
            break
    return out


def recall_at_k(retrieved: Collection[str], relevant: Collection[str], k: int) -> float:
    """Recall@k：前 k 个命中相关数 / 相关总数"""
    if not relevant:
        return 0.0
    relevant_set = set(relevant)
    top = _truncate_ids(retrieved, k)
    hits = sum(1 for doc_id in top if doc_id in relevant_set)
    return hits / len(relevant_set)


def precision_at_k(retrieved: Collection[str], relevant: Collection[str], k: int) -> float:
    """Precision@k：前 k 个命中相关数 / k"""
    if k <= 0:
        return 0.0
    relevant_set = set(relevant)
    top = _truncate_ids(retrieved, k)
    hits = sum(1 for doc_id in top if doc_id in relevant_set)
    return hits / k


def mrr(retrieved: Collection[str], relevant: Collection[str]) -> float:
    """MRR：1 / 首个相关文档位置；无相关返回 0"""
    relevant_set = set(relevant)
    for rank, doc_id in enumerate(_truncate_ids(retrieved, len(retrieved)), start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


def hit_at_k(retrieved: Collection[str], relevant: Collection[str], k: int) -> float:
    """Hit@k：前 k 个是否存在任一相关文档"""
    if not relevant:
        return 0.0
    relevant_set = set(relevant)
    top = _truncate_ids(retrieved, k)
    return 1.0 if any(doc_id in relevant_set for doc_id in top) else 0.0


def _dcg(ranked_ids: Iterable[str], relevant: set[str]) -> float:
    """DCG@k：Σ rel_i / log2(i+1)，i 从 1 起；binary relevance"""
    total = 0.0
    for idx, doc_id in enumerate(ranked_ids, start=1):
        if doc_id in relevant:
            total += 1.0 / math.log2(idx + 1)
    return total


def _ideal_dcg(relevant_count: int, k: int) -> float:
    """IDCG@k：理想排序下相关文档恰好排在 1..min(n,k) 位的 DCG"""
    n = min(relevant_count, k)
    return sum(1.0 / math.log2(i + 1) for i in range(1, n + 1))


def ndcg_at_k(retrieved: Collection[str], relevant: Collection[str], k: int) -> float:
    """NDCG@k：DCG@k / IDCG@k（理想排序 = 相关文档恰好排在 k 内最前）"""
    if not relevant:
        return 0.0
    relevant_set = set(relevant)
    top = _truncate_ids(retrieved, k)
    dcg = _dcg(top, relevant_set)

    idcg = _ideal_dcg(len(relevant_set), k)
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def evaluate_ranking(
    retrieved: Collection[str],
    relevant: Collection[str],
    k: int = 10,
) -> dict[str, float]:
    """一次检索的完整指标集（供脚本单查询与宏观聚合共用）"""
    return {
        "recall@k": recall_at_k(retrieved, relevant, k),
        "precision@k": precision_at_k(retrieved, relevant, k),
        "mrr": mrr(retrieved, relevant),
        "hit@k": hit_at_k(retrieved, relevant, k),
        "ndcg@k": ndcg_at_k(retrieved, relevant, k),
    }
