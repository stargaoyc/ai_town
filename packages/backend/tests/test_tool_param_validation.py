"""tools/registry.py - 工具参数类型/范围/枚举校验单测（审查 LLM-04）

覆盖：
- 数值范围：quantity 负数/超上限拦截，合法值放行
- 类型：字符串传整数拦截，bool 不被当作 int
- 枚举：conflict_type 非法取值拦截
- 未声明 spec 的参数不校验（向后兼容）
"""

from __future__ import annotations

from typing import Any

from src.tools.registry import _validate_param_types

_INT_SPEC: dict[str, Any] = {"param_specs": {"quantity": {"type": int, "min": 1, "max": 99}}}
_ENUM_SPEC: dict[str, Any] = {"param_specs": {"conflict_type": {"enum": ["argument", "misunderstanding", "betrayal"]}}}
_NO_SPEC: dict[str, Any] = {"param_specs": {}}


class TestIntRangeValidation:
    def test_valid_int_passes(self) -> None:
        assert _validate_param_types(_INT_SPEC, {"quantity": 5}) == []

    def test_negative_int_rejected(self) -> None:
        errors = _validate_param_types(_INT_SPEC, {"quantity": -5})
        assert any("不能小于" in e for e in errors)

    def test_over_max_rejected(self) -> None:
        errors = _validate_param_types(_INT_SPEC, {"quantity": 100})
        assert any("不能大于" in e for e in errors)

    def test_string_rejected(self) -> None:
        errors = _validate_param_types(_INT_SPEC, {"quantity": "5"})
        assert any("期望 int" in e for e in errors)

    def test_bool_not_treated_as_int(self) -> None:
        # bool 是 int 子类，quantity=True 不应被当作 1 通过校验
        errors = _validate_param_types(_INT_SPEC, {"quantity": True})
        assert any("布尔值" in e for e in errors)

    def test_missing_param_skipped(self) -> None:
        assert _validate_param_types(_INT_SPEC, {}) == []


class TestEnumValidation:
    def test_valid_enum_passes(self) -> None:
        assert _validate_param_types(_ENUM_SPEC, {"conflict_type": "argument"}) == []

    def test_invalid_enum_rejected(self) -> None:
        errors = _validate_param_types(_ENUM_SPEC, {"conflict_type": "foo"})
        assert any("非法取值" in e for e in errors)


class TestNoSpecBackwardCompat:
    def test_no_spec_no_validation(self) -> None:
        # 未声明 param_specs 的工具不受新校验影响（向后兼容）
        assert _validate_param_types(_NO_SPEC, {"anything": "whatever"}) == []
        assert _validate_param_types({}, {"anything": -999}) == []
