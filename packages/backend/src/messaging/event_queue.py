"""OneBot 事件兜底队列 - Redis Streams 至少一次语义

解决「后端重启/处理失败导致入站消息丢失」：
- 入站消息事件先 XADD 持久化（内联处理前），处理成功后 XACK + XDEL；
- 崩溃/重启后由 recover_drain() 重放未确认条目（消费幂等由
  OneBot 侧 SETNX 去重保证，重放不会重复回复）；
- 投递次数超过上限的毒消息转入死信流，不阻塞后续。

为什么成功处理必须 XDEL 而非只 XACK（round-3 H3）：XACK 只清除 PEL
（待确认列表），从不移除流内条目；而内联快速路径的条目从未经
XREADGROUP 投递、根本不进 PEL，对它 XACK 是空操作。只 XACK 的后果是
条目永久留在流中，恢复循环用 ">" 把它当新条目再次投递——重复回复
仅靠 SETNX 去重兜住。remove() 用 XACK + XDEL 让已处理条目彻底离开
流：既不会被重放，也不会无限累积。

设计取舍：内联快速路径保持不变（回复延迟最低），队列只承担
「崩溃恢复 + 失败重放」职责，不做全量异步化。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = structlog.get_logger(__name__)

STREAM = "onebot:events"
GROUP = "processor"
CONSUMER = "recovery-worker"
DLQ_STREAM = "onebot:events:dead"
MAX_DELIVERIES = 5

# redis-py 对 XREADGROUP/XADD 的返回标注是宽泛联合类型，
# 集中在此收敛为业务形状（decode_responses=True 下键值均为 str）
StreamEntry = tuple[str, dict[str, str]]
ReadPage = list[tuple[str, list[StreamEntry]]]
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventQueue:
    """基于 Redis Streams Consumer Group 的事件兜底队列"""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def ensure_group(self) -> None:
        """创建消费组（已存在则忽略 BUSYGROUP）"""
        try:
            await self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def enqueue(self, event: dict[str, Any]) -> str:
        """持久化一条事件，返回流内条目 ID（maxlen 防止流无限增长）"""
        from src.config import settings

        return str(
            await self.redis.xadd(
                STREAM,
                {"event": json.dumps(event, ensure_ascii=False)},
                maxlen=settings.onebot_stream_maxlen,
                approximate=True,
            )
        )

    async def ack(self, entry_id: str) -> None:
        """仅清除 PEL 记录，不移除流内条目（保留为原语，用于与 remove() 对比语义）"""
        await self.redis.xack(STREAM, GROUP, entry_id)

    async def remove(self, entry_id: str) -> None:
        """确认并从流中删除已成功处理的条目

        为什么必须 XDEL：见模块 docstring（round-3 H3）。XACK 清除 PEL
        记录（对已投递条目生效），XDEL 移除流内数据本体；缺后者时条目
        会被恢复循环当作新条目无限重投。
        """
        await self.redis.xack(STREAM, GROUP, entry_id)
        await self.redis.xdel(STREAM, entry_id)

    async def dead_letter(self, entry_id: str, fields: dict[str, str], reason: str) -> None:
        """毒消息转死信流并从源流移除，避免无限重放阻塞队列"""
        from src.config import settings

        await self.redis.xadd(
            DLQ_STREAM,
            {
                "event": fields.get("event", ""),
                "source_id": entry_id,
                "reason": reason[:500],
            },
            maxlen=settings.onebot_stream_maxlen,
            approximate=True,
        )
        # 拷贝完成后同样要从源流移除：只 XACK 不 XDEL 会留下永久重投的残骸
        await self.remove(entry_id)
        logger.warning("event_queue_dead_letter", source_id=entry_id, reason=reason)

    async def recover_drain(
        self,
        handler: EventHandler,
        *,
        max_entries: int = 200,
        max_batches: int = 10,
    ) -> int:
        """恢复消费：先重放「已投递未确认」的崩溃残留，再消费新条目

        Args:
            handler: 事件处理器（应具备幂等性——重放依赖上游去重）
            max_entries: 单次恢复最多处理条数
            max_batches: 最多读取批数（防长循环）

        Returns:
            处理的事件条数（含死信转移）
        """
        await self.ensure_group()
        processed = 0
        for start_id in ("0", ">"):
            for _batch in range(max_batches):
                page = cast(ReadPage, await self.redis.xreadgroup(GROUP, CONSUMER, {STREAM: start_id}, count=50))
                if not page or not page[0][1]:
                    break
                for entry_id, fields in page[0][1]:
                    if processed >= max_entries:
                        return processed
                    await self._handle_entry(entry_id, fields, handler)
                    processed += 1
        return processed

    async def _handle_entry(
        self,
        entry_id: str,
        fields: dict[str, str],
        handler: EventHandler,
    ) -> None:
        """处理单条：毒消息判定 -> 重放 -> 确认"""
        # 投递次数检查（仅对已投递过的条目有意义；首次读取时无 pending 记录）
        pending = await self.redis.xpending_range(STREAM, GROUP, min=entry_id, max=entry_id, count=1)
        deliveries = int(pending[0]["times_delivered"]) if pending else 0
        if deliveries > MAX_DELIVERIES:
            await self.dead_letter(entry_id, fields, f"exceeded {MAX_DELIVERIES} deliveries")
            return

        raw = fields.get("event", "")
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            await self.dead_letter(entry_id, fields, f"invalid json: {e}")
            return

        try:
            await handler(event)
        except Exception as e:
            # 不确认：留在 pending，下轮 recover_drain 重放
            logger.warning(
                "event_queue_replay_failed",
                entry_id=entry_id,
                deliveries=deliveries,
                error=str(e),
            )
            return
        # 成功即确认并从流中移除：只 XACK 的话条目会被 ">" 再次投递（round-3 H3）
        await self.remove(entry_id)
