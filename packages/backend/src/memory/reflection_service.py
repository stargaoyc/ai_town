"""反思服务 - 从记忆片段提炼高层认知（两层）

- tier=1 批次主题反思：未反思记忆达到阈值后触发；LLM 对编号记忆做主题归纳，
  每个主题一条 Reflection，来源只挂载支撑该主题的记忆（grounding 精确化）
- tier=2 跨期元反思：累计反思足够多且近期无元反思时触发，
  对既有反思再做归纳，沉淀长期倾向与价值观（跨期主题归纳）
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from structlog import get_logger

from src.db.models import Reflection, ReflectionSource
from src.db.repositories import MemoryRepository, ReflectionRepository
from src.llm import LLMClient

logger = get_logger(__name__)


class ReflectionService:
    """反思服务（批次主题反思 + 跨期元反思）"""

    REFLECTION_THRESHOLD = 20  # 每 N 条未反思记忆触发批次反思
    REFLECTION_POOL_SIZE = 30  # 单次参与归纳的记忆池上限（跨期覆盖优于旧版固定 20）
    META_REFLECTION_MIN_TOTAL = 6  # 累计反思达到该数量后才考虑元反思
    META_REFLECTION_COOLDOWN_DAYS = 7  # 两次元反思的最小间隔
    META_SOURCE_LIMIT = 10  # 元反思读取的最近 tier-1 反思条数

    def __init__(
        self,
        llm: LLMClient,
        mem_repo: MemoryRepository,
        ref_repo: ReflectionRepository,
        prompts: Any | None = None,
    ):
        self.llm = llm
        self.mem_repo = mem_repo
        self.ref_repo = ref_repo
        self._prompts = prompts

    async def check_and_reflect(self, character_id: UUID) -> Reflection | None:
        """检查是否需要批次反思，如需要则执行；随后尝试元反思

        Returns:
            本次产生的最后一条 Reflection（可能是 tier=1 或 tier=2），否则 None
        """
        count = await self.mem_repo.count_unreflected(character_id)
        if count < self.REFLECTION_THRESHOLD:
            return None

        reflection = await self._do_reflection(character_id)

        # 批次完成后尝试元反思（失败不影响主流程）
        try:
            await self._maybe_meta_reflect(character_id)
        except Exception as e:
            logger.warning("meta_reflection_failed", character_id=str(character_id), error=str(e))

        if reflection is not None:
            logger.info(
                "reflection_completed",
                character_id=str(character_id),
                episode_count=count,
            )
        return reflection

    async def _do_reflection(self, character_id: UUID) -> Reflection | None:
        """批次主题反思：编号记忆 -> 多主题 -> 每主题一条 Reflection"""
        episodes = await self.mem_repo.fetch_unreflected(character_id, limit=self.REFLECTION_POOL_SIZE)
        if not episodes:
            return None

        memories_text = "\n".join(
            f"[{idx}] [{e.timestamp:%m-%d %H:%M}] {e.content}" for idx, e in enumerate(episodes, start=1)
        )

        prompts = self._prompts or self._load_prompts(character_id)
        if prompts is None:
            return None
        prompt = prompts.render("reflection", memories_text=memories_text)

        result = await self.llm.structured_output(
            prompt,
            schema={
                "type": "object",
                "properties": {
                    "reflections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "detail": {"type": "string"},
                                "memory_ids": {"type": "array", "items": {"type": "integer"}},
                            },
                        },
                    }
                },
            },
            model="chat",
        )
        themes = self._parse_themes(result, total=len(episodes))
        if not themes:
            # LLM 未给出可用主题映射时退化为单条汇总，来源挂全部记忆（不丢 grounding）
            themes = [
                {
                    "summary": "近期经历小结",
                    "detail": "\n".join(e.content for e in episodes[:10]),
                    "memory_ids": list(range(1, len(episodes) + 1)),
                }
            ]

        saved: Reflection | None = None
        for theme in themes:
            reflection = Reflection(
                character_id=character_id,
                content=f"{theme['summary']}：{theme['detail']}",
                tier=1,
            )
            stored = await self.ref_repo.add(reflection)
            saved = stored
            for memory_id in theme["memory_ids"]:
                episode = episodes[memory_id - 1]
                self.ref_repo.session.add(
                    ReflectionSource(
                        reflection_id=stored.id,
                        memory_id=episode.id,
                        memory_character_id=episode.character_id,
                    )
                )
        await self.ref_repo.session.flush()

        await self.mem_repo.mark_reflected([e.id for e in episodes])
        logger.info(
            "thematic_reflection_completed",
            character_id=str(character_id),
            themes=len(themes),
            episodes=len(episodes),
        )
        return saved

    async def _maybe_meta_reflect(self, character_id: UUID) -> Reflection | None:
        """跨期元反思：累计反思足够多、且冷却期内没有元反思时，对既有反思再归纳"""
        now = datetime.now(UTC)
        cooldown_start = now - timedelta(days=self.META_REFLECTION_COOLDOWN_DAYS)
        recent_meta = await self.ref_repo.count_recent(character_id, since=cooldown_start, tier=2)
        if recent_meta > 0:
            return None
        total = await self.ref_repo.count_recent(character_id, since=now - timedelta(days=365), tier=None)
        if total < self.META_REFLECTION_MIN_TOTAL:
            return None

        contents = await self.ref_repo.get_recent_contents(character_id, limit=self.META_SOURCE_LIMIT, max_tier=1)
        if len(contents) < 3:
            return None

        prompts = self._prompts or self._load_prompts(character_id)
        if prompts is None:
            return None
        prompt = prompts.render("reflection_meta", reflections_text="\n".join(f"- {c}" for c in contents))

        result = await self.llm.structured_output(
            prompt,
            schema={
                "type": "object",
                "properties": {
                    "metas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "meta_summary": {"type": "string"},
                                "meta_detail": {"type": "string"},
                            },
                        },
                    }
                },
            },
            model="chat",
        )
        metas = [
            m
            for m in result.get("metas", [])
            if isinstance(m, dict) and isinstance(m.get("meta_summary"), str) and m["meta_summary"].strip()
        ]
        if not metas:
            return None

        saved: Reflection | None = None
        for meta in metas:
            detail = meta.get("meta_detail") or ""
            reflection = Reflection(
                character_id=character_id,
                content=f"[长期倾向] {meta['meta_summary']}：{detail}".strip(),
                tier=2,
            )
            saved = await self.ref_repo.add(reflection)

        logger.info("meta_reflection_completed", character_id=str(character_id), metas=len(metas))
        return saved

    def _parse_themes(self, result: dict[str, Any], total: int) -> list[dict[str, Any]]:
        """解析主题输出：过滤非法条目，memory_ids 收敛到 [1, total] 且去重"""
        themes: list[dict[str, Any]] = []
        seen_summaries: set[str] = set()
        for item in result.get("reflections", []):
            if not isinstance(item, dict):
                continue
            summary = item.get("summary")
            detail = item.get("detail")
            if not isinstance(summary, str) or not summary.strip():
                continue
            if summary.strip() in seen_summaries:
                continue
            raw_ids = item.get("memory_ids")
            ids: list[int] = []
            if isinstance(raw_ids, list):
                for raw in raw_ids:
                    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= total and raw not in ids:
                        ids.append(raw)
            detail_text = detail.strip() if isinstance(detail, str) else ""
            themes.append({"summary": summary.strip(), "detail": detail_text, "memory_ids": ids})
            seen_summaries.add(summary.strip())
        return themes

    def _load_prompts(self, character_id: UUID) -> Any | None:
        from src.runtime import get_prompts

        prompts = get_prompts()
        if not prompts:
            logger.warning("reflection_prompts_unavailable", character_id=str(character_id))
        return prompts
