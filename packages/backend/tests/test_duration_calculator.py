"""src/modules/duration/calculator.py 单元测试

覆盖 DurationCalculator 的修正因子计算与最终耗时计算逻辑。
"""

import pytest

from src.modules.duration.calculator import (
    WEATHER_DURATION_MULTIPLIER,
    DurationCalculator,
    DurationModifiers,
    Weather,
)

# ---------------------------------------------------------------------------
# _weather_multiplier
# ---------------------------------------------------------------------------


def test_weather_multiplier_sunny_outdoor() -> None:
    calc = DurationCalculator()
    assert calc._weather_multiplier(Weather.SUNNY, is_outdoor=True) == 1.0


def test_weather_multiplier_rainy_outdoor() -> None:
    calc = DurationCalculator()
    assert calc._weather_multiplier(Weather.RAINY, is_outdoor=True) == 1.2


def test_weather_multiplier_snowy_outdoor() -> None:
    calc = DurationCalculator()
    assert calc._weather_multiplier(Weather.SNOWY, is_outdoor=True) == 1.3


def test_weather_multiplier_stormy_outdoor() -> None:
    calc = DurationCalculator()
    assert calc._weather_multiplier(Weather.STORMY, is_outdoor=True) == 1.5


def test_weather_multiplier_foggy_outdoor() -> None:
    calc = DurationCalculator()
    assert calc._weather_multiplier(Weather.FOGGY, is_outdoor=True) == pytest.approx(1.15)


def test_weather_multiplier_indoor_unaffected() -> None:
    """室内场景不受天气影响，始终返回 1.0"""
    calc = DurationCalculator()
    for w in Weather:
        assert calc._weather_multiplier(w, is_outdoor=False) == 1.0


def test_weather_multiplier_string_input() -> None:
    """字符串天气自动转换为枚举"""
    calc = DurationCalculator()
    assert calc._weather_multiplier("rainy", is_outdoor=True) == 1.2
    assert calc._weather_multiplier("sunny", is_outdoor=True) == 1.0


def test_weather_multiplier_unknown_weather() -> None:
    """未知天气返回默认倍率 1.0"""
    calc = DurationCalculator()
    assert calc._weather_multiplier("hail", is_outdoor=True) == 1.0


def test_weather_multiplier_all_enums_match_table() -> None:
    """每个 Weather 枚举值都应能在 WEATHER_DURATION_MULTIPLIER 中查到"""
    calc = DurationCalculator()
    for w in Weather:
        assert calc._weather_multiplier(w, is_outdoor=True) == WEATHER_DURATION_MULTIPLIER[w]


# ---------------------------------------------------------------------------
# _crowdedness_multiplier
# ---------------------------------------------------------------------------


def test_crowdedness_multiplier_low() -> None:
    """拥挤度 <= 0.5 返回 1.0"""
    calc = DurationCalculator()
    assert calc._crowdedness_multiplier(0.0) == 1.0
    assert calc._crowdedness_multiplier(0.3) == 1.0
    assert calc._crowdedness_multiplier(0.5) == 1.0


def test_crowdedness_multiplier_high_linear() -> None:
    """拥挤度 > 0.5 线性增加"""
    calc = DurationCalculator()
    assert calc._crowdedness_multiplier(0.6) == pytest.approx(1.1)
    assert calc._crowdedness_multiplier(0.75) == pytest.approx(1.25)


def test_crowdedness_multiplier_max() -> None:
    """拥挤度 1.0 时倍率为 1.5"""
    calc = DurationCalculator()
    assert calc._crowdedness_multiplier(1.0) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# _stamina_multiplier
# ---------------------------------------------------------------------------


def test_stamina_multiplier_high() -> None:
    """体力 >= 30 返回 1.0"""
    calc = DurationCalculator()
    assert calc._stamina_multiplier(30) == 1.0
    assert calc._stamina_multiplier(50) == 1.0
    assert calc._stamina_multiplier(100) == 1.0


def test_stamina_multiplier_low_linear() -> None:
    """体力 < 30 线性增加"""
    calc = DurationCalculator()
    assert calc._stamina_multiplier(20) == pytest.approx(1.0 + (30 - 20) / 30 * 0.5)
    assert calc._stamina_multiplier(15) == pytest.approx(1.0 + (30 - 15) / 30 * 0.5)


def test_stamina_multiplier_zero() -> None:
    """体力 0 时倍率为 1.5"""
    calc = DurationCalculator()
    assert calc._stamina_multiplier(0) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# _mood_multiplier
# ---------------------------------------------------------------------------


def test_mood_multiplier_normal() -> None:
    """正常情绪返回 1.0"""
    calc = DurationCalculator()
    assert calc._mood_multiplier("happy") == 1.0
    assert calc._mood_multiplier("calm") == 1.0
    assert calc._mood_multiplier("excited") == 1.0


def test_mood_multiplier_mild_negative() -> None:
    """轻度负面情绪返回 1.1"""
    calc = DurationCalculator()
    for mood in ("tired", "sad", "anxious", "angry"):
        assert calc._mood_multiplier(mood) == 1.1


def test_mood_multiplier_severe_negative() -> None:
    """严重负面情绪返回 1.25"""
    calc = DurationCalculator()
    assert calc._mood_multiplier("sick") == 1.25
    assert calc._mood_multiplier("exhausted") == 1.25


# ---------------------------------------------------------------------------
# compute_modifiers
# ---------------------------------------------------------------------------


def test_compute_modifiers_defaults() -> None:
    """默认参数下所有修正因子均为 1.0"""
    calc = DurationCalculator()
    mods = calc.compute_modifiers()
    assert mods.weather == 1.0
    assert mods.crowdedness == 1.0
    assert mods.stamina == 1.0
    assert mods.mood == 1.0
    assert mods.total_multiplier() == 1.0


def test_compute_modifiers_all_factors() -> None:
    """多因素同时影响"""
    calc = DurationCalculator()
    mods = calc.compute_modifiers(
        weather="rainy",
        is_outdoor=True,
        crowdedness=0.8,
        stamina=10,
        mood="sick",
    )
    assert mods.weather == 1.2
    assert mods.crowdedness == pytest.approx(1.3)
    assert mods.stamina == pytest.approx(1.0 + (30 - 10) / 30 * 0.5)
    assert mods.mood == 1.25


def test_compute_modifiers_indoor_neutralizes_weather() -> None:
    """室内场景下天气修正因子为 1.0"""
    calc = DurationCalculator()
    mods = calc.compute_modifiers(weather="stormy", is_outdoor=False)
    assert mods.weather == 1.0


def test_compute_modifiers_returns_duration_modifiers() -> None:
    """返回类型为 DurationModifiers"""
    calc = DurationCalculator()
    mods = calc.compute_modifiers()
    assert isinstance(mods, DurationModifiers)


# ---------------------------------------------------------------------------
# calculate_duration
# ---------------------------------------------------------------------------


def test_calculate_duration_base_no_modifiers() -> None:
    """无修正时耗时等于基础耗时"""
    calc = DurationCalculator()
    assert calc.calculate_duration(10) == 10


def test_calculate_duration_with_modifiers() -> None:
    """修正后耗时 = base * total_multiplier（四舍五入）"""
    calc = DurationCalculator()
    # rainy 户外 1.2, stamina=100, crowd=0, mood=calm -> 1.2
    assert calc.calculate_duration(10, weather="rainy", is_outdoor=True) == 12


def test_calculate_duration_rounding() -> None:
    """四舍五入验证：1.15 * 10 = 11.5 -> int(11.5+0.5)=12"""
    calc = DurationCalculator()
    result = calc.calculate_duration(10, weather="foggy", is_outdoor=True)
    assert result == 12


def test_calculate_duration_minimum_one_minute() -> None:
    """基础耗时为 0 时返回最小值 1"""
    calc = DurationCalculator()
    assert calc.calculate_duration(0) == 1


def test_calculate_duration_all_negative_factors() -> None:
    """所有负面因素叠加"""
    calc = DurationCalculator()
    result = calc.calculate_duration(
        10,
        weather="stormy",
        is_outdoor=True,
        crowdedness=1.0,
        stamina=0,
        mood="exhausted",
    )
    # 1.5 * 1.5 * 1.5 * 1.25 = 4.21875, 10 * 4.21875 = 42.1875 -> int(42.6875) = 42
    assert result == 42


def test_calculate_duration_indoor_storm_neutralized() -> None:
    """室内暴风天气被中和"""
    calc = DurationCalculator()
    indoor = calc.calculate_duration(10, weather="stormy", is_outdoor=False)
    outdoor = calc.calculate_duration(10, weather="stormy", is_outdoor=True)
    assert indoor == 10
    assert outdoor == 15
