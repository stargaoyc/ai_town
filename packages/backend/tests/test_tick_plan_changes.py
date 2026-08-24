"""CharacterTickEngine._apply_plan_changes 安全语义单元测试（审查二轮 N2）

LLM 决策的 planChanges 进入 plans 表的唯一通道，锁定三个安全语义：
- 跨角色防篡改：update_plan_scoped 必须收到决策所属 character_id
- progress 钳制：越界收敛到 [0, 100]，bool 不视为 int
- 非法输入容错：坏 UUID / 非 dict 条目 / 空 updates 跳过且不抛异常
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from src.core.character.tick import CharacterTickEngine
from src.db.repositories.plan_repo import PlanRepository


class FakePlanRepo:
    def __init__(self, applied: bool = True) -> None:
        self.calls: list[tuple[UUID, UUID, dict[str, Any]]] = []
        self._applied = applied

    async def update_plan_scoped(self, plan_id: UUID, character_id: UUID, **fields: Any) -> bool:
        self.calls.append((plan_id, character_id, fields))
        return self._applied


def _as_repo(fake: FakePlanRepo) -> PlanRepository:
    # 测试替身注入生产签名：_apply_plan_changes 仅依赖 update_plan_scoped 协议（测试规范 §5.2）
    return cast(PlanRepository, fake)


class TestApplyPlanChanges:
    async def test_status_actions_map_and_scope_by_character(self) -> None:
        fake = FakePlanRepo()
        plan_id = uuid4()
        owner_id = uuid4()

        await CharacterTickEngine._apply_plan_changes(
            _as_repo(fake),
            owner_id,
            [
                {"planId": str(plan_id), "action": "complete"},
                {"planId": str(plan_id), "action": "abandon", "progress": 50},
                {"planId": str(plan_id), "action": "update", "progress": 80},
            ],
        )

        assert [c[2] for c in fake.calls] == [
            {"status": "completed"},
            {"status": "abandoned", "progress": 50},
            {"status": "active", "progress": 80},
        ]
        # 每次调用都以决策所属角色约束范围——跨角色 planId 更新会被 SQL 层拒绝
        assert all(c[1] == owner_id for c in fake.calls)

    async def test_progress_clamped_and_bool_rejected(self) -> None:
        fake = FakePlanRepo()
        plan_id = uuid4()

        await CharacterTickEngine._apply_plan_changes(
            _as_repo(fake),
            uuid4(),
            [
                {"planId": str(plan_id), "progress": 150},
                {"planId": str(plan_id), "progress": -5},
                {"planId": str(plan_id), "progress": True},
                {"planId": str(plan_id), "progress": "80"},
            ],
        )

        # 无显式 action 的条目只更新 progress；bool/str 进度不构成合法变更，
        # 连同缺省 status 一起被跳过（不再有隐式 update 兜底）
        progresses = [c[2].get("progress") for c in fake.calls]
        assert progresses == [100, 0]

    async def test_invalid_entries_skipped_without_raise(self) -> None:
        fake = FakePlanRepo()
        # 故意混入非 dict 条目，验证运行时容错（类型上需 Any 才能构造畸形输入）
        malformed: list[Any] = [
            "not-a-dict",
            {"planId": "", "action": "complete"},
            {"planId": "bad-uuid", "action": "complete"},
            {"action": "complete"},
            {"planId": str(uuid4())},
            123,
        ]

        await CharacterTickEngine._apply_plan_changes(_as_repo(fake), uuid4(), malformed)

        assert fake.calls == []

    async def test_target_not_found_does_not_raise(self) -> None:
        fake = FakePlanRepo(applied=False)

        await CharacterTickEngine._apply_plan_changes(
            _as_repo(fake),
            uuid4(),
            [{"planId": str(uuid4()), "action": "complete"}],
        )

        assert len(fake.calls) == 1

    async def test_empty_changes_is_noop(self) -> None:
        fake = FakePlanRepo()

        await CharacterTickEngine._apply_plan_changes(_as_repo(fake), uuid4(), [])

        assert fake.calls == []
