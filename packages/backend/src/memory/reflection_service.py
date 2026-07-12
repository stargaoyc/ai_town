"""反思服务 - 从记忆片段提炼高层认知

触发条件：未反思记忆数 >= 反思阈值（默认 20）
"""
from uuid import UUID

from structlog import get_logger

from src.db.models import Reflection, ReflectionSource
from src.db.repositories import MemoryRepository, ReflectionRepository
from src.llm import LLMClient

logger = get_logger(__name__)


class ReflectionService:
    """反思服务"""

    REFLECTION_THRESHOLD = 20  # 每 N 条未反思记忆触发反思

    def __init__(self, llm: LLMClient, mem_repo: MemoryRepository, ref_repo: ReflectionRepository):
        self.llm = llm
        self.mem_repo = mem_repo
        self.ref_repo = ref_repo

    async def check_and_reflect(self, character_id: UUID) -> Reflection | None:
        """检查是否需要反思，如需要则执行

        Args:
            character_id: 角色 ID

        Returns:
            Reflection 实体（如果触发了反思），否则 None
        """
        # 统计未反思记忆数
        count = await self.mem_repo.count_unreflected(character_id)

        if count < self.REFLECTION_THRESHOLD:
            return None

        # 执行反思
        reflection = await self._do_reflection(character_id)
        logger.info(
            "reflection_completed",
            character_id=str(character_id),
            episode_count=count,
        )
        return reflection

    async def _do_reflection(self, character_id: UUID) -> Reflection | None:
        """执行反思

        流程：
        1. 获取最近未反思记忆
        2. 调用 LLM 归纳高层认知
        3. 写入 Reflection 表
        4. 标记记忆为已反思

        Args:
            character_id: 角色 ID

        Returns:
            Reflection 实体；无未反思记忆时返回 None
        """
        # 获取未反思记忆（按时间正序，先入先反思）
        episodes = await self.mem_repo.fetch_unreflected(character_id, limit=20)
        if not episodes:
            return None

        # 构建反思 Prompt
        memories_text = "\n".join([
            f"[{e.timestamp}] {e.content}"
            for e in episodes
        ])

        prompt = f"""[角色记忆]
{memories_text}

[任务]
请基于以上记忆，归纳出 3 条关于该角色的高层认知。
每条以 JSON 输出: {{ "summary": "...", "detail": "..." }}

输出格式：
{{ "reflections": [{{...}}, {{...}}, {{...}}] }}
"""

        # 调用 LLM（使用 strong 模型）
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
                            }
                        }
                    }
                }
            },
            model="chat",
        )

        # 构建反思内容
        content = "\n".join([
            f"- {r['summary']}: {r['detail']}"
            for r in result.get("reflections", [])
        ])

        # 写入 Reflection（不含 related_episodes，已移至 reflection_sources 中间表）
        reflection = Reflection(
            character_id=character_id,
            content=content,
        )
        saved = await self.ref_repo.add(reflection)

        # 写入 reflection_sources 中间表（复合外键引用 memory_episodes）
        for episode in episodes:
            self.ref_repo.session.add(ReflectionSource(
                reflection_id=saved.id,
                memory_id=episode.id,
                memory_character_id=episode.character_id,
            ))
        await self.ref_repo.session.flush()

        # 标记记忆为已反思
        await self.mem_repo.mark_reflected([e.id for e in episodes])

        return saved