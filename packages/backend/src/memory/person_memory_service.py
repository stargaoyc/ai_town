"""角色对用户的记忆服务

管理角色对每个用户的独立记忆，每次用户交互后更新记忆。
更新为增量合并语义（由 person_memory.yaml 约束 LLM 保留旧事实），
并落库结构化 preferences；热度由后台任务周期衰减。
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from structlog import get_logger

from src.config import settings
from src.db.models import PersonMemory, PersonMemoryEntry
from src.runtime import get_llm

logger = get_logger(__name__)


def _char_bigrams(text: str) -> set[str]:
    """字符二元组集合：中文无分词场景的轻量相似度原语"""
    text = text.strip()
    if len(text) <= 1:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


class PersonMemoryService:
    """管理角色对每个用户的独立记忆

    每次用户交互后更新记忆，包含：
    - 用户偏好（preferences JSONB，结构化）
    - 关系进展与共同话题（content 自然语言）
    - 热度机制：交互越频繁热度越高，后台任务周期衰减
    """

    def __init__(self, session_factory: Any, llm_client: Any = None, prompts: Any = None):
        """
        Args:
            session_factory: 异步会话工厂（async context manager），
                             如 db.session 或 db.session_factory
            llm_client: LLM 客户端实例（可选，默认从 runtime 获取）
            prompts: Prompt 模板管理器（可选，默认从 runtime 获取）
        """
        self.session_factory = session_factory
        self._llm = llm_client
        self._prompts = prompts

    async def get_memory(self, character_id: UUID, user_id: str) -> dict[str, Any] | None:
        """获取角色对某用户的记忆"""
        async with self.session_factory() as session:
            stmt = select(PersonMemory).where(
                PersonMemory.character_id == character_id,
                PersonMemory.user_id == user_id,
            )
            row = await session.execute(stmt)
            memory = row.scalars().first()
            if memory is None:
                return None
            return {
                "id": str(memory.id),
                "character_id": str(memory.character_id),
                "user_id": memory.user_id,
                "platform": memory.platform,
                "content": memory.content,
                "summary": memory.summary,
                "heat": memory.heat,
                "preferences": memory.preferences,
                "last_interaction_at": memory.last_interaction_at.isoformat() if memory.last_interaction_at else None,
            }

    async def update_memory(
        self,
        character_id: UUID,
        character_name: str,
        user_id: str,
        platform: str,
        user_message: str,
        character_reply: str,
    ) -> dict[str, Any] | None:
        """交互后更新角色对用户的记忆（两层结构·抽取式）

        LLM 按 person_memory.yaml 从对话中抽取**新事实**（不做全文重写）：
        {"facts": [...], "preferences": {...}}；
        事实逐条追加到 person_memory_entries（append-only），
        主档 content 由后台压缩任务定期合并生成。
        解析失败时回退将用户消息截断作为单条事实，不丢交互痕迹。

        Returns:
            {"appended": 追加条数}，或 None（LLM 不可用/失败）
        """
        llm = self._llm or get_llm()
        if not llm:
            return None

        existing = await self.get_memory(character_id, user_id)
        existing_content = existing.get("content", "") if existing else "（初次交流）"

        from src.runtime import get_prompts

        prompts = self._prompts or get_prompts()
        if not prompts:
            logger.warning("person_memory_prompts_unavailable", character_id=str(character_id))
            return None
        prompt = prompts.render(
            "person_memory",
            character_name=character_name,
            user_id=user_id,
            existing_content=existing_content,
            user_message=user_message,
            character_reply=character_reply,
        )

        try:
            response = await llm.chat(prompt)
            facts, preferences = self._parse_memory_response(response)

            if not facts:
                # 抽取为空也要留下交互痕迹：回退单条事实（截断防膨胀）
                facts = [f"用户提到：{user_message[:120]}"]

            await self._append_entries(character_id, user_id, platform, facts)
            await self._upsert_memory(character_id, user_id, platform, preferences)
            logger.info(
                "person_memory_updated",
                character_id=str(character_id),
                user_id=user_id,
                appended=len(facts),
                has_preferences=bool(preferences),
            )
            return {"appended": len(facts)}

        except Exception as e:
            logger.error(
                "person_memory_update_failed",
                error=str(e),
                exc_info=True,
            )
            return None

    @staticmethod
    def _parse_memory_response(raw: str) -> tuple[list[str], dict[str, Any]]:
        """解析 LLM 抽取输出

        优先解析 JSON {"facts": [...], "preferences": {...}}；失败回退整段文本作为单条事实。
        """
        text = raw.strip()
        if text.startswith("```"):
            lines = [ln for ln in text.split("\n") if not ln.startswith("```")]
            text = "\n".join(lines).strip()

        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            parsed = json.loads(text[start:end])
            if isinstance(parsed, dict):
                facts_raw = parsed.get("facts")
                facts = (
                    [f.strip() for f in facts_raw if isinstance(f, str) and f.strip()]
                    if isinstance(facts_raw, list)
                    else []
                )
                preferences = parsed.get("preferences")
                return facts, preferences if isinstance(preferences, dict) else {}
        except (ValueError, json.JSONDecodeError):
            pass
        # 解析失败：返回空事实，由调用方回退为「用户提到：<消息>」，不丢交互痕迹
        return [], {}

    async def _append_entries(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        facts: list[str],
    ) -> None:
        """追加事实条目（append-only，只写不改）"""
        async with self.session_factory() as session:
            for fact in facts:
                session.add(
                    PersonMemoryEntry(
                        character_id=character_id,
                        user_id=user_id,
                        platform=platform,
                        content=fact,
                        embedding=await self._embed_fact(fact),
                    )
                )
            await session.commit()

    async def _embed_fact(self, fact: str) -> list[float] | None:
        """对单条事实生成语义向量（审查 记忆-05）

        失败返回 None（不阻断写入），检索侧对 NULL 向量回退二元组重叠。
        """
        try:
            llm = self._llm or get_llm()
            if llm is None:
                return None
            return await llm.embed(fact)
        except Exception as e:
            logger.warning("person_memory_entry_embed_failed", error=str(e))
            return None

    async def _upsert_memory(
        self,
        character_id: UUID,
        user_id: str,
        platform: str,
        preferences: dict[str, Any] | None = None,
    ) -> None:
        """单语句 upsert 主档行并累积热度（content 由压缩任务维护，此处不写）

        R6-M5：此前 UPDATE-then-INSERT 在并发首聊时双双看到 rowcount=0，
        败者撞 idx_pmem_char_user 唯一索引抛异常被 update_memory 兜底吞掉，
        丢失该次交互的主档更新；改为 INSERT..ON CONFLICT 后并发路径
        在数据库侧原子收敛，不再依赖应用层行数判断。
        preferences 为顶层合并语义（round-3 review M6）：jsonb || 新偏好覆盖同键、
        保留旧键，避免 LLM 只返回增量偏好时把既有偏好整表清空；
        合并下推到 SQL 内完成，并发更新不会互相整表覆盖。
        """
        async with self.session_factory() as session:
            values: dict[str, Any] = {
                "character_id": character_id,
                "user_id": user_id,
                "platform": platform,
                "content": "",
                "heat": 1,
                "last_interaction_at": func.now(),
            }
            if preferences:
                values["preferences"] = preferences
            stmt = (
                pg_insert(PersonMemory)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[PersonMemory.character_id, PersonMemory.user_id],
                    set_={
                        # P2-11：热度钳制上限——高频用户无界增长会与低频用户拉开
                        # 数千倍差距，使 heat 排序失去区分度
                        "heat": func.least(PersonMemory.heat + 1, settings.person_memory_heat_cap),
                        "last_interaction_at": func.now(),
                        "updated_at": func.now(),
                        **(
                            {
                                "preferences": func.coalesce(PersonMemory.preferences, text("'{}'::jsonb")).op("||")(
                                    cast(preferences, JSONB)
                                )
                            }
                            if preferences
                            else {}
                        ),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def get_relevant_context(self, character_id: UUID, user_id: str, query_hint: str | None = None) -> str:
        """获取角色对用户的记忆上下文，注入对话 system prompt（两层组装）

        = 主档 content（后台压缩合并的稳定认知）
          + 事实条目选取（P1-9）：提供 query_hint 时按与当前消息的字符二元组
            重叠度从近 50 条未压缩条目中选最相关的 8 条——此前固定取最新 8 条，
            用户多话题交错时无法召回「关于这个用户我记过的相关事」；
            无 hint 时退化为时间倒序最新 8 条。
        """
        memory = await self.get_memory(character_id, user_id)
        profile = str(memory.get("content") or "").strip() if memory else ""

        async with self.session_factory() as session:
            stmt = (
                select(PersonMemoryEntry.content, PersonMemoryEntry.embedding)
                .where(
                    PersonMemoryEntry.character_id == character_id,
                    PersonMemoryEntry.user_id == user_id,
                    PersonMemoryEntry.compacted.is_(False),
                )
                .order_by(PersonMemoryEntry.created_at.desc())
                .limit(50)
            )
            rows = [(row[0], row[1]) for row in (await session.execute(stmt)).all()]

        # 语义召回（审查 记忆-05）：有 query_hint 且候选含向量时按与当前消息的
        # 余弦相似度排序取前 8；无向量/embedding 失败时回退字符二元组重叠。
        recent: list[str]
        if query_hint and query_hint.strip():
            vec = await self._embed_fact(query_hint)
            if vec is not None:
                scored = []
                for content, emb in rows:
                    if emb is None:
                        continue
                    # 候选池已限定 50 条，应用层余弦计算开销可忽略
                    try:
                        sim = self._cosine(vec, emb)
                    except Exception:
                        sim = 0.0
                    scored.append((sim, content))
                if scored:
                    scored.sort(key=lambda pair: pair[0], reverse=True)
                    recent = [c for sim, c in scored[:8]]
                else:
                    recent = self._bigram_select(rows, query_hint)
            else:
                recent = self._bigram_select(rows, query_hint)
        else:
            recent = [c for c, _ in rows[:8]]

        parts = []
        if profile:
            parts.append(profile)
        if recent:
            parts.append("最近了解到的：\n" + "\n".join(f"- {fact}" for fact in recent))
        return "\n".join(parts) if parts else "（初次与该用户交流）"

    @staticmethod
    def _bigram_select(
        rows: list[tuple[str, Any]], query_hint: str
    ) -> list[str]:
        """字符二元组重叠回退选择（无语义，仅供向量缺失时兜底）"""
        candidates = [c for c, _ in rows]
        hint_bigrams = _char_bigrams(query_hint)
        if not hint_bigrams:
            return candidates[:8]
        scored = sorted(
            ((len(hint_bigrams & _char_bigrams(c)) / max(len(hint_bigrams), 1), c) for c in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [c for score, c in scored[:8] if score > 0]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """两个向量的余弦相似度（候选池内轻量计算）"""
        import math

        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    async def get_top_users_context(self, character_id: UUID, limit: int = 3) -> str:
        """按热度取最「记得」的用户摘要，供小镇决策注入

        让陪伴关系影响角色在镇内的行为（审查 §4.4 断层：此前用户记忆
        只进对话链路，镇内决策完全想不起任何用户）。
        """
        async with self.session_factory() as session:
            stmt = (
                select(
                    PersonMemory.user_id,
                    PersonMemory.summary,
                    PersonMemory.content,
                    PersonMemory.heat,
                )
                .where(PersonMemory.character_id == character_id)
                .order_by(PersonMemory.heat.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()

        lines = []
        for user_id, summary, content, heat in rows:
            text = str(summary or content or "").strip().replace("\n", " ")[:80]
            if not text:
                continue
            # 剥离平台前缀（qq_123456 → 123456）：工程标识符不应进入 LLM 上下文（三轮审查 L3）
            display_id = str(user_id).split("_", 1)[-1]
            lines.append(f"- 用户 {display_id}（亲密度 {heat}）：{text}")
        return "\n".join(lines)
