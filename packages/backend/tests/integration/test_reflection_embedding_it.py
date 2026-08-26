"""反思 embedding 集成测试

覆盖：
- 反思保存后即时生成 embedding（tier-1 批次反思 + tier-2 元反思，service 路径）
- embed 失败降级为 NULL（检索回退 recency 的文档化退化路径）
- search_semantic 按余弦近邻排序返回（repo 路径）
"""

from __future__ import annotations

from typing import Any, cast

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode, Reflection
from src.db.repositories.memory_repo import MemoryRepository
from src.db.repositories.reflection_repo import ReflectionRepository
from src.llm import LLMClient
from src.memory.reflection_service import ReflectionService


def _unit_vec(dim: int = 2048, index: int = 0) -> list[float]:
    """单位向量：仅 index 位置为 1.0，其余 0——余弦相似度可手工推算"""
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


class _StubPrompts:
    def render(self, name: str, **_kwargs: Any) -> str:
        return name


class _StubLLM:
    """structured_output 返回固定主题/metas；embed 按内容关键词路由到正交向量"""

    def __init__(self, fail_embed: bool = False) -> None:
        self.fail_embed = fail_embed

    async def structured_output(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if "reflection_meta" in prompt:
            return {"metas": [{"meta_summary": "长期偏好咖啡店社交", "meta_detail": "持续光顾咖啡店与人交谈"}]}
        return {
            "reflections": [
                {"summary": "咖啡社交", "detail": "多次在咖啡店与人交谈", "memory_ids": [1, 2]},
                {"summary": "阅读独处", "detail": "在图书馆读书", "memory_ids": [3]},
            ]
        }

    async def embed(self, text: str) -> list[float]:
        if self.fail_embed:
            raise RuntimeError("embed backend down")
        return _unit_vec(index=7 if "咖啡" in text else 11)


def _service(it_session: AsyncSession, llm: _StubLLM) -> ReflectionService:
    # StubLLM 只实现 embed/structured_output 协议（测试规范 §5.2 cast 模式）
    return ReflectionService(
        llm=cast(LLMClient, llm),
        mem_repo=MemoryRepository(it_session),
        ref_repo=ReflectionRepository(it_session),
        prompts=_StubPrompts(),
    )


@pytest_asyncio.fixture
async def reflection_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="反思向量角色")
    it_session.add(char)
    await it_session.flush()
    return char


async def _seed_batch_memories(it_session: AsyncSession, character_id: Any, count: int = 20) -> None:
    for i in range(count):
        content = f"在咖啡店和朋友聊天第{i}场" if i % 2 == 0 else f"在图书馆读完一本书第{i}本"
        it_session.add(MemoryEpisode(character_id=character_id, content=content))
    await it_session.flush()


class TestReflectionEmbeddingGeneration:
    async def test_tier1_and_tier2_reflections_saved_with_embedding(
        self, it_session: AsyncSession, reflection_character: Character
    ) -> None:
        repo = ReflectionRepository(it_session)
        # 预置 5 条历史 tier-1 反思：批次反思后总数 ≥6 才会触发元反思（META_REFLECTION_MIN_TOTAL）
        for i in range(5):
            await repo.add(Reflection(character_id=reflection_character.id, content=f"历史反思{i}", tier=1))

        await _seed_batch_memories(it_session, reflection_character.id)

        saved = await _service(it_session, _StubLLM()).check_and_reflect(reflection_character.id)

        assert saved is not None
        reflections = await repo.get_by_character(reflection_character.id, limit=50)
        metas = [r for r in reflections if r.tier == 2]
        assert len(metas) == 1, "预置 5 + 批次 2 = 7 条应触发元反思"
        assert saved.embedding is not None and len(saved.embedding) == 2048
        for r in reflections:
            if r.content.startswith("历史反思"):
                continue  # 预置原料未经 service 保存路径，本就不带向量
            expected_index = 7 if "咖啡" in r.content else 11
            assert r.embedding is not None, f"tier={r.tier} 反思应已生成 embedding: {r.content[:20]}"
            assert r.embedding[expected_index] == 1.0

    async def test_embed_failure_degrades_to_null(
        self, it_session: AsyncSession, reflection_character: Character
    ) -> None:
        await _seed_batch_memories(it_session, reflection_character.id)

        saved = await _service(it_session, _StubLLM(fail_embed=True)).check_and_reflect(reflection_character.id)

        assert saved is not None
        await it_session.flush()
        refreshed = await it_session.get(Reflection, saved.id)
        assert refreshed is not None
        assert refreshed.embedding is None, "embed 失败必须留 NULL（降级），不得阻塞反思主流程"

    async def test_search_semantic_returns_nearest_first(
        self, it_session: AsyncSession, reflection_character: Character
    ) -> None:
        repo = ReflectionRepository(it_session)
        coffee = Reflection(
            character_id=reflection_character.id,
            content="咖啡社交",
            tier=1,
            embedding=_unit_vec(index=7),
        )
        reading = Reflection(
            character_id=reflection_character.id,
            content="阅读独处",
            tier=1,
            embedding=_unit_vec(index=11),
        )
        no_vector = Reflection(character_id=reflection_character.id, content="无向量的反思", tier=1)
        other_char = Character(id=uuid7(), name="别的角色")
        it_session.add(other_char)
        await it_session.flush()
        foreign = Reflection(
            character_id=other_char.id,
            content="其他角色的反思",
            tier=1,
            embedding=_unit_vec(index=7),
        )
        await repo.add(coffee)
        await repo.add(reading)
        await repo.add(no_vector)
        await repo.add(foreign)

        rows = await repo.search_semantic(reflection_character.id, _unit_vec(index=7), limit=5)

        contents = [r.content for r in rows]
        assert contents[0] == "咖啡社交"
        assert "阅读独处" in contents
        assert "无向量的反思" not in contents, "embedding 为 NULL 的行不参与语义检索"
        assert "其他角色的反思" not in contents, "跨角色隔离"

        rows_far = await repo.search_semantic(reflection_character.id, _unit_vec(index=11), limit=5)
        assert rows_far[0].content == "阅读独处"
