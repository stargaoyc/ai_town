"""ReflectionService._parse_themes 单元测试 - 主题解析的防御语义（跨期主题归纳）"""

from __future__ import annotations

from typing import Any, cast

from src.memory.reflection_service import ReflectionService


def _parse(result: dict[str, Any]) -> list[dict[str, Any]]:
    # _parse_themes 不依赖实例状态，以空对象充任 self（测试规范 §5.2 cast 模式）
    return ReflectionService._parse_themes(cast(ReflectionService, object()), result, total=5)


class TestParseThemes:
    def test_valid_theme_with_ids(self) -> None:
        themes = _parse({"reflections": [{"summary": "社交活跃", "detail": "常与人交流", "memory_ids": [1, 3]}]})
        assert len(themes) == 1
        assert themes[0]["summary"] == "社交活跃"
        assert themes[0]["memory_ids"] == [1, 3]

    def test_out_of_range_and_non_int_ids_dropped(self) -> None:
        themes = _parse({"reflections": [{"summary": "s", "detail": "d", "memory_ids": [0, 6, "2", True, 2]}]})
        # 0/6 越界、"2" 非整数、True 是 bool——全部剔除，仅保留合法的 2
        assert themes[0]["memory_ids"] == [2]

    def test_duplicate_summary_deduped(self) -> None:
        themes = _parse(
            {
                "reflections": [
                    {"summary": "同一主题", "detail": "a", "memory_ids": [1]},
                    {"summary": "同一主题", "detail": "b", "memory_ids": [2]},
                ]
            }
        )
        assert len(themes) == 1

    def test_invalid_entries_skipped(self) -> None:
        themes = _parse({"reflections": ["junk", {"detail": "无标题"}, {"summary": ""}, 123]})
        assert themes == []

    def test_empty_result_returns_empty(self) -> None:
        assert _parse({}) == []
