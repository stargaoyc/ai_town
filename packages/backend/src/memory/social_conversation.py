"""社交会话服务 - 事件驱动的角色间对话管理

将对话从"发起方 Tick 内的同步 Action"升级为"事件驱动的会话实体"
（交互-02 方案），支持：
- 跨 Tick 延续（会话持久化到 Redis，TTL 自动过期）
- 三层终止机制防无休止（硬上限/软结束/超时死亡）
- 按需唤醒（交互第二步：B 的回复由 B 自己的 Tick 决策）

会话生命周期：
  pending（A 发起）→ active（B 响应）→ ended（任一方终止/超时/到达上限）
                              ↘ 沉默死亡（TTL 过期）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from structlog import get_logger

from src.config import settings
from src.runtime import get_redis

logger = get_logger(__name__)

# Redis key 前缀
_CONV_PREFIX = "social:conv:"
_CONV_TTL = 180  # 会话 TTL（秒），超时自动死亡

# 对话终止条件
_CONV_MAX_TURNS = 6     # 默认，会被 settings.chat_max_turns 覆盖
_CONV_IDLE_TICKS = 2    # 默认，会被 settings.chat_idle_ticks 覆盖


@dataclass
class SocialConversation:
    """社交会话实体"""

    id: str
    char_a: str
    char_b: str
    scene: str
    status: str  # pending / active / ended
    topic: str
    turn_count: int
    last_turn_at: float
    last_speaker: str = ""  # 最后发言方（pending_for 判定：仅对方最后发言时才需本方回应）
    ended_reason: str | None = None  # hard_limit / soft_end / timeout

    @classmethod
    def new(cls, char_a: str, char_b: str, scene: str, topic: str = "") -> SocialConversation:
        return cls(
            id=uuid4().hex[:16],
            char_a=char_a,
            char_b=char_b,
            scene=scene,
            status="pending",
            topic=topic,
            turn_count=0,
            last_turn_at=time.time(),
            last_speaker="",
        )

    @staticmethod
    def key(conv_id: str) -> str:
        return f"{_CONV_PREFIX}{conv_id}"

    def to_redis(self) -> dict[str, str]:
        return {
            "id": self.id,
            "char_a": self.char_a,
            "char_b": self.char_b,
            "scene": self.scene,
            "status": self.status,
            "topic": self.topic,
            "turn_count": str(self.turn_count),
            "last_turn_at": str(self.last_turn_at),
            "last_speaker": self.last_speaker,
            "ended_reason": self.ended_reason or "",
        }

    @classmethod
    def from_redis(cls, data: dict[str, str]) -> SocialConversation:
        return cls(
            id=data.get("id", ""),
            char_a=data.get("char_a", ""),
            char_b=data.get("char_b", ""),
            scene=data.get("scene", ""),
            status=data.get("status", "pending"),
            topic=data.get("topic", ""),
            turn_count=int(data.get("turn_count", "0")),
            last_turn_at=float(data.get("last_turn_at", "0")),
            last_speaker=data.get("last_speaker", ""),
            ended_reason=data.get("ended_reason") or None,
        )


class SocialConversationService:
    """社交会话服务 - Redis 持久化 + 三层终止判定"""

    @property
    def max_turns(self) -> int:
        """会话硬上限（轮数），供调用方计算剩余配额"""
        return settings.chat_max_turns

    async def create_or_get(self, char_a: str, char_b: str, scene: str, topic: str = "") -> SocialConversation:
        """查找活跃会话或创建新会话"""
        redis = get_redis()
        if redis is None:
            # Redis 不可用时降级为一次性会话（无持久化）
            return SocialConversation.new(char_a, char_b, scene, topic)

        # 查找活跃会话（双向查找）
        for a, b in [(char_a, char_b), (char_b, char_a)]:
            pattern = f"{_CONV_PREFIX}*"
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=20)
                for key in keys:
                    conv_id = str(key).split(":")[-1]
                    conv = await self.get(conv_id)
                    if conv is None:
                        continue
                    if conv.status == "ended":
                        continue
                    # 检查是否匹配双方角色
                    if (conv.char_a == a and conv.char_b == b) or (conv.char_a == b and conv.char_b == a):
                        # 复用会话：置 active（发起方将发言）
                        conv.status = "active"
                        await self._save(conv)
                        return conv
                if cursor == 0:
                    break

        # 无活跃会话，创建新会话
        conv = SocialConversation.new(char_a, char_b, scene, topic)
        await self._save(conv)
        return conv

    async def get(self, conv_id: str) -> SocialConversation | None:
        redis = get_redis()
        if redis is None:
            return None
        data = await redis.hgetall(SocialConversation.key(conv_id))
        if not data:
            return None
        decoded = {str(k): str(v) if isinstance(v, bytes) else str(v) for k, v in data.items()}
        return SocialConversation.from_redis(decoded)

    async def pending_for(self, character_id: str) -> list[SocialConversation]:
        """获取该角色有待回复的活跃会话（交互第二步：B 在自己的 Tick 检测待处理事件）

        仅当对方最后发言时需本方回应——last_speaker != character_id。
        若本方最后发言则等待对方回应，不重复触发。
        """
        redis = get_redis()
        if redis is None:
            return []
        result: list[SocialConversation] = []
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=f"{_CONV_PREFIX}*", count=20)
            for key in keys:
                conv_id = str(key).split(":")[-1]
                conv = await self.get(conv_id)
                if conv is None or conv.status != "active":
                    continue
                if conv.char_a == character_id or conv.char_b == character_id:
                    # 仅对方最后发言时才需本方回应
                    if conv.last_speaker and conv.last_speaker != character_id:
                        result.append(conv)
            if cursor == 0:
                break
        return result

    async def _save(self, conv: SocialConversation) -> None:
        redis = get_redis()
        if redis is None:
            return
        # redis-py 对 hset(mapping=...) 的标注是宽泛联合类型，dict[str,str] 被误报
        await redis.hset(SocialConversation.key(conv.id), mapping=conv.to_redis())  # type: ignore[arg-type]
        await redis.expire(SocialConversation.key(conv.id), _CONV_TTL)

    async def advance_turn(self, conv: SocialConversation, speaker: str = "") -> tuple[SocialConversation, bool]:
        """推进一轮对话，记录发言方，返回(更新后会话, 是否应终止)"""
        conv.turn_count += 1
        conv.last_turn_at = time.time()
        if speaker:
            conv.last_speaker = speaker
        await self._save(conv)
        should_end = self._check_termination(conv)
        if should_end:
            await self._end(conv, "hard_limit")
        return conv, should_end

    async def end_with_reason(self, conv: SocialConversation, reason: str) -> None:
        """软结束（LLM 决定不再聊）"""
        await self._end(conv, reason)

    async def soft_end_if_intended(self, conv: SocialConversation, reply: str) -> bool:
        """检测 LLM 回复中是否包含结束意图；检测到则结束会话"""
        end_signals = {"不想聊了", "不说了", "先这样吧", "下次再说", "拜拜", "再见", "我先走了", "困了", "睡了"}
        for signal in end_signals:
            if signal in reply:
                await self._end(conv, "soft_end")
                return True
        return False

    async def check_timeout(self, conv: SocialConversation) -> bool:
        """超时检测：超过 chat_idle_ticks 个世界 Tick 无回应"""
        world_tick_seconds = settings.world_tick_seconds
        idle_limit = world_tick_seconds * settings.chat_idle_ticks
        if time.time() - conv.last_turn_at > idle_limit:
            await self._end(conv, "timeout")
            return True
        return False

    def _check_termination(self, conv: SocialConversation) -> bool:
        """三层终止判定：轮数达到硬上限"""
        return conv.turn_count >= settings.chat_max_turns

    async def _end(self, conv: SocialConversation, reason: str) -> None:
        conv.status = "ended"
        conv.ended_reason = reason
        await self._save(conv)
        logger.info("conversation_ended", conv_id=conv.id, reason=reason, turns=conv.turn_count)