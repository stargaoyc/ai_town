"""MemoryRepository 集成测试 - HASH 分区表 + SKIP LOCKED + pgvector 混合检索

覆盖文档「测试覆盖缺口」P0/P1 项：
- embedding worker 队列：fetch_unmaterialized 的 SKIP LOCKED 与熔断/退避过滤
- 向量化成功/失败路径（update_embedding / mark_embedding_failed 指数退避）
- search_hybrid 真实 halfvec 余弦检索（HNSW 索引 + 分区裁剪）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character, MemoryEpisode
from src.db.repositories.memory_repo import MemoryRepository

_EMBEDDING_DIM = 2048


def _unit_vec(dim: int = _EMBEDDING_DIM, index: int = 0) -> list[float]:
    """单位向量：仅 index 位置为 1.0，其余 0——余弦相似度可手工推算"""
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


@pytest_asyncio.fixture
async def memory_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="记忆测试角色")
    it_session.add(char)
    await it_session.flush()
    return char


class TestEmbeddingWorkerQueue:
    async def test_fetch_unmaterialized_returns_new_episode(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        episode = MemoryEpisode(character_id=memory_character.id, content="今天去了咖啡馆")
        it_session.add(episode)
        await it_session.flush()

        fetched = await MemoryRepository(it_session).fetch_unmaterialized(limit=10)
        assert episode.id in {e.id for e in fetched}

    async def test_circuit_broken_episodes_excluded(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        broken = MemoryEpisode(
            character_id=memory_character.id,
            content="熔断的记忆",
            fail_count=5,
            materialized=False,
        )
        ok = MemoryEpisode(character_id=memory_character.id, content="正常的记忆")
        it_session.add_all([broken, ok])
        await it_session.flush()

        fetched = await MemoryRepository(it_session).fetch_unmaterialized(limit=50)
        ids = {e.id for e in fetched}
        assert ok.id in ids
        assert broken.id not in ids

    async def test_next_retry_backoff_defers_episode(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        repo = MemoryRepository(it_session)
        waiting = MemoryEpisode(
            character_id=memory_character.id,
            content="退避中",
            fail_count=1,
            next_retry_at=datetime.now(UTC) + timedelta(hours=1),
        )
        retryable = MemoryEpisode(
            character_id=memory_character.id,
            content="已到重试时间",
            fail_count=1,
            next_retry_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        it_session.add_all([waiting, retryable])
        await it_session.flush()

        fetched = await repo.fetch_unmaterialized(limit=50)
        ids = {e.id for e in fetched}
        assert retryable.id in ids
        assert waiting.id not in ids

    async def test_update_embedding_materializes_and_resets_failure_state(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        repo = MemoryRepository(it_session)
        episode = MemoryEpisode(
            character_id=memory_character.id,
            content="待向量化",
            fail_count=2,
            last_error="boom",
            next_retry_at=datetime.now(UTC),
        )
        it_session.add(episode)
        await it_session.flush()

        await repo.update_embedding(episode.id, memory_character.id, _unit_vec(index=3))

        refreshed = await it_session.get(MemoryEpisode, (episode.id, memory_character.id))
        assert refreshed is not None
        assert refreshed.materialized is True
        assert refreshed.fail_count == 0
        assert refreshed.last_error is None
        assert refreshed.next_retry_at is None

    async def test_mark_embedding_failed_applies_exponential_backoff(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        repo = MemoryRepository(it_session)
        episode = MemoryEpisode(character_id=memory_character.id, content="会失败的")
        it_session.add(episode)
        await it_session.flush()

        before = datetime.now(UTC)
        await repo.mark_embedding_failed(episode.id, memory_character.id, "embedding service down")

        # repo 内部用 update() 语句绕过了身份映射，显式 refresh 读最新值（expire_all 会触发同步 IO）
        refreshed = await it_session.get(MemoryEpisode, (episode.id, memory_character.id))
        assert refreshed is not None
        await it_session.refresh(refreshed)
        assert refreshed.fail_count == 1
        assert refreshed.last_error == "embedding service down"
        # retry 1 → 约 60s 后
        assert refreshed.next_retry_at is not None
        delta = (refreshed.next_retry_at - before).total_seconds()
        assert 55 <= delta <= 65


class TestSearchHybrid:
    async def test_vector_search_returns_nearest_and_skips_unmaterialized(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        near = MemoryEpisode(
            character_id=memory_character.id,
            content="与查询向量完全一致的记忆",
            embedding=_unit_vec(index=7),
            materialized=True,
            importance=5,
        )
        far = MemoryEpisode(
            character_id=memory_character.id,
            content="正交方向的记忆",
            embedding=_unit_vec(index=100),
            materialized=True,
            importance=10,
        )
        unmaterialized = MemoryEpisode(
            character_id=memory_character.id,
            content="未向量化的记忆不应出现",
            importance=10,
        )
        other_char = Character(id=uuid7(), name="别的角色")
        it_session.add(other_char)
        await it_session.flush()
        foreign = MemoryEpisode(
            character_id=other_char.id,
            content="其他角色的记忆（分区隔离）",
            embedding=_unit_vec(index=7),
            materialized=True,
        )
        it_session.add_all([near, far, unmaterialized, foreign])
        await it_session.flush()

        rows = await MemoryRepository(it_session).search_hybrid(memory_character.id, _unit_vec(index=7), top_k=2)

        contents = [r["content"] for r in rows]
        assert contents[0] == near.content  # sim=1.0 完全命中
        assert far.content in contents
        assert unmaterialized.content not in contents
        assert foreign.content not in contents
        assert all(r["sim_score"] > 0.99 for r in rows if r["content"] == near.content)

    async def test_importance_and_recency_boost_ranking(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        """两记忆与查询等距时，重要性高且更新的应排前（final_score 公式回归）"""
        old_low = MemoryEpisode(
            character_id=memory_character.id,
            content="旧且不重要",
            embedding=_unit_vec(index=11),
            materialized=True,
            importance=1,
        )
        new_high = MemoryEpisode(
            character_id=memory_character.id,
            content="新且重要",
            embedding=_unit_vec(index=12),
            materialized=True,
            importance=9,
        )
        it_session.add_all([old_low, new_high])
        await it_session.flush()

        rows = await MemoryRepository(it_session).search_hybrid(memory_character.id, _unit_vec(index=13), top_k=5)

        scores = {r["content"]: float(r["final_score"]) for r in rows}
        assert scores["新且重要"] > scores["旧且不重要"]

    async def test_decay_floor_keeps_old_important_memories_reachable(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        """指数衰减 25% 下限：300 天的重要记忆仍可召回且得分不低于基准的 ~25%（P0-2 回归）"""
        old_important = MemoryEpisode(
            character_id=memory_character.id,
            content="三个月前的重要事件",
            embedding=_unit_vec(index=21),
            materialized=True,
            importance=9,
            timestamp=datetime.now(UTC) - timedelta(days=300),
        )
        it_session.add(old_important)
        await it_session.flush()

        # 查询向量与记忆向量同向（sim≈1），隔离衰减因子维度
        rows = await MemoryRepository(it_session).search_hybrid(memory_character.id, _unit_vec(index=21), top_k=5)

        target = [r for r in rows if r["content"] == "三个月前的重要事件"]
        assert target, "老记忆必须仍可召回"
        final = float(target[0]["final_score"])
        # sim≈1 时 base≈1.05；300 天因子 ≈0.250034 -> final ≈0.2625
        assert 0.25 <= final <= 0.28

    async def test_fresh_memory_full_score_no_penalty(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        """刚发生的记忆不受衰减惩罚（days≈0，因子≈1）"""
        fresh = MemoryEpisode(
            character_id=memory_character.id,
            content="刚刚发生的事",
            embedding=_unit_vec(index=31),
            materialized=True,
            importance=10,
        )
        it_session.add(fresh)
        await it_session.flush()

        # 同向查询（sim≈1），隔离衰减因子维度
        rows = await MemoryRepository(it_session).search_hybrid(memory_character.id, _unit_vec(index=31), top_k=5)
        final = float(rows[0]["final_score"])
        # sim≈1、imp=10 -> base≈1.1，因子≈1
        assert final > 1.0


class TestExistsRecentDuplicate:
    async def test_exact_normalized_match_detected(self, it_session: AsyncSession, memory_character: Character) -> None:
        it_session.add(MemoryEpisode(character_id=memory_character.id, content="今天  在咖啡店  和艾莉丝聊天"))
        await it_session.flush()

        repo = MemoryRepository(it_session)
        assert await repo.exists_recent_duplicate(memory_character.id, "今天 在咖啡店 和艾莉丝聊天") is True

    async def test_paraphrase_not_detected_documents_limitation(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        """改写式复述当前不拦截——pg_trgm 对中文实测无效（真实改写对仅 0.3-0.4），
        正确方案是 embedding worker 落向量后余弦比对（复审 N7 待办）。
        本测试钉住现状语义，防止误以为已覆盖 paraphrase。"""
        it_session.add(MemoryEpisode(character_id=memory_character.id, content="今天在咖啡店和艾莉丝聊了新上线的拿铁"))
        await it_session.flush()

        repo = MemoryRepository(it_session)
        assert await repo.exists_recent_duplicate(memory_character.id, "今天在咖啡馆跟艾莉丝聊起新推出的拿铁") is False

    async def test_unrelated_content_not_flagged(self, it_session: AsyncSession, memory_character: Character) -> None:
        it_session.add(MemoryEpisode(character_id=memory_character.id, content="在图书馆读完了一本哲学书"))
        await it_session.flush()

        repo = MemoryRepository(it_session)
        assert await repo.exists_recent_duplicate(memory_character.id, "去公园跑了五公里") is False

    async def test_old_duplicate_outside_window_ignored(
        self, it_session: AsyncSession, memory_character: Character
    ) -> None:
        stale = MemoryEpisode(
            character_id=memory_character.id,
            content="今天在咖啡店和艾莉丝聊天",
            timestamp=datetime.now(UTC) - timedelta(hours=48),
        )
        it_session.add(stale)
        await it_session.flush()

        repo = MemoryRepository(it_session)
        assert await repo.exists_recent_duplicate(memory_character.id, "今天在咖啡店和艾莉丝聊天", hours=24) is False
