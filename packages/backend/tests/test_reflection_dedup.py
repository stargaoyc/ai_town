"""ReflectionService 反思近重复去重单元测试（R6-M8）

纯逻辑验证（不依赖 DB，stub repo）：
- 与既有 tier-1 反思语义近重复的主题跳过插入，且不计入产出主题数
- 被跳过批次的来源记忆仍全部标记已反思（防止未反思计数反复触发空转）
- embed 失败降级为 None 时不去重、照常插入（不阻塞反思主流程）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from structlog.testing import capture_logs

from src.db.models import MemoryEpisode, Reflection
from src.db.repositories import MemoryRepository, ReflectionRepository
from src.llm import LLMClient
from src.memory.reflection_service import ReflectionService


class StubPrompts:
    def render(self, name: str, **kwargs: Any) -> str:
        return name


class StubLLM:
    def __init__(self, fail_embed: bool = False) -> None:
        self.fail_embed = fail_embed

    async def structured_output(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "reflections": [
                {"summary": "咖啡社交", "detail": "多次在咖啡店交谈", "memory_ids": [1]},
            ]
        }

    async def embed(self, text: str) -> list[float]:
        if self.fail_embed:
            raise RuntimeError("embed backend down")
        return [0.1, 0.2, 0.3]


class StubSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


class StubRefRepo:
    """find_paraphrase_duplicate 结果由 duplicate 开关注入；add 记录写入行"""

    def __init__(self, duplicate: bool) -> None:
        self.duplicate = duplicate
        self.session = StubSession()
        self.added: list[Reflection] = []
        self.dedup_queries = 0

    async def add(self, obj: Reflection) -> Reflection:
        self.added.append(obj)
        return obj

    async def find_paraphrase_duplicate(
        self, character_id: Any, embedding: list[float], similarity_threshold: float = 0.95
    ) -> bool:
        self.dedup_queries += 1
        return self.duplicate


class StubMemRepo:
    def __init__(self) -> None:
        self.episodes: list[MemoryEpisode] = [
            MemoryEpisode(id=uuid4(), character_id=uuid4(), content=f"记忆{i}", timestamp=datetime.now(UTC))
            for i in range(1)
        ]
        self.marked: list[list[Any]] = []

    async def fetch_unreflected(self, character_id: Any, limit: int = 20) -> list[MemoryEpisode]:
        return self.episodes

    async def mark_reflected(self, episode_ids: list[Any]) -> None:
        self.marked.append(list(episode_ids))


def _service(mem_repo: StubMemRepo, ref_repo: StubRefRepo) -> ReflectionService:
    # stub 只实现 _do_reflection 消费的协议子集（测试规范 §5.2 cast 模式）
    return ReflectionService(
        llm=cast(LLMClient, StubLLM()),
        mem_repo=cast(MemoryRepository, mem_repo),
        ref_repo=cast(ReflectionRepository, ref_repo),
        prompts=StubPrompts(),
    )


def _character_id() -> Any:
    return uuid4()


class TestThemeDedup:
    async def test_near_duplicate_theme_skipped_and_not_counted(self) -> None:
        mem_repo, ref_repo = StubMemRepo(), StubRefRepo(duplicate=True)
        cid = _character_id()

        with capture_logs() as logs:
            saved = await _service(mem_repo, ref_repo)._do_reflection(cid)

        assert saved is None
        assert ref_repo.added == []
        assert ref_repo.session.added == [], "被跳过的主题不应挂载 reflection_sources"
        assert ref_repo.dedup_queries == 1
        skipped = [e for e in logs if e.get("event") == "reflection_duplicate_skipped"]
        assert len(skipped) == 1

    async def test_skipped_batch_still_marks_episodes_reflected(self) -> None:
        mem_repo, ref_repo = StubMemRepo(), StubRefRepo(duplicate=True)

        await _service(mem_repo, ref_repo)._do_reflection(_character_id())

        assert mem_repo.marked == [[e.id for e in mem_repo.episodes]], "来源记忆须标记已反思，避免幻影计数空转"

    async def test_distinct_theme_inserted_with_embedding_and_sources(self) -> None:
        mem_repo, ref_repo = StubMemRepo(), StubRefRepo(duplicate=False)
        cid = _character_id()

        with capture_logs() as logs:
            saved = await _service(mem_repo, ref_repo)._do_reflection(cid)

        assert saved is not None
        assert saved.embedding == [0.1, 0.2, 0.3]
        assert saved.tier == 1
        assert saved.importance >= 3
        assert len(ref_repo.session.added) == 1, "reflection_sources 随主题挂载"
        completed = [e for e in logs if e.get("event") == "thematic_reflection_completed"]
        assert len(completed) == 1 and completed[0]["themes"] == 1

    async def test_embed_failure_inserts_without_dedup_check(self) -> None:
        mem_repo = StubMemRepo()
        ref_repo = StubRefRepo(duplicate=False)
        service = ReflectionService(
            llm=cast(LLMClient, StubLLM(fail_embed=True)),
            mem_repo=cast(MemoryRepository, mem_repo),
            ref_repo=cast(ReflectionRepository, ref_repo),
            prompts=StubPrompts(),
        )
        cid = _character_id()

        with capture_logs() as logs:
            saved = await service._do_reflection(cid)

        assert saved is not None and saved.embedding is None
        assert ref_repo.dedup_queries == 0, "embedding 缺失时跳过去重而非阻塞插入"
        failed = [e for e in logs if e.get("event") == "reflection_embedding_failed"]
        assert len(failed) == 1
