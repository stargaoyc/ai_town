"""P0-5 回归测试：RelationGraph 关系升级日志可达

修复前 update_on_interaction 先覆写 relationship_type 再比较旧值，
条件恒为 False，升级日志永远不可达。本测试锁定：
- 类型跨档位变化时双向都产生「关系升级」日志
- 类型未变化时不产生日志
"""

from collections.abc import MutableMapping
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from src.db.models.relation import Relation
from src.db.repositories.relation_repo import RelationRepository
from src.modules.relation.graph import RelationGraph

_CHAR_A = UUID("01964000-0000-7000-8000-000000000001")
_CHAR_B = UUID("01964000-0000-7000-8000-000000000002")


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: object) -> None:
        self.store[key] = {k: str(v) for k, v in (mapping or {}).items()}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))


class FakeRelationRepo:
    def __init__(self) -> None:
        self.relations: dict[tuple[UUID, UUID], Relation] = {}
        self._seed(_CHAR_A, _CHAR_B, strength=18)
        self._seed(_CHAR_B, _CHAR_A, strength=18)

    def _seed(self, character_id: UUID, target_id: UUID, strength: int) -> None:
        self.relations[(character_id, target_id)] = Relation(
            character_id=character_id,
            target_id=target_id,
            strength=strength,
            relationship_type="stranger",
            last_interaction_at=None,
            notes=None,
        )

    async def get_or_create(self, character_id: UUID, target_id: UUID) -> Relation:
        if (character_id, target_id) not in self.relations:
            self._seed(character_id, target_id, strength=20)
        return self.relations[(character_id, target_id)]

    async def update_relation(self, character_id: UUID, target_id: UUID, **fields: object) -> None:
        rel = self.relations[(character_id, target_id)]
        for field, value in fields.items():
            setattr(rel, field, value)


def _make_graph() -> RelationGraph:
    graph = RelationGraph(cast(AsyncSession, object()), cast(Redis, FakeRedis()))
    graph.repo = cast(RelationRepository, FakeRelationRepo())
    return graph


def _upgrade_events(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [e for e in logs if str(e.get("event", "")).startswith("关系升级")]


async def test_upgrade_log_emitted_on_type_change_both_directions() -> None:
    graph = _make_graph()

    with capture_logs() as logs:
        await graph.update_on_interaction(_CHAR_A, _CHAR_B, strength_delta=+5)

    upgrades = _upgrade_events(logs)
    assert len(upgrades) == 2
    events = [str(entry.get("event", "")) for entry in upgrades]
    assert any(str(_CHAR_A) in e and "stranger -> acquaintance" in e for e in events)
    assert any(str(_CHAR_B) in e and "stranger -> acquaintance" in e for e in events)


async def test_no_upgrade_log_when_type_unchanged() -> None:
    graph = _make_graph()

    with capture_logs() as logs:
        await graph.update_on_interaction(_CHAR_A, _CHAR_B, strength_delta=+1)

    assert not _upgrade_events(logs)
