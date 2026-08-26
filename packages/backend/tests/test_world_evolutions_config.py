"""EventEvolution / ResourceEvolution 配置加载测试（round-6 L17a/L17b）

锁定两个约定：
- 节日日历在构造时（而非模块导入时）从 configs/events.yaml 加载——import 本模块
  不得再产生文件系统副作用；
- 商品配置唯一真相源为 configs/resources.yaml，Python 侧不再有 DEFAULT_GOODS。
两者缺失/损坏均在构造期 fail-fast（引擎于 lifespan 构造演化器 = 启动期）。
"""

from pathlib import Path

import pytest

from src.core.world.evolutions import resource_evolution
from src.core.world.evolutions.event_evolution import EventEvolution, _load_festival_calendar
from src.core.world.evolutions.resource_evolution import ResourceEvolution, _load_goods_config


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestFestivalCalendarConstructionLoading:
    def test_construction_loads_repo_calendar(self) -> None:
        evolution = EventEvolution()
        assert evolution.festival_calendar[(4, 5)]["name"] == "樱花祭"
        assert evolution.festival_calendar[(4, 5)]["duration_days"] == 3

    def test_missing_calendar_fails_fast_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _load_festival_calendar(tmp_path / "missing.yaml")

    def test_invalid_date_fails_fast(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "events.yaml", 'events:\n  - id: bad\n    date: "13-99"\n')
        with pytest.raises(ValueError, match="date 非法"):
            _load_festival_calendar(path)


class TestGoodsConfigTruthSource:
    def test_construction_loads_repo_config(self) -> None:
        evolution = ResourceEvolution()
        assert set(evolution.goods.keys()) == {"food", "energy", "coffee", "book"}
        assert evolution.goods["coffee"] == {
            "base_inventory": 50,
            "base_price": 8,
            "consumption": 3,
            "restock_to": 50,
        }

    def test_default_goods_removed_from_python(self) -> None:
        assert not hasattr(resource_evolution, "DEFAULT_GOODS")

    def test_explicit_goods_override_config(self) -> None:
        goods = {"food": {"base_inventory": 1, "base_price": 2, "consumption": 0, "restock_to": 1}}
        assert ResourceEvolution(goods=goods).goods is goods

    def test_missing_config_fails_fast(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _load_goods_config(tmp_path / "missing.yaml")

    def test_missing_field_fails_fast(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "resources.yaml",
            "goods:\n  food:\n    base_inventory: 100\n",
        )
        with pytest.raises(ValueError, match="base_price"):
            _load_goods_config(path)

    def test_negative_value_fails_fast(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "resources.yaml",
            ("goods:\n  food:\n    base_inventory: -5\n    base_price: 10\n    consumption: 5\n    restock_to: 100\n"),
        )
        with pytest.raises(ValueError, match="base_inventory"):
            _load_goods_config(path)
