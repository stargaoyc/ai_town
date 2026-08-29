"""CharacterTickEngine.tick_character 主流程集成测试（round-7 P2-8）

覆盖「感知 → 决策 → 执行 → 记忆」完整闭环（wait 决策路径）：
- 从 PG 读取角色档案与状态
- LLM 结构化决策（stub 返回 wait）
- Action 执行写入 action_records + character_states
- 记忆沉淀写入 memory_episodes
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from pytest import MonkeyPatch
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.character.tick import CharacterTickEngine
from src.db.models import ActionRecord, Character, CharacterState, MemoryEpisode

_CHAR_ID = UUID("01964000-0000-7000-8000-000000000001")


class StubLLM:
    """structured_output 返回 wait 决策；embed 返回占位向量"""

    def __init__(self) -> None:
        self.decide_calls = 0

    async def structured_output(self, prompt: str, schema: dict[str, Any], model: str = "chat") -> dict[str, Any]:
        self.decide_calls += 1
        return {"action": "wait", "reason": "test_wait", "params": {}, "duration": 10}

    async def embed(self, text: str) -> list[float]:
        return [0.0] * 2048

    async def chat(self, prompt: str, model: str | None = None) -> str:
        return ""


class StubPrompts:
    def render(self, _template: str, **kwargs: Any) -> str:
        return f"[{_template}: {list(kwargs.keys())}]"

    def render_system(self, _template: str, **kwargs: Any) -> str:
        return ""

    def has_system(self, _template: str) -> bool:
        return False


@asynccontextmanager
async def _ctx(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield session


class TestCharacterTickMainFlow:
    async def test_tick_character_writes_action_and_memory(
        self,
        it_session: AsyncSession,
        it_redis: AsyncRedis,
        monkeypatch: MonkeyPatch,
    ) -> None:
        it_session.add(Character(id=_CHAR_ID, name="小艾", is_active=True))
        it_session.add(
            CharacterState(
                character_id=_CHAR_ID,
                location="home",
                stamina=80,
                satiety=60,
                mood="calm",
                money=500,
            )
        )
        await it_session.flush()
        # 预置 Redis 实时状态（_perceive 缓存优先；缺失时回退 PG）
        await it_redis.hset(f"char:{_CHAR_ID}:state", mapping={"location": "home"})
        await it_redis.hset("world:state", mapping={"world_time": "2026-08-27T10:00:00", "weather": "sunny"})

        # 统一注入集成会话（tick/perception/social 共享 db 单例）
        import src.db.session as session_mod

        monkeypatch.setattr(session_mod.db, "session", lambda: _ctx(it_session))

        llm = StubLLM()
        from src.actions import Action, ActionCategory, ActionRegistry

        registry = ActionRegistry()
        registry.register(Action(id="wait", name="等待", category=ActionCategory.LIFE, duration_minutes=10))
        # 临时关闭记忆写入门禁（5b15467 引入的显著性门禁会过滤 wait 等低重要性 action）
        monkeypatch.setattr(settings, "memory_write_gate_enabled", False, raising=False)
        engine = CharacterTickEngine(
            redis=it_redis,
            registry=registry,
            llm=cast(Any, llm),
            prompts=cast(Any, StubPrompts()),
        )

        await engine.tick_character(_CHAR_ID)

        # 行为记录写入
        records = list(
            (await it_session.execute(select(ActionRecord).where(ActionRecord.character_id == _CHAR_ID))).scalars()
        )
        assert len(records) == 1
        assert records[0].action_id == "wait"
        assert records[0].reason == "test_wait"

        # 记忆沉淀写入
        episodes = list(
            (await it_session.execute(select(MemoryEpisode).where(MemoryEpisode.character_id == _CHAR_ID))).scalars()
        )
        assert len(episodes) == 1
        assert episodes[0].source_type == "action"

    async def test_tick_character_without_candidates_skips(
        self,
        it_session: AsyncSession,
        it_redis: AsyncRedis,
        monkeypatch: MonkeyPatch,
    ) -> None:
        it_session.add(Character(id=_CHAR_ID, name="小艾", is_active=True))
        it_session.add(CharacterState(character_id=_CHAR_ID, location="home"))
        await it_session.flush()
        await it_redis.hset(f"char:{_CHAR_ID}:state", mapping={"location": "home"})
        await it_redis.hset("world:state", mapping={"weather": "sunny"})

        import src.db.session as session_mod

        monkeypatch.setattr(session_mod.db, "session", lambda: _ctx(it_session))

        engine = CharacterTickEngine(
            redis=it_redis,
            registry=cast(Any, SimpleNamespace(get_candidates=lambda state, scene=None: [])),
            llm=cast(Any, StubLLM()),
            prompts=cast(Any, StubPrompts()),
        )

        await engine.tick_character(_CHAR_ID)

        records = list(
            (await it_session.execute(select(ActionRecord).where(ActionRecord.character_id == _CHAR_ID))).scalars()
        )
        assert len(records) == 0
