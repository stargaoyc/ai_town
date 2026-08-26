"""P0-7 回归测试：executor 抽象落地 + move 目标校验 + duration 契约

验证目标（docs/design-improvement-and-fixes.md P0-7 / A-5）：
- move 决策经 MovementSystem 校验，幻觉场景/不连通/未开放 → 降级为 wait
- move 校验通过后使用移动矩阵的真实耗时，位置由 executor 更新
- LLM 动态时长仅在 Action 声明 allow_dynamic_duration 时生效
- executor 返回的状态变更合并进 new_state（优先覆盖默认成本字段）
"""

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

import src.core.character.tick as tick_module
from src.actions import ActionRegistry, DecisionResult
from src.actions.base import Action, ActionCategory
from src.actions.move import build_move_action
from src.core.character.tick import CharacterTickEngine
from src.db.session import db as db_singleton
from src.llm import LLMClient, PromptTemplates
from src.modules.duration.calculator import DurationCalculator
from src.modules.movement.system import MovementResult
from src.modules.town.loader import SceneLoader
from src.modules.town.schema import Scene, SceneType, WorldMap

_CHARACTER_ID = UUID("01964000-0000-7000-8000-000000000001")


class FakeRedis:
    def __init__(self) -> None:
        self.hset_calls: list[tuple[str, dict[str, Any]]] = []
        self.hincrby_calls: list[tuple[str, str, int]] = []

    async def hget(self, key: str, field: str) -> None:
        return None

    async def hset(self, key: str, *args: Any, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        # 兼容两种调用形态：tick 路径 hset(key, mapping=...)、loader 记账 hset(key, field, value)
        self.hset_calls.append((key, mapping or {}))

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        self.hincrby_calls.append((key, field, amount))
        return amount

    async def sadd(self, key: str, member: str) -> int:
        return 1

    async def srem(self, key: str, member: str) -> int:
        return 1


class FakeSession:
    def add(self, obj: Any) -> None:
        pass


class FakeSessionCtx:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


class FakeActionRepo:
    def __init__(self, session: Any) -> None:
        self.added: list[Any] = []

    async def add(self, record: Any) -> None:
        self.added.append(record)


class FakeCharRepo:
    def __init__(self, session: Any) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update_state(self, character_id: UUID, **fields: Any) -> None:
        self.updates.append(fields)


class FakeMovementSystem:
    def __init__(self, result: MovementResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def calculate_move(
        self,
        from_scene: str,
        to_scene: str,
        hour: int | None = None,
        is_workday: bool = True,
        *,
        weather_move_multiplier: float = 1.0,
    ) -> MovementResult:
        self.calls.append(
            (
                from_scene,
                to_scene,
                {"hour": hour, "is_workday": is_workday, "weather_move_multiplier": weather_move_multiplier},
            )
        )
        return self._result


def _make_registry() -> ActionRegistry:
    registry = ActionRegistry()
    registry.register(build_move_action())
    registry.register(
        Action(
            id="wait",
            name="等待",
            category=ActionCategory.SOCIAL,
            duration_minutes=10,
            precondition=None,
            executor=None,
        )
    )
    registry.register(
        Action(
            id="relax",
            name="放松",
            category=ActionCategory.LIFE,
            duration_minutes=30,
            allow_dynamic_duration=False,
        )
    )
    registry.register(
        Action(
            id="custom",
            name="自定义",
            category=ActionCategory.LIFE,
            duration_minutes=10,
            executor=lambda state, params: {"custom_flag": "on"},
        )
    )
    registry.register(
        Action(
            id="long_task",
            name="长任务",
            category=ActionCategory.LIFE,
            duration_minutes=480,
        )
    )
    return registry


def _make_engine(registry: ActionRegistry) -> tuple[CharacterTickEngine, FakeRedis]:
    redis = FakeRedis()
    engine = CharacterTickEngine(
        redis=cast(Redis, redis),
        registry=registry,
        llm=cast(LLMClient, None),
        prompts=cast(PromptTemplates, None),
    )
    return engine, redis


def _make_context() -> dict[str, Any]:
    return {
        "character": SimpleNamespace(id=_CHARACTER_ID, name="测试角色"),
        "state": {
            "location": "home",
            "stamina": 80,
            "satiety": 60,
            "mood": "calm",
            "money": 100,
            "phone_battery": 75,
            "social_energy": 60,
            "inventory": {},
        },
        "world": {"world_time": "2026-08-24T10:00:00+00:00"},
    }


def _enable_duration_modules(monkeypatch: pytest.MonkeyPatch) -> SceneLoader:
    """接入真实 DurationCalculator + 场景表（home 室内 / park 户外），捕获修正输入"""
    loader = SceneLoader(cast(Redis, FakeRedis()))
    loader._scenes = {
        "home": Scene(id="home", name="家", type=SceneType.INDOOR, open_hours=[0, 24], capacity=5),
        "park": Scene(id="park", name="公园", type=SceneType.OUTDOOR, open_hours=[0, 24], capacity=100),
    }
    loader._world_map = WorldMap(adjacency={"home": {"park": 15}, "park": {"home": 15}})
    monkeypatch.setattr(tick_module, "get_scene_loader", lambda: loader)
    monkeypatch.setattr(tick_module, "get_duration_calculator", lambda: DurationCalculator())
    return loader


@pytest.fixture
def persistence(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """替换 Tick 引擎的持久化层，捕获 ActionRecord 与状态更新"""
    fake_session = FakeSession()
    action_repo = FakeActionRepo(fake_session)
    char_repo = FakeCharRepo(fake_session)
    monkeypatch.setattr(db_singleton, "session", lambda: FakeSessionCtx(fake_session))
    monkeypatch.setattr(tick_module, "ActionRepository", lambda session: action_repo)
    monkeypatch.setattr(tick_module, "CharacterRepository", lambda session: char_repo)
    return {"action_repo": action_repo, "char_repo": char_repo}


async def test_move_to_unknown_scene_falls_back_to_wait(
    monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
) -> None:
    engine, _ = _make_engine(_make_registry())
    fake_movement = FakeMovementSystem(
        MovementResult(success=False, path=[], total_minutes=0, reason="场景 home 无法直达 mars")
    )
    # tick 顶层绑定了 get_movement_system，须 patch tick 模块内名字
    monkeypatch.setattr(tick_module, "get_movement_system", lambda: fake_movement)

    decision = DecisionResult(action="move", reason="想去火星", params={"target_scene": "mars"})
    context = _make_context()

    await engine._execute_action(_CHARACTER_ID, decision, context)

    assert len(fake_movement.calls) == 1
    record = persistence["action_repo"].added[-1]
    assert record.action_id == "wait"
    assert record.location == "home"


async def test_move_success_uses_matrix_duration_and_updates_location(
    monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
) -> None:
    engine, redis = _make_engine(_make_registry())
    fake_movement = FakeMovementSystem(MovementResult(success=True, path=["home", "cafe"], total_minutes=12))
    # tick 顶层绑定了 get_movement_system，须 patch tick 模块内名字
    monkeypatch.setattr(tick_module, "get_movement_system", lambda: fake_movement)

    decision = DecisionResult(action="move", reason="去咖啡店", params={"target_scene": "cafe"})
    context = _make_context()

    await engine._execute_action(_CHARACTER_ID, decision, context)

    record = persistence["action_repo"].added[-1]
    assert record.action_id == "move"
    assert record.duration_minutes == 12
    update = persistence["char_repo"].updates[-1]
    assert update["location"] == "cafe"
    # 位置变化应维护场景在场人数：旧场景 -1、新场景 +1
    assert ("world:scene:visitors", "home", -1) in redis.hincrby_calls
    assert ("world:scene:visitors", "cafe", 1) in redis.hincrby_calls


async def test_llm_duration_ignored_without_allow_dynamic_duration(
    persistence: dict[str, Any],
) -> None:
    engine, _ = _make_engine(_make_registry())

    decision = DecisionResult(action="relax", reason="休息一下", duration=999)
    context = _make_context()

    await engine._execute_action(_CHARACTER_ID, decision, context)

    record = persistence["action_repo"].added[-1]
    assert record.duration_minutes == 30


async def test_executor_changes_merged_into_new_state(persistence: dict[str, Any]) -> None:
    engine, redis = _make_engine(_make_registry())

    decision = DecisionResult(action="custom", reason="触发自定义动作")
    context = _make_context()

    await engine._execute_action(_CHARACTER_ID, decision, context)

    redis_mapping = redis.hset_calls[-1][1]
    assert redis_mapping["custom_flag"] == "on"


class TestDurationModifiersInTick:
    """round-6 M9c：DurationCalculator 接入 Tick 非移动耗时路径"""

    async def test_stormy_outdoor_raises_duration_within_clamp(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        _enable_duration_modules(monkeypatch)
        engine, _ = _make_engine(_make_registry())
        decision = DecisionResult(action="relax", reason="公园放松")
        context = _make_context()
        context["state"]["location"] = "park"
        context["world"]["weather"] = "stormy"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        record = persistence["action_repo"].added[-1]
        assert record.duration_minutes == 45  # 30 × 1.5

    async def test_indoor_location_immune_to_weather(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        _enable_duration_modules(monkeypatch)
        engine, _ = _make_engine(_make_registry())
        decision = DecisionResult(action="relax", reason="在家休息")
        context = _make_context()
        context["world"]["weather"] = "stormy"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        record = persistence["action_repo"].added[-1]
        assert record.duration_minutes == 30

    async def test_neutral_conditions_leave_duration_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        _enable_duration_modules(monkeypatch)
        engine, _ = _make_engine(_make_registry())
        decision = DecisionResult(action="relax", reason="休息")
        context = _make_context()
        context["world"]["weather"] = "sunny"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        record = persistence["action_repo"].added[-1]
        assert record.duration_minutes == 30

    async def test_multiplier_result_clamped_to_global_maximum(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        _enable_duration_modules(monkeypatch)
        engine, _ = _make_engine(_make_registry())
        decision = DecisionResult(action="long_task", reason="户外长任务")
        context = _make_context()
        context["state"]["location"] = "park"
        context["state"]["stamina"] = 0
        context["world"]["weather"] = "stormy"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        record = persistence["action_repo"].added[-1]
        # 480 × 1.5(暴风) × 1.5(体力0) = 1080 → 全局钳制 480
        assert record.duration_minutes == 480

    async def test_modules_degraded_keep_base_duration(self, persistence: dict[str, Any]) -> None:
        engine, _ = _make_engine(_make_registry())
        decision = DecisionResult(action="relax", reason="模块降级时休息")
        context = _make_context()
        context["world"]["weather"] = "stormy"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        record = persistence["action_repo"].added[-1]
        assert record.duration_minutes == 30


class TestMoveWeatherAndWorkdayWiring:
    """round-6 M9a/M9b：Tick 向 MovementSystem 传天气倍率与工作日标记"""

    async def test_move_receives_workday_flag_and_weather_multiplier(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        engine, _ = _make_engine(_make_registry())
        fake_movement = FakeMovementSystem(MovementResult(success=True, path=["home", "cafe"], total_minutes=18))
        monkeypatch.setattr(tick_module, "get_movement_system", lambda: fake_movement)

        decision = DecisionResult(action="move", reason="雨天出门", params={"target_scene": "cafe"})
        context = _make_context()  # 周一 + 无天气字段
        context["world"]["weather"] = "rainy"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        _, _, kwargs = fake_movement.calls[0]
        assert kwargs["is_workday"] is True
        assert kwargs["weather_move_multiplier"] == 1.5

    async def test_weekend_world_time_marks_non_workday(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        engine, _ = _make_engine(_make_registry())
        fake_movement = FakeMovementSystem(MovementResult(success=True, path=["home", "cafe"], total_minutes=12))
        monkeypatch.setattr(tick_module, "get_movement_system", lambda: fake_movement)

        decision = DecisionResult(action="move", reason="周末出门", params={"target_scene": "cafe"})
        context = _make_context()
        context["world"]["world_time"] = "2026-08-22T10:00:00+00:00"  # 周六

        await engine._execute_action(_CHARACTER_ID, decision, context)

        _, _, kwargs = fake_movement.calls[0]
        assert kwargs["is_workday"] is False

    async def test_move_duration_not_double_adjusted(
        self, monkeypatch: pytest.MonkeyPatch, persistence: dict[str, Any]
    ) -> None:
        _enable_duration_modules(monkeypatch)
        engine, _ = _make_engine(_make_registry())
        # total_minutes 已含矩阵 × 天气倍率（MovementSystem 内完成）
        fake_movement = FakeMovementSystem(MovementResult(success=True, path=["home", "cafe"], total_minutes=18))
        monkeypatch.setattr(tick_module, "get_movement_system", lambda: fake_movement)

        decision = DecisionResult(action="move", reason="暴雨天移动", params={"target_scene": "cafe"})
        context = _make_context()
        context["world"]["weather"] = "stormy"

        await engine._execute_action(_CHARACTER_ID, decision, context)

        record = persistence["action_repo"].added[-1]
        assert record.duration_minutes == 18
