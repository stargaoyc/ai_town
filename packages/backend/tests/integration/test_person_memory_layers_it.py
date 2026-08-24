"""PersonMemory 两层结构集成测试 - 抽取追加 + 主档压缩（审查清单 #4）"""

from __future__ import annotations

from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.config import settings
from src.db.models import Character, PersonMemory, PersonMemoryEntry
from src.memory.person_memory_service import PersonMemoryService


class StubLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[str] = []

    async def chat(self, prompt: str, model: str | None = None) -> str:
        self.calls.append(prompt)
        return self._response


class StubPrompts:
    def render(self, name: str, **kwargs: Any) -> str:
        return f"[{name}] {kwargs.get('user_message', '')}{kwargs.get('facts_text', '')}"


class _SessionFactory:
    """把共享 it_session 包装成服务所需的 session_factory 形态"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest_asyncio.fixture
async def pm_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="PM测试角色")
    it_session.add(char)
    await it_session.flush()
    return char


def _service(it_session: AsyncSession, llm: StubLLM) -> PersonMemoryService:
    return PersonMemoryService(
        session_factory=_SessionFactory(it_session),
        llm_client=llm,
        prompts=StubPrompts(),
    )


class TestExtractionAppend:
    async def test_facts_appended_not_rewritten(self, it_session: AsyncSession, pm_character: Character) -> None:
        llm = StubLLM('{"facts": ["用户喜欢深夜聊天", "用户在准备考研"], "preferences": {"称呼": "小K"}}')
        service = _service(it_session, llm)

        result = await service.update_memory(
            character_id=pm_character.id,
            character_name="小艾",
            user_id="qq_123",
            platform="qq",
            user_message="我最近在准备考研，总熬夜",
            character_reply="注意休息呀",
        )

        assert result is not None and result["appended"] == 2
        entries = list(
            (await it_session.execute(select(PersonMemoryEntry).where(PersonMemoryEntry.user_id == "qq_123"))).scalars()
        )
        assert len(entries) == 2
        assert all(e.compacted is False for e in entries)
        # 主档行存在但 content 未被交互重写（保持空，等压缩任务合并）
        profile = await it_session.scalar(
            select(PersonMemory.content).where(
                PersonMemory.character_id == pm_character.id, PersonMemory.user_id == "qq_123"
            )
        )
        assert profile is not None
        assert profile == ""

    async def test_parse_failure_falls_back_to_user_message(
        self, it_session: AsyncSession, pm_character: Character
    ) -> None:
        llm = StubLLM("这不是 JSON 输出")
        service = _service(it_session, llm)

        result = await service.update_memory(
            character_id=pm_character.id,
            character_name="小艾",
            user_id="qq_456",
            platform="web",
            user_message="今天去了海边散心",
            character_reply="听起来很放松",
        )

        assert result is not None and result["appended"] == 1
        entry = await it_session.scalar(select(PersonMemoryEntry).where(PersonMemoryEntry.user_id == "qq_456"))
        assert entry is not None
        assert "海边" in entry.content


class TestTwoLayerContextAndCompaction:
    async def test_context_composes_profile_and_recent_entries(
        self, it_session: AsyncSession, pm_character: Character
    ) -> None:
        llm = StubLLM('{"facts": ["用户养了一只猫"], "preferences": {}}')
        service = _service(it_session, llm)
        await service.update_memory(
            character_id=pm_character.id,
            character_name="小艾",
            user_id="qq_789",
            platform="web",
            user_message="我家猫今天拆家了",
            character_reply="哈哈",
        )
        # update_memory 已自动创建主档行，此处只改写 content 模拟压缩任务产物
        profile_row = await it_session.scalar(
            select(PersonMemory).where(PersonMemory.character_id == pm_character.id, PersonMemory.user_id == "qq_789")
        )
        assert profile_row is not None
        profile_row.content = "用户是猫主人，作息偏晚。"
        await it_session.flush()

        context = await service.get_relevant_context(pm_character.id, "qq_789")

        assert "猫主人" in context  # 主档层
        assert "最近了解到的" in context and "养了一只猫" in context  # 条目层

    async def test_compaction_merges_into_profile_and_flags_entries(
        self, it_session: AsyncSession, pm_character: Character, monkeypatch: Any
    ) -> None:
        import src.runtime as runtime_mod
        from src.scheduler.loops import run_person_memory_compaction

        for i in range(3):
            it_session.add(
                PersonMemoryEntry(
                    character_id=pm_character.id,
                    user_id="qq_compact",
                    content=f"事实{i}",
                )
            )
        it_session.add(PersonMemory(character_id=pm_character.id, user_id="qq_compact", content="旧主档", heat=1))
        await it_session.flush()

        monkeypatch.setattr(settings, "person_memory_compact_threshold", 2)
        monkeypatch.setattr(runtime_mod, "get_llm", lambda: StubLLM('{"content": "新主档：合并后内容"}'))
        monkeypatch.setattr(runtime_mod, "get_prompts", lambda: StubPrompts())

        pairs = await run_person_memory_compaction(lambda: _SessionFactory(it_session)())
        assert pairs >= 1

        profile = await it_session.scalar(
            select(PersonMemory.content).where(
                PersonMemory.character_id == pm_character.id, PersonMemory.user_id == "qq_compact"
            )
        )
        assert profile == "新主档：合并后内容"
        entries = list(
            (
                await it_session.execute(select(PersonMemoryEntry).where(PersonMemoryEntry.user_id == "qq_compact"))
            ).scalars()
        )
        assert all(e.compacted for e in entries)
