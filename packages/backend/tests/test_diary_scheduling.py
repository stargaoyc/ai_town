"""DiaryService 世界时钟语义单元测试 - 窗口换算 / 触发矩阵 / 日期派生（round-3 review H1）
round-5 扩展：批量路径世界时间透传与幂等（H3）、素材等距采样（M1）"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from src.config import settings
from src.memory.diary_service import (
    MATERIAL_FETCH_LIMIT,
    MATERIAL_SAMPLE_SIZE,
    DiaryService,
    _derive_diary_dates,
    _diary_trigger_periods,
    _sample_material,
    _world_real_window_seconds,
)


@pytest.fixture
def default_world_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """钉住默认世界时钟参数，隔离 .env 覆盖（10 虚拟分 / 30 真实秒 → 1 世界日 = 72 真实分）"""
    monkeypatch.setattr(settings, "world_tick_seconds", 30)
    monkeypatch.setattr(settings, "world_tick_minutes", 10.0)


class TestWorldRealWindowSeconds:
    def test_day_equals_72_real_minutes_at_default_settings(self, default_world_clock: None) -> None:
        assert _world_real_window_seconds("day") == pytest.approx(72 * 60)

    def test_windows_scale_with_world_days(self, default_world_clock: None) -> None:
        day_seconds = _world_real_window_seconds("day")
        assert _world_real_window_seconds("week") == pytest.approx(day_seconds * 7)
        assert _world_real_window_seconds("month") == pytest.approx(day_seconds * 30)
        assert _world_real_window_seconds("year") == pytest.approx(day_seconds * 365)


class TestDiaryTriggerPeriods:
    def test_night_hours_trigger_day(self) -> None:
        for hour in (22, 23, 0, 5):
            world_now = datetime(2026, 8, 10, hour, 0, tzinfo=UTC)
            assert "day" in _diary_trigger_periods(world_now)

    def test_daytime_hours_do_not_trigger_day(self) -> None:
        for hour in (6, 12, 21):
            world_now = datetime(2026, 8, 10, hour, 0, tzinfo=UTC)
            assert "day" not in _diary_trigger_periods(world_now)

    def test_week_triggers_on_7th_world_day(self) -> None:
        # 第 14 世界日：%7==0 周中；%30==14、%365!=0 不触发月/年
        assert _diary_trigger_periods(datetime(2026, 1, 14, 23, 0, tzinfo=UTC)) == ["day", "week"]

    def test_month_triggers_on_30th_world_day(self) -> None:
        # 第 30 世界日：%7==2 不触发周
        assert _diary_trigger_periods(datetime(2026, 1, 30, 23, 0, tzinfo=UTC)) == ["day", "month"]

    def test_year_triggers_on_365th_world_day(self) -> None:
        # 平年末日第 365 天：%7==1、%30==5，只命中年
        assert _diary_trigger_periods(datetime(2026, 12, 31, 2, 0, tzinfo=UTC)) == ["day", "year"]


class TestDeriveDiaryDates:
    def test_diary_date_derives_from_world_now_not_wall_clock(self) -> None:
        world_now = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)
        diary_date, diary_end = _derive_diary_dates("day", world_now)
        # 幂等键 diary_date::date 与存储值都必须来自世界日期（H1：此前错用真实日期）
        assert diary_date == world_now
        assert diary_date.date() == datetime(2026, 3, 1).date()
        assert diary_end is None

    def test_period_end_backdated_by_world_days(self) -> None:
        world_now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
        _, diary_end = _derive_diary_dates("week", world_now)
        assert diary_end == datetime(2026, 3, 3, 12, 0, tzinfo=UTC)

    def test_day_period_has_no_end(self) -> None:
        _, diary_end = _derive_diary_dates("month", datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
        assert diary_end == datetime(2026, 1, 30, 0, 0, tzinfo=UTC)


class TestSampleMaterial:
    def test_short_list_returns_all_in_chronological_order(self) -> None:
        assert _sample_material(["a", "b", "c"]) == ["a", "b", "c"]

    def test_evenly_spaced_sample_covers_first_and_last_quartile(self) -> None:
        items = [f"m{i:03d}" for i in range(400)]
        sampled = _sample_material(items)
        # 旧实现 memories[-20:] 只取到 m380..m399，窗口起点 m000 永远缺席
        assert sampled == [f"m{i * 20:03d}" for i in range(MATERIAL_SAMPLE_SIZE)]


_CHAR_ID = UUID("01964000-0000-7000-8000-000000000001")
_WORLD_NOW = datetime(2026, 8, 20, 23, 30, tzinfo=UTC)
_WINDOW_START = datetime(2026, 8, 19, 21, 0, tzinfo=UTC)
StoredDates = set[tuple[str, str, str]]


class _FakeResult:
    def __init__(self, found: bool) -> None:
        self._found = found

    def fetchone(self) -> tuple[int] | None:
        return (1,) if self._found else None


class _IdempotencySession:
    """模拟 character_diaries 幂等 EXISTS：stored 即已落库的 (角色, 周期, 世界日)"""

    def __init__(self, stored: StoredDates) -> None:
        self._stored = stored

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
        key = (params["cid"], params["period"], params["world_date"].date().isoformat())
        return _FakeResult(key in self._stored)

    async def commit(self) -> None:
        return None


class _IdempotencyCtx:
    def __init__(self, stored: StoredDates) -> None:
        self._stored = stored

    async def __aenter__(self) -> _IdempotencySession:
        return _IdempotencySession(self._stored)

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _PlainSession:
    async def execute(self, stmt: Any, params: Any = None) -> None:
        return None

    async def commit(self) -> None:
        return None


class _PlainCtx:
    async def __aenter__(self) -> _PlainSession:
        return _PlainSession()

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _SpyDiaryService(DiaryService):
    """记录 generate_diary 入参，并把落库世界日写入 stored（复刻生产回落语义）"""

    def __init__(
        self,
        session_factory: Any,
        stored_dates: StoredDates,
        calls: list[tuple[datetime | None, datetime | None]],
    ) -> None:
        super().__init__(session_factory)
        self._stored_dates = stored_dates
        self.calls = calls

    async def generate_diary(
        self,
        character_id: UUID,
        character_name: str,
        period: str = "day",
        *,
        world_now: datetime | None = None,
        window_start: datetime | None = None,
    ) -> dict[str, Any] | None:
        effective_world_now = world_now or datetime.now(UTC)
        self._stored_dates.add((str(character_id), period, effective_world_now.date().isoformat()))
        self.calls.append((world_now, window_start))
        return {"title": "t", "character_id": str(character_id), "period": period}


def _patched_batch_service(
    monkeypatch: pytest.MonkeyPatch,
    stored: StoredDates,
    calls: list[tuple[datetime | None, datetime | None]],
) -> DiaryService:
    class _FakeCharacterRepository:
        def __init__(self, session: Any) -> None:
            pass

        async def get_active_characters(self) -> list[Any]:
            return [SimpleNamespace(id=_CHAR_ID, name="小铃")]

    monkeypatch.setattr("src.db.repositories.CharacterRepository", _FakeCharacterRepository)

    def factory() -> _IdempotencyCtx:
        return _IdempotencyCtx(stored)

    return _SpyDiaryService(factory, stored, calls)


class TestBatchWorldTimeForwarding:
    async def test_batch_forwards_world_now_and_window_start_to_generate_diary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[datetime | None, datetime | None]] = []
        service = _patched_batch_service(monkeypatch, set(), calls)

        summary = await service.generate_diaries_for_all_characters(
            "day", world_now=_WORLD_NOW, window_start=_WINDOW_START
        )

        assert (summary["success"], summary["skipped"], summary["failed"]) == (1, 0, 0)
        # round-5 H3 回归：批量路径缺省透传会让 diary_date 回落真实日历
        assert calls == [(_WORLD_NOW, _WINDOW_START)]

    async def test_second_call_within_same_world_day_skips_via_idempotency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stored: StoredDates = set()
        calls: list[tuple[datetime | None, datetime | None]] = []
        service = _patched_batch_service(monkeypatch, stored, calls)

        first = await service.generate_diaries_for_all_characters(
            "day", world_now=_WORLD_NOW, window_start=_WINDOW_START
        )
        second = await service.generate_diaries_for_all_characters(
            "day", world_now=_WORLD_NOW, window_start=_WINDOW_START
        )

        assert (first["success"], first["failed"]) == (1, 0)
        # 回归前 spy 收不到 world_now → 落库日期错用真实日历 → 幂等键永不命中、每轮重复生成
        assert (second["skipped"], second["success"], second["failed"]) == (1, 0, 0)
        assert len(calls) == 1


class TestGenerateDiaryMaterialSpan:
    async def test_fetches_dedicated_limit_and_samples_across_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        episodes = [SimpleNamespace(content=f"记忆{i:03d}") for i in range(400)]
        captured: dict[str, Any] = {}

        class _FakeMemoryRepository:
            def __init__(self, session: Any) -> None:
                pass

            async def get_by_character_and_time_range(
                self, character_id: UUID, start_date: datetime, end_date: datetime, limit: int = 100
            ) -> list[Any]:
                captured["limit"] = limit
                return episodes

        monkeypatch.setattr("src.db.repositories.memory_repo.MemoryRepository", _FakeMemoryRepository)

        rendered: list[str] = []

        class _StubPrompts:
            def render(self, name: str, **kwargs: Any) -> str:
                rendered.append(str(kwargs["memory_summary"]))
                return name

        class _StubLLM:
            async def structured_output(self, prompt: str, schema: Any) -> dict[str, str]:
                return {"title": "t", "content": "c", "mood": "calm"}

        service = DiaryService(session_factory=lambda: _PlainCtx(), llm_client=_StubLLM(), prompts=_StubPrompts())
        diary = await service.generate_diary(_CHAR_ID, "小铃", "day", world_now=_WORLD_NOW, window_start=_WINDOW_START)

        assert diary is not None
        assert captured["limit"] == MATERIAL_FETCH_LIMIT
        # 素材必须覆盖窗口首/中/尾（旧实现 [-20:] 只含 m380..m399）
        assert rendered[0].splitlines() == [f"- 记忆{i * 20:03d}" for i in range(MATERIAL_SAMPLE_SIZE)]
