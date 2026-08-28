"""retrieval_eval 指标纯函数单测"""

from __future__ import annotations

import pytest

from src.memory.retrieval_eval import (
    evaluate_ranking,
    hit_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

# 相关文档集合（按「理想排序」设计的检索结果，验证各指标精确值）
_RELEVANT = {"doc_a", "doc_b", "doc_c"}


class TestRecallAtK:
    def test_full_recall(self) -> None:
        # 前 3 个全部命中相关 → recall=1.0
        assert recall_at_k(["doc_a", "doc_b", "doc_c", "doc_x"], _RELEVANT, k=10) == 1.0

    def test_partial_recall(self) -> None:
        # 3 个相关只命中 1 个 → 1/3
        assert recall_at_k(["doc_x", "doc_y", "doc_a"], _RELEVANT, k=10) == pytest.approx(1 / 3)

    def test_k_truncation_reduces_recall(self) -> None:
        # 相关排在第 4 位，k=3 时漏掉 → 0.0
        assert recall_at_k(["doc_x", "doc_y", "doc_z", "doc_a"], _RELEVANT, k=3) == 0.0

    def test_empty_relevant_returns_zero(self) -> None:
        assert recall_at_k(["doc_a"], [], k=10) == 0.0

    def test_empty_retrieved(self) -> None:
        assert recall_at_k([], _RELEVANT, k=10) == 0.0


class TestPrecisionAtK:
    def test_all_precision(self) -> None:
        # k=5，前 5 个命中 3 个相关 → 3/5
        assert precision_at_k(["doc_a", "doc_b", "doc_c", "doc_x", "doc_y"], _RELEVANT, k=5) == 3 / 5

    def test_perfect_top_k(self) -> None:
        # k=3，前 3 个全相关 → 3/3
        assert precision_at_k(["doc_a", "doc_b", "doc_c"], _RELEVANT, k=3) == 1.0

    def test_zero_k(self) -> None:
        assert precision_at_k(["doc_a"], _RELEVANT, k=0) == 0.0

    def test_short_result_denominator_is_k(self) -> None:
        # 返回仅 1 条且相关，k=10 → 1/10（分母是 k 不是返回长度）
        assert precision_at_k(["doc_a"], _RELEVANT, k=10) == 0.1


class TestMrr:
    def test_first_relevant_at_rank_2(self) -> None:
        assert mrr(["doc_x", "doc_a", "doc_b"], _RELEVANT) == pytest.approx(1 / 2)

    def test_first_relevant_at_rank_1(self) -> None:
        assert mrr(["doc_c", "doc_x"], _RELEVANT) == pytest.approx(1.0)

    def test_no_relevant(self) -> None:
        assert mrr(["doc_x", "doc_y"], _RELEVANT) == 0.0

    def test_empty_relevant(self) -> None:
        assert mrr(["doc_a"], []) == 0.0


class TestHitAtK:
    def test_relevant_within_k(self) -> None:
        assert hit_at_k(["doc_x", "doc_b"], _RELEVANT, k=2) == 1.0

    def test_not_within_k(self) -> None:
        assert hit_at_k(["doc_x", "doc_y", "doc_a"], _RELEVANT, k=2) == 0.0

    def test_empty_relevant(self) -> None:
        assert hit_at_k(["doc_x"], [], k=10) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking(self) -> None:
        # 相关恰好排 1,2,3 → DCG == IDCG → 1.0
        assert ndcg_at_k(["doc_a", "doc_b", "doc_c"], _RELEVANT, k=3) == pytest.approx(1.0)

    def test_imperfect_ranking_below_ideal(self) -> None:
        # 相关排在 2,3,4 位：DCG < IDCG → (0,1)
        val = ndcg_at_k(["doc_x", "doc_a", "doc_b", "doc_c"], _RELEVANT, k=10)
        assert 0.0 < val < 1.0

    def test_k_truncation(self) -> None:
        # 相关全在 k 之外 → 0.0
        assert ndcg_at_k(["doc_x", "doc_y", "doc_z"], _RELEVANT, k=3) == 0.0

    def test_empty_relevant(self) -> None:
        assert ndcg_at_k([], [], k=10) == 0.0

    def test_fewer_relevant_than_k(self) -> None:
        # 仅 2 个相关，k=10：理想排序把 2 个排前两位
        relevant2 = {"doc_a", "doc_b"}
        assert ndcg_at_k(["doc_a", "doc_b", "doc_x"], relevant2, k=10) == pytest.approx(1.0)


class TestDeduplication:
    def test_duplicate_ids_only_counted_once(self) -> None:
        # 重复 ID 不放大召回：去重保序后 doc_a、doc_b 均命中 → 2/3
        assert recall_at_k(["doc_a", "doc_a", "doc_a", "doc_b"], _RELEVANT, k=10) == pytest.approx(2 / 3)
        # doc_x 与 doc_a 均相关（doc_x 不在 _RELEVANT），命中 1 → 1/3
        assert precision_at_k(["doc_x", "doc_x", "doc_a"], _RELEVANT, k=3) == pytest.approx(1 / 3)

    def test_repeated_relevant_at_rank_1_still_single_hit(self) -> None:
        # 首位相关重复多次：MRR 与 Hit@k 不受重复影响
        assert mrr(["doc_a", "doc_a", "doc_b"], _RELEVANT) == pytest.approx(1.0)
        assert hit_at_k(["doc_a", "doc_a"], _RELEVANT, k=10) == 1.0


class TestEvaluateRanking:
    def test_returns_all_metric_keys(self) -> None:
        result = evaluate_ranking(["doc_a", "doc_b"], {"doc_a"}, k=10)
        assert set(result) == {"recall@k", "precision@k", "mrr", "hit@k", "ndcg@k"}
        assert all(isinstance(v, float) for v in result.values())

    def test_empty_relevant_all_zero(self) -> None:
        result = evaluate_ranking(["doc_a"], [], k=10)
        assert all(v == 0.0 for v in result.values())
