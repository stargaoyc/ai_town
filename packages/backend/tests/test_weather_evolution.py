"""src/core/world/evolutions/weather_evolution.py 单元测试

覆盖 WeatherEvolution 的静态方法与数据结构完整性。
"""

from src.core.world.evolutions.weather_evolution import (
    SEASON_WEIGHTS,
    WEATHER_IMPACT,
    WEATHER_TYPES,
    WeatherEvolution,
)

# ---------------------------------------------------------------------------
# 数据结构完整性
# ---------------------------------------------------------------------------


def test_weather_types_is_tuple_of_strings() -> None:
    """WEATHER_TYPES 是字符串元组"""
    assert isinstance(WEATHER_TYPES, tuple)
    assert len(WEATHER_TYPES) == 5
    for w in WEATHER_TYPES:
        assert isinstance(w, str)


def test_season_weights_covers_all_seasons() -> None:
    """SEASON_WEIGHTS 包含春夏秋冬四个季节"""
    assert set(SEASON_WEIGHTS.keys()) == {"spring", "summer", "autumn", "winter"}


def test_season_weights_length_matches_weather_types() -> None:
    """每个季节的权重元组长度与 WEATHER_TYPES 一致"""
    for season, weights in SEASON_WEIGHTS.items():
        assert isinstance(weights, tuple), f"{season} 权重应为 tuple"
        assert len(weights) == len(WEATHER_TYPES), (
            f"{season} 权重长度 {len(weights)} != 天气类型数 {len(WEATHER_TYPES)}"
        )


def test_season_weights_all_non_negative() -> None:
    """所有权重为非负整数"""
    for season, weights in SEASON_WEIGHTS.items():
        for w in weights:
            assert isinstance(w, int)
            assert w >= 0, f"{season} 存在负权重 {w}"


def test_season_weights_sum_to_100() -> None:
    """每个季节的权重总和为 100"""
    for season, weights in SEASON_WEIGHTS.items():
        assert sum(weights) == 100, f"{season} 权重总和 {sum(weights)} != 100"


def test_weather_impact_covers_all_types() -> None:
    """WEATHER_IMPACT 覆盖所有 WEATHER_TYPES"""
    for w in WEATHER_TYPES:
        assert w in WEATHER_IMPACT, f"WEATHER_IMPACT 缺少天气 {w}"


def test_weather_impact_has_required_fields() -> None:
    """每个天气影响矩阵包含 move_multiplier 与 outdoor_fail_bonus"""
    for _w, impact in WEATHER_IMPACT.items():
        assert "move_multiplier" in impact
        assert "outdoor_fail_bonus" in impact
        assert isinstance(impact["move_multiplier"], float)
        assert isinstance(impact["outdoor_fail_bonus"], float)


def test_winter_has_snow_weight() -> None:
    """冬季应有雪天权重（>0），其他季节雪天权重为 0"""
    # WEATHER_TYPES 顺序: sunny, cloudy, rainy, snowy, stormy
    snowy_index = WEATHER_TYPES.index("snowy")
    assert SEASON_WEIGHTS["winter"][snowy_index] > 0
    for season in ("spring", "summer", "autumn"):
        assert SEASON_WEIGHTS[season][snowy_index] == 0, f"{season} 不应有雪天权重"


# ---------------------------------------------------------------------------
# _pick_weather
# ---------------------------------------------------------------------------


def test_pick_weather_returns_valid_type() -> None:
    """_pick_weather 返回的天气必须在 WEATHER_TYPES 中"""
    for season in SEASON_WEIGHTS:
        weather = WeatherEvolution._pick_weather(season)
        assert weather in WEATHER_TYPES, f"{season} 返回未知天气 {weather}"


def test_pick_weather_unknown_season_falls_back_to_spring() -> None:
    """未知季节回退到 spring 权重"""
    weather = WeatherEvolution._pick_weather("nonexistent")
    assert weather in WEATHER_TYPES


def test_pick_weather_spring_never_snowy() -> None:
    """春季权重中雪天为 0，抽样不应返回 snowy"""
    for _ in range(200):
        weather = WeatherEvolution._pick_weather("spring")
        assert weather != "snowy"


def test_pick_weather_summer_never_snowy() -> None:
    """夏季权重中雪天为 0，抽样不应返回 snowy"""
    for _ in range(200):
        weather = WeatherEvolution._pick_weather("summer")
        assert weather != "snowy"


def test_pick_weather_winter_can_produce_snowy() -> None:
    """冬季权重中雪天 > 0，多次抽样应能产生 snowy"""
    results = {WeatherEvolution._pick_weather("winter") for _ in range(500)}
    assert "snowy" in results


# ---------------------------------------------------------------------------
# _temperature
# ---------------------------------------------------------------------------


def test_temperature_spring_sunny() -> None:
    """春季晴天温度 = 18"""
    assert WeatherEvolution._temperature("spring", "sunny") == 18


def test_temperature_summer_sunny() -> None:
    """夏季晴天温度 = 30"""
    assert WeatherEvolution._temperature("summer", "sunny") == 30


def test_temperature_autumn_sunny() -> None:
    """秋季晴天温度 = 15"""
    assert WeatherEvolution._temperature("autumn", "sunny") == 15


def test_temperature_winter_sunny() -> None:
    """冬季晴天温度 = 2"""
    assert WeatherEvolution._temperature("winter", "sunny") == 2


def test_temperature_rainy_reduces_by_3() -> None:
    """雨天/暴风温度比基础低 3"""
    assert WeatherEvolution._temperature("summer", "rainy") == 27
    assert WeatherEvolution._temperature("summer", "stormy") == 27


def test_temperature_snowy_reduces_by_5() -> None:
    """雪天温度比基础低 5"""
    assert WeatherEvolution._temperature("winter", "snowy") == -3


def test_temperature_unknown_season_default() -> None:
    """未知季节使用默认基础温度 20"""
    assert WeatherEvolution._temperature("mars", "sunny") == 20


def test_temperature_all_combinations_reasonable() -> None:
    """所有季节 x 天气组合温度在合理范围 [-20, 50]"""
    for season in SEASON_WEIGHTS:
        for weather in WEATHER_TYPES:
            temp = WeatherEvolution._temperature(season, weather)
            assert -20 <= temp <= 50, f"{season}/{weather} 温度 {temp} 超出合理范围"


def test_temperature_stormy_reduces_by_3() -> None:
    """暴风天气温度比基础低 3（与雨天同）"""
    base = 30  # summer
    assert WeatherEvolution._temperature("summer", "stormy") == base - 3
