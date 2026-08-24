"""Plan 层级体系测试 - LLM 新建计划归一化 + daily 滚动过期（B2/B3）"""

from __future__ import annotations

from src.core.character.tick import CharacterTickEngine


class TestNormalizePlanCreates:
    def test_valid_create_normalized(self) -> None:
        out = CharacterTickEngine._normalize_plan_creates(
            [{"title": "去图书馆还书", "type": "daily", "priority": 4, "deadline": "2026-08-25T18:00:00"}]
        )
        assert len(out) == 1
        assert out[0]["title"] == "去图书馆还书"
        assert out[0]["type"] == "daily"
        assert out[0]["priority"] == 4
        assert out[0]["deadline"] is not None

    def test_defaults_and_clamps(self) -> None:
        out = CharacterTickEngine._normalize_plan_creates(
            [{"title": "无类型计划", "priority": 99}, {"title": "负优先级", "priority": -3}]
        )
        assert [p["priority"] for p in out] == [5, 1]
        assert all(p["type"] == "short_term" for p in out)
        assert all(p["deadline"] is None for p in out)

    def test_invalid_type_and_empty_title_skipped(self) -> None:
        out = CharacterTickEngine._normalize_plan_creates(
            [
                {"title": "坏类型", "type": "weekly"},
                {"title": "   "},
                {"description": "没有标题"},
                {"title": "合法计划", "type": "daily"},
            ]
        )
        assert len(out) == 1
        assert out[0]["title"] == "合法计划"

    def test_capped_at_three_per_decision(self) -> None:
        creates = [{"title": f"计划{i}"} for i in range(6)]
        assert len(CharacterTickEngine._normalize_plan_creates(creates)) == 3

    def test_bad_deadline_becomes_none(self) -> None:
        out = CharacterTickEngine._normalize_plan_creates([{"title": "x", "deadline": "下周三"}])
        assert out[0]["deadline"] is None
