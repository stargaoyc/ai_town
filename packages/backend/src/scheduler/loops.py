"""后台业务循环 - 从 main.py 下沉的三个长驻任务

职责边界：
- character_tick_loop: 定期对所有活跃角色执行 Tick（含 429 限流退避）
- diary_scheduler_loop: 按世界时间触发日/周/月/年日记生成
- reconciliation_loop: Redis vs PG 状态对账与自动修复

装配方式：main.py lifespan 以 asyncio.create_task 启动，shutdown 时统一 cancel。
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, Delete, delete, false, func, select, true, tuple_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src import runtime
from src.config import settings
from src.db.models import (
    Character,
    CharacterDiary,
    MemoryEpisode,
    Message,
    PersonMemory,
    PersonMemoryEntry,
    Plan,
    Reflection,
    WorldEvent,
    WorldSnapshot,
)
from src.db.repositories import CharacterRepository, MemoryRepository
from src.db.session import db
from src.memory.diary_service import (
    DiaryService,
    diary_trigger_periods,
    world_real_window_seconds,
)

logger = get_logger(__name__)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """判断异常是否为 LLM 供应商限流（429）

    覆盖两条路径：
    - openai SDK 的 APIStatusError（含 RateLimitError）携带 status_code
    - 其他异常回退到类型名匹配 RateLimitError（LangChain 包装层）
    """
    from openai import APIStatusError

    if isinstance(exc, APIStatusError):
        return exc.status_code == 429
    return type(exc).__name__ == "RateLimitError"


async def character_tick_loop() -> None:
    """Character Tick 后台循环

    定期对所有活跃角色执行 Tick，推进角色状态。
    遇到 LLM 限流 (429) 时自动退避，避免抢占消息处理的 API 配额。
    """
    logger.info("character_tick_loop_started", interval=settings.character_tick_seconds)

    backoff_multiplier = 1  # 限流退避倍数
    max_backoff = 10  # 最大退避倍数

    while True:
        try:
            await asyncio.sleep(settings.character_tick_seconds * backoff_multiplier)

            # 每轮从运行时容器获取最新实例（支持引擎重启后自动恢复）
            character_engine = runtime.get_character_engine()
            redis = runtime.get_redis()
            if not character_engine or not redis:
                continue

            # 获取所有活跃角色
            async with db.session() as session:
                repo = CharacterRepository(session)
                characters = await repo.get_active_characters()

            if not characters:
                logger.debug("no_active_characters")
                continue

            # 更新活跃角色数 Gauge
            from src.observability.metrics import ACTIVE_CHARACTERS

            ACTIVE_CHARACTERS.set(len(characters))

            logger.info("character_tick_batch_start", count=len(characters), backoff=backoff_multiplier)

            # 并发执行所有角色的 Tick（Semaphore 限流在引擎内部）
            outcomes = await character_engine.tick_all_active(characters)

            success_count = 0
            rate_limited = False
            for char, exc in outcomes:
                if exc is None:
                    success_count += 1
                    continue

                error_str = str(exc)
                # 记录 Character Tick 错误指标
                from src.observability.metrics import CHARACTER_TICK_ERRORS

                CHARACTER_TICK_ERRORS.labels(character_id=str(char.id)).inc()
                # 检测 LLM 限流 (429)，本批次结束后退避。
                # P-6：按异常类型/状态码判定，不用字符串匹配——错误文本中
                # 碰巧含 "429"（QQ 号、消息内容）会误判并中止整个批次
                if _is_rate_limit_error(exc):
                    logger.warning(
                        "character_tick_rate_limited",
                        character_id=str(char.id),
                        character_name=char.name,
                        backoff_multiplier=backoff_multiplier,
                    )
                    rate_limited = True
                else:
                    logger.error(
                        "character_tick_failed",
                        character_id=str(char.id),
                        character_name=char.name,
                        error=error_str,
                        exc_info=exc,
                    )

            # 限流退避：逐次增加等待时间，成功后逐步恢复
            if rate_limited:
                backoff_multiplier = min(backoff_multiplier * 2, max_backoff)
                logger.warning("character_tick_backoff", multiplier=backoff_multiplier)
            elif success_count > 0:
                backoff_multiplier = 1  # 全部或部分成功，恢复正常间隔

            logger.info(
                "character_tick_batch_complete",
                total=len(characters),
                success=success_count,
                failed=len(characters) - success_count,
                rate_limited=rate_limited,
            )

        except asyncio.CancelledError:
            logger.info("character_tick_loop_cancelled")
            raise
        except Exception as e:
            logger.error("character_tick_loop_error", error=str(e), exc_info=True)
            # 继续循环，不中断


async def diary_scheduler_loop() -> None:
    """日记自动生成后台循环

    每 1800 秒（30 分钟现实时间）检查一次世界时间，根据时段决定生成哪种周期的日记：
    - 每日：世界时间 22:00-06:00（一天结束时）
    - 每周：每 7 个世界日
    - 每月：每 30 个世界日
    - 每年：每 365 个世界日

    日记归属与幂等键均使用世界时间；记忆查询窗口按世界天数 × 时钟倍率换算为真实秒
    （round-3 review H1：此前窗口与幂等键错用真实日期，一天最多生成一篇日记）。
    批量路径必须把 world_now/window_start 透传给 DiaryService（round-5 H3：
    此前批量调用缺省透传，diary_date 回落真实日历，与幂等键的世界日历错位，
    触发窗内每轮轮询都会重复生成）。
    生成是幂等的：DiaryService 会跳过当前世界日已存在日记的角色。
    循环内部捕获所有异常，保证不会崩溃退出。
    """
    interval = 1800
    logger.info("diary_scheduler_loop_started", interval=interval)

    while True:
        try:
            await asyncio.sleep(interval)

            redis = runtime.get_redis()
            if not redis:
                continue

            # 读取世界状态（world:state 主哈希中的 world_time 字段为 ISO 格式时间）
            world_state = await redis.hgetall("world:state")
            if not world_state:
                continue

            world_time_raw = str(world_state.get("world_time", ""))
            if not world_time_raw:
                continue

            # 兼容 world_time 被 JSON 双重序列化的情况
            try:
                parsed = json.loads(world_time_raw)
                if isinstance(parsed, str):
                    world_time_raw = parsed
            except (json.JSONDecodeError, TypeError):
                pass

            try:
                world_now = datetime.fromisoformat(world_time_raw)
            except ValueError:
                logger.warning("diary_scheduler_invalid_world_time", raw=world_time_raw)
                continue

            # 当日计划滚动过期（随世界时间检查，30 分钟粒度足够日级语义）
            try:
                await expire_daily_plans()
            except Exception as e:
                logger.warning("daily_plan_expire_failed", error=str(e))

            periods_to_generate = diary_trigger_periods(world_now)

            if not periods_to_generate:
                continue

            # P2-8：倍率取自时间演化器落盘的快照，与幂等键同一口径
            time_hash = await redis.hgetall("world:state:time")
            multiplier: float | None = None
            multiplier_raw = time_hash.get("clock_multiplier") if time_hash else None
            if multiplier_raw:
                try:
                    multiplier = float(multiplier_raw)
                except (TypeError, ValueError):
                    multiplier = None

            logger.info(
                "diary_scheduler_trigger",
                periods=periods_to_generate,
                world_hour=world_now.hour,
                world_day_of_year=world_now.timetuple().tm_yday,
            )

            service = DiaryService(session_factory=db.session)
            real_now = datetime.now(UTC)
            for period in periods_to_generate:
                try:
                    # 记忆按真实时间戳存储：世界天数经时钟倍率换算为真实窗口再查询
                    window_start = real_now - timedelta(
                        seconds=world_real_window_seconds(period, clock_multiplier=multiplier)
                    )
                    summary = await service.generate_diaries_for_all_characters(
                        period,
                        world_now=world_now,
                        window_start=window_start,
                    )
                    logger.info("diary_scheduler_period_done", period=period, summary=summary)
                except Exception as e:
                    logger.error(
                        "diary_scheduler_period_failed",
                        period=period,
                        error=str(e),
                        exc_info=True,
                    )

        except asyncio.CancelledError:
            logger.info("diary_scheduler_loop_cancelled")
            raise
        except Exception as e:
            logger.error("diary_scheduler_loop_error", error=str(e), exc_info=True)
            # 继续循环，不中断


async def reconciliation_loop() -> None:
    """Redis vs PG 状态对账后台循环（roadmap #24）

    每 600 秒（10 分钟）diff 一次两库状态并自动修复漂移：
    - Redis 键缺失 → 从 PG 回灌
    - 字段值漂移 → 以 Redis 为准修正 PG

    循环内部捕获所有异常，保证不会崩溃退出。
    """
    from src.core.reconcile import run_reconciliation

    interval = 600
    logger.info("reconciliation_loop_started_detail", interval=interval)

    while True:
        try:
            await asyncio.sleep(interval)

            redis = runtime.get_redis()
            if not redis:
                continue

            await run_reconciliation(redis, db.session)
        except asyncio.CancelledError:
            logger.info("reconciliation_loop_cancelled")
            raise
        except Exception as e:
            logger.error("reconciliation_loop_error", error=str(e), exc_info=True)
            # 继续循环，不中断


async def hnsw_reindex_loop() -> None:
    """HNSW 索引周期性在线重建（P1-1）

    保留周期大量 DELETE 后 pgvector HNSW 的索引项不被 VACUUM 回收，
    长期运行索引膨胀、召回衰减；REINDEX CONCURRENTLY 不阻塞读写。
    必须在 AUTOCOMMIT 连接上执行（CONCURRENTLY 不能运行在事务块内）。
    """
    from sqlalchemy import text

    interval_days = settings.hnsw_reindex_interval_days
    if not settings.hnsw_reindex_enabled or interval_days <= 0:
        logger.info("hnsw_reindex_loop_disabled")
        return
    interval = interval_days * 86400
    logger.info("hnsw_reindex_loop_started", interval_days=interval_days)

    while True:
        try:
            await asyncio.sleep(interval)
            async with db.engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text("REINDEX INDEX CONCURRENTLY idx_mem_embedding_hnsw;"))
            logger.info("hnsw_reindex_completed")
        except asyncio.CancelledError:
            logger.info("hnsw_reindex_loop_cancelled")
            raise
        except Exception as e:
            logger.error("hnsw_reindex_failed", error=str(e), exc_info=True)


async def redis_health_loop() -> None:
    """Redis 周期探活 + Streams 队列深度采集

    REDIS_CONNECTED 此前只在启动和 World Tick 成功时置 1：两次 Tick 之间断连时
    gauge 保持陈旧值，RedisDisconnected 告警可能漏报（审查 §八盲区 4）。
    本循环每 15 秒 ping 一次刷新连接状态，并采集 onebot Streams 积压/死信深度。
    """
    from src.messaging.event_queue import DLQ_STREAM, STREAM
    from src.observability.metrics import REDIS_CONNECTED, REDIS_STREAM_MESSAGES

    interval = 15
    logger.info("redis_health_loop_started", interval=interval)

    while True:
        try:
            await asyncio.sleep(interval)

            redis = runtime.get_redis()
            if not redis:
                continue

            try:
                await redis.ping()
            except Exception as e:
                REDIS_CONNECTED.set(0)
                logger.warning("redis_health_ping_failed", error=str(e))
                continue
            REDIS_CONNECTED.set(1)

            for stream in (STREAM, DLQ_STREAM):
                REDIS_STREAM_MESSAGES.labels(stream=stream).set(await redis.xlen(stream))
        except asyncio.CancelledError:
            logger.info("redis_health_loop_cancelled")
            raise
        except Exception as e:
            logger.error("redis_health_loop_error", error=str(e), exc_info=True)


async def person_memory_heat_decay_loop() -> None:
    """Person Memory 热度衰减后台循环

    每 6 小时执行一次：超过 14 天未交互的记忆热度减半，
    抑制历史用户长期占据检索优先级（审查 §五-P1：heat 只增不减）。
    """
    interval = 6 * 3600
    logger.info("person_memory_heat_decay_loop_started", interval=interval)

    while True:
        try:
            await asyncio.sleep(interval)
            await run_person_memory_heat_decay()
        except asyncio.CancelledError:
            logger.info("person_memory_heat_decay_loop_cancelled")
            raise
        except Exception as e:
            logger.error("person_memory_heat_decay_loop_error", error=str(e), exc_info=True)


async def run_person_memory_heat_decay(session_factory: Any | None = None) -> int:
    """单次热度衰减周期（可测试入口）；返回衰减的行数

    Args:
        session_factory: 会话上下文工厂；缺省用全局 db.session
    """
    cutoff = datetime.now(UTC) - timedelta(days=14)
    factory = session_factory or db.session
    async with factory() as session:
        count_stmt = (
            select(func.count())
            .select_from(PersonMemory)
            .where(
                PersonMemory.heat > 0,
                PersonMemory.last_interaction_at < cutoff,
            )
        )
        stale_count = int((await session.execute(count_stmt)).scalar_one())
        if stale_count == 0:
            return 0
        stmt = (
            update(PersonMemory)
            .where(
                PersonMemory.heat > 0,
                PersonMemory.last_interaction_at < cutoff,
            )
            .values(heat=func.floor(PersonMemory.heat / 2))
        )
        await session.execute(stmt)
        logger.info("person_memory_heat_decayed", rows=stale_count)
        return stale_count


async def person_memory_compaction_loop() -> None:
    """Person Memory 主档压缩循环（两层结构，审查清单 #4）

    每 6 小时执行一次：未压缩事实条目 >= 阈值的 (角色, 用户) 对，
    由 LLM 把条目合并进 person_memories.content 主档并标记 compacted。
    条目只追加不修改（append-only），压缩后软归档保留可追溯。
    """

    interval = 6 * 3600
    logger.info("person_memory_compaction_loop_started", interval=interval)

    while True:
        try:
            await asyncio.sleep(interval)
            await run_person_memory_compaction()
        except asyncio.CancelledError:
            logger.info("person_memory_compaction_loop_cancelled")
            raise
        except Exception as e:
            logger.error("person_memory_compaction_loop_error", error=str(e), exc_info=True)


async def run_person_memory_compaction(session_factory: Any | None = None) -> int:
    """单次主档压缩周期（可测试入口）；返回压缩的 (角色, 用户) 对数

    Args:
        session_factory: 会话上下文工厂；缺省用全局 db.session
    """
    from src.config import settings as _settings

    llm = runtime.get_llm()
    prompts = runtime.get_prompts()
    if llm is None or prompts is None:
        return 0

    threshold = _settings.person_memory_compact_threshold
    compacted_pairs = 0
    factory = session_factory or db.session
    async with factory() as session:
        # 找出未压缩条目达到阈值的 (角色, 用户) 对
        count_stmt = (
            select(
                PersonMemoryEntry.character_id,
                PersonMemoryEntry.user_id,
                func.count().label("cnt"),
            )
            .where(PersonMemoryEntry.compacted.is_(False))
            .group_by(PersonMemoryEntry.character_id, PersonMemoryEntry.user_id)
            .having(func.count() >= threshold)
        )
        pairs = list((await session.execute(count_stmt)).all())

        for character_id, user_id, _cnt in pairs:
            entries = list(
                (
                    await session.execute(
                        select(PersonMemoryEntry)
                        .where(
                            PersonMemoryEntry.character_id == character_id,
                            PersonMemoryEntry.user_id == user_id,
                            PersonMemoryEntry.compacted.is_(False),
                        )
                        .order_by(PersonMemoryEntry.created_at.asc())
                    )
                ).scalars()
            )
            profile = await session.scalar(
                select(PersonMemory.content).where(
                    PersonMemory.character_id == character_id,
                    PersonMemory.user_id == user_id,
                )
            )
            merged = await _merge_profile(
                prompts,
                llm,
                profile=profile or "",
                entries=[e.content for e in entries],
            )
            if not merged:
                continue
            await session.execute(
                update(PersonMemory)
                .where(
                    PersonMemory.character_id == character_id,
                    PersonMemory.user_id == user_id,
                )
                .values(content=merged, updated_at=func.now())
            )
            for entry in entries:
                entry.compacted = True
            compacted_pairs += 1

    if compacted_pairs:
        logger.info("person_memory_compacted", pairs=compacted_pairs)
    return compacted_pairs


async def _merge_profile(prompts: Any, llm: Any, *, profile: str, entries: list[str]) -> str | None:
    """LLM 把未压缩事实合并进主档；失败返回 None（本周期跳过）"""
    try:
        prompt = prompts.render(
            "person_memory_compact",
            existing_content=profile or "（暂无主档）",
            facts_text="\n".join(f"- {fact}" for fact in entries),
        )
        response = await llm.chat(prompt)
        text = response.strip()
        if text.startswith("```"):
            text = "\n".join(ln for ln in text.split("\n") if not ln.startswith("```")).strip()
        start, end = text.find("{"), text.rfind("}") + 1
        parsed = json.loads(text[start:end])
        content = parsed.get("content")
        return content.strip() if isinstance(content, str) and content.strip() else None
    except Exception as e:
        logger.warning("person_memory_merge_failed", error=str(e))
        return None


def _pk_batched_delete(
    model: type[Any],
    *conditions: ColumnElement[bool],
    batch_size: int,
) -> Delete:
    """构造单批限量 DELETE：主键 IN (SELECT 主键 ... LIMIT n)（R5-L4）

    为什么按主键子查询而非 ctid：ctid 是物理行位置，并发更新下会漂移；
    主键定位对分区表同样成立——memory_episodes 的复合主键用 row-constructor
    IN 表达，普通表走单列 IN。ORDER BY 主键保证逐批稳定推进（uuid7 时间有序，
    近似最旧优先）。
    """
    pk_cols = list(model.__table__.primary_key.columns)
    if len(pk_cols) == 1:
        col = pk_cols[0]
        return delete(model).where(col.in_(select(col).where(*conditions).order_by(col).limit(batch_size)))
    return delete(model).where(
        tuple_(*pk_cols).in_(select(*pk_cols).where(*conditions).order_by(*pk_cols).limit(batch_size))
    )


async def _delete_in_batches(
    session: AsyncSession,
    build_stmt: Callable[[], Delete],
    batch_size: int,
) -> int:
    """循环执行单批删除直至删空，返回总删除行数（R5-L4）

    大积压时一次性 DELETE 是长事务：锁与 WAL 在一个语句内一口气生成。
    每批删除数等于 batch_size 时可能仍有剩余，必须再执行一轮；
    某轮删除数小于 batch_size 即为最后一批（含 0 行的空轮）。
    """
    deleted_total = 0
    while True:
        result = cast("CursorResult[Any]", await session.execute(build_stmt()))
        batch_deleted = int(result.rowcount or 0)
        deleted_total += batch_deleted
        logger.debug("retention_delete_batch", batch_deleted=batch_deleted, total=deleted_total)
        if batch_deleted < batch_size:
            break
    return deleted_total


async def expire_daily_plans(session_factory: Any | None = None) -> int:
    """当日计划滚动过期：创建超过 TTL 的 active daily 计划置 expired（审查清单 B2）

    以真实时间计龄（created_at 为服务端时间），避免虚拟时间倍率差异；
    过期区别于 abandoned（主动放弃）——语义是「当日已过，自然失效」。

    Args:
        session_factory: 会话上下文工厂；缺省用全局 db.session
    """
    cutoff = datetime.now(UTC) - timedelta(hours=settings.daily_plan_ttl_hours)
    factory = session_factory or db.session
    async with factory() as session:
        stmt = (
            update(Plan)
            .where(
                Plan.type == "daily",
                Plan.status == "active",
                Plan.created_at < cutoff,
            )
            .values(status="expired", updated_at=func.now())
        )
        result = cast("CursorResult[Any]", await session.execute(stmt))
        count = int(result.rowcount or 0)
        if count:
            logger.info("daily_plans_expired", count=count)
        return count


async def memory_retention_loop() -> None:
    """记忆生命周期治理后台循环（审查 §七-P1）

    每 24 小时执行一次：
    1. 记忆两阶段治理：压缩归档 + 分级删除（importance>=7 永久保留）
    2. 世界历史清理：超期 world_events 删除、world_snapshots 仅保留最近 N 份
    3. 消息表清理：超期 messages 删除（三轮审查 M1：messages 无界增长）
    4. 认知产物清理 + 终态计划修剪（R4-M7 / R5-L5）

    memory_episodes 为 HASH 分区表，无法像 RANGE 分区那样按时间 drop，
    膨胀治理只能在应用层定期处理。可通过 MEMORY_RETENTION_ENABLED=false 关闭。
    """
    from src.config import settings as _settings

    interval = 24 * 3600
    logger.info("memory_retention_loop_started", interval=interval, enabled=_settings.memory_retention_enabled)

    while True:
        try:
            await asyncio.sleep(interval)
            await run_memory_retention_cycle()
            await run_world_retention_cycle()
            await run_messages_retention_cycle()
            await run_cognition_retention_cycle()
        except asyncio.CancelledError:
            logger.info("memory_retention_loop_cancelled")
            raise
        except Exception as e:
            logger.error("memory_retention_loop_error", error=str(e), exc_info=True)


async def run_cognition_retention_cycle(session_factory: Any | None = None) -> dict[str, int]:
    """单次认知产物清理 + plans 终态修剪（R4-M7 可测试入口）

    覆盖此前无任何清理路径的五类数据：
    - tier=1 批次反思（tier=2 元反思跨期归纳，永久保留）
    - 角色日记
    - Person Memory 已压缩条目（compacted=TRUE 的软归档行此前永不清理）
    - 归档记忆行（source_type='archive'）：保留期按 created_at 计龄，
      不继承原事件时间戳——archive.timestamp 仅承载展示/排序语义（round-5 M2）
    - 终态计划行（completed/abandoned/expired，R5-L5）：expire_daily_plans 只翻
      状态不删行，终态行此前无界累积

    各 retention_days<=0 时跳过对应类。所有删除经 _delete_in_batches 分批执行，
    避免大积压首跑时单语句长事务锁表（R5-L4）。返回各类删除行数。
    """
    from src.config import settings as _settings

    factory = session_factory or db.session
    now = datetime.now(UTC)
    batch_size = _settings.retention_delete_batch_size
    deleted = {"reflections": 0, "diaries": 0, "pm_entries": 0, "archive_episodes": 0, "plans": 0}

    async with factory() as session:
        days = _settings.reflection_retention_days
        if days > 0:
            refl_cutoff = now - timedelta(days=days)
            deleted["reflections"] = await _delete_in_batches(
                session,
                lambda: _pk_batched_delete(
                    Reflection,
                    Reflection.tier == 1,
                    Reflection.created_at < refl_cutoff,
                    batch_size=batch_size,
                ),
                batch_size,
            )

        days = _settings.diary_retention_days
        if days > 0:
            diary_cutoff = now - timedelta(days=days)
            deleted["diaries"] = await _delete_in_batches(
                session,
                lambda: _pk_batched_delete(
                    CharacterDiary,
                    CharacterDiary.generated_at < diary_cutoff,
                    batch_size=batch_size,
                ),
                batch_size,
            )

        days = _settings.person_memory_entry_retention_days
        if days > 0:
            pm_cutoff = now - timedelta(days=days)
            deleted["pm_entries"] = await _delete_in_batches(
                session,
                lambda: _pk_batched_delete(
                    PersonMemoryEntry,
                    PersonMemoryEntry.compacted.is_(True),
                    PersonMemoryEntry.created_at < pm_cutoff,
                    batch_size=batch_size,
                ),
                batch_size,
            )

        days = _settings.archive_episode_retention_days
        if days > 0:
            # 归档保留期按创建时间计龄，不继承原事件时间戳（round-5 M2）：
            # archive.timestamp 继承自原事件，旧积压压缩出的归档按它计龄会生来即到期
            archive_cutoff = now - timedelta(days=days)
            deleted["archive_episodes"] = await _delete_in_batches(
                session,
                lambda: _pk_batched_delete(
                    MemoryEpisode,
                    MemoryEpisode.source_type == "archive",
                    MemoryEpisode.created_at < archive_cutoff,
                    batch_size=batch_size,
                ),
                batch_size,
            )

        days = _settings.plans_retention_days
        if days > 0:
            plans_cutoff = now - timedelta(days=days)
            deleted["plans"] = await _delete_in_batches(
                session,
                lambda: _pk_batched_delete(
                    Plan,
                    Plan.status.in_(("completed", "abandoned", "expired")),
                    Plan.updated_at < plans_cutoff,
                    batch_size=batch_size,
                ),
                batch_size,
            )

        await session.commit()

    if any(deleted.values()):
        logger.info("cognition_retention_done", **deleted)
    return deleted


async def run_messages_retention_cycle(session_factory: Any | None = None) -> int:
    """单次消息表清理（可测试入口）；返回删除行数

    messages_retention_days=0 表示永久保留，直接跳过。
    删除经 _pk_batched_delete 分批（R5-L4）：WHERE 条件与
    MessageRepository.delete_older_than 保持一致，后者保留给其他调用方，
    但本周期不再走它的单条全量 DELETE。
    """
    from src.config import settings as _settings

    retention_days = _settings.messages_retention_days
    if retention_days <= 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    batch_size = _settings.retention_delete_batch_size
    factory = session_factory or db.session
    async with factory() as session:
        deleted = await _delete_in_batches(
            session,
            lambda: _pk_batched_delete(Message, Message.created_at < cutoff, batch_size=batch_size),
            batch_size,
        )

    if deleted:
        logger.info("messages_retention_done", deleted=deleted, retention_days=retention_days)
    return deleted


async def run_world_retention_cycle(session_factory: Any | None = None) -> tuple[int, int]:
    """单次世界历史清理：超期 world_events + 过多 world_snapshots（可测试入口）

    world_events 按创建时间删除超过 WORLD_EVENTS_RETENTION_DAYS 的行；
    world_snapshots 是冷启动恢复真相源，仅保留最近 WORLD_SNAPSHOTS_KEEP_LATEST 份。

    两类删除均分批执行（R5-L4）。快照阈值子查询在批循环外构建一次，
    与旧单语句语义严格一致——被删行都严格低于阈值，删后重算阈值只会得到同值。

    Returns:
        (deleted_events, deleted_snapshots)
    """
    from src.config import settings as _settings

    factory = session_factory or db.session
    cutoff = datetime.now(UTC) - timedelta(days=_settings.world_events_retention_days)
    keep = _settings.world_snapshots_keep_latest
    batch_size = _settings.retention_delete_batch_size

    async with factory() as session:
        deleted_events = await _delete_in_batches(
            session,
            lambda: _pk_batched_delete(WorldEvent, WorldEvent.created_at < cutoff, batch_size=batch_size),
            batch_size,
        )

        deleted_snapshots = 0
        if keep > 0:
            threshold = (
                select(WorldSnapshot.tick_id)
                .order_by(WorldSnapshot.tick_id.desc())
                .offset(keep - 1)
                .limit(1)
                .scalar_subquery()
            )
            snap_cond = WorldSnapshot.tick_id < threshold
            deleted_snapshots = await _delete_in_batches(
                session,
                lambda: _pk_batched_delete(WorldSnapshot, snap_cond, batch_size=batch_size),
                batch_size,
            )

    if deleted_events or deleted_snapshots:
        logger.info(
            "world_retention_done",
            deleted_events=deleted_events,
            deleted_snapshots=deleted_snapshots,
        )
    return (deleted_events, deleted_snapshots)


async def run_memory_retention_cycle(
    session_factory: Any | None = None,
) -> tuple[int, int]:
    """单次保留周期：压缩归档 + 分级删除（可测试入口）

    Args:
        session_factory: 会话上下文工厂；缺省用全局 db.session（测试可注入共享会话）

    Returns:
        (archived_groups, deleted_rows)
    """
    from src.config import settings as _settings

    if not _settings.memory_retention_enabled:
        return (0, 0)

    factory = session_factory or db.session
    now = datetime.now(UTC)
    low_cutoff = now - timedelta(days=_settings.memory_retention_low_importance_days)
    mid_cutoff = now - timedelta(days=_settings.memory_retention_mid_importance_days)

    archived_groups = 0
    deleted_rows = 0
    async with factory() as session:
        repo = MemoryRepository(session)

        # 阶段一：压缩归档（LLM 失败的组原样保留，下周期重试）
        compression_active = False
        deletable_small_ids: list[UUID] = []
        if _settings.memory_compression_enabled:
            llm = runtime.get_llm()
            if llm is not None:
                compression_active = True
                candidates = await repo.fetch_retention_candidates(
                    low_cutoff,
                    mid_cutoff,
                    limit=_settings.memory_compression_batch_limit,
                )
                # 归档 prompt 需要真实角色名而非 UUID（round-3 review M24），单次批量取全
                name_by_id: dict[UUID, str] = {
                    row[0]: row[1]
                    for row in (
                        await session.execute(
                            select(Character.id, Character.name).where(
                                Character.id.in_({e.character_id for e in candidates})
                            )
                        )
                    ).all()
                }
                archived_groups, deletable_small_ids = await _compress_candidates(
                    session, repo, llm, candidates, name_by_id
                )

        # 阶段二：分级删除（归档行豁免）
        # 压缩激活时只删「低于最小批的小组」——大组必须先压缩成功才允许删除；
        # 压缩关闭/无 LLM 时保持旧直删行为
        scope: Any = (
            (MemoryEpisode.id.in_(deletable_small_ids) if deletable_small_ids else false())
            if compression_active
            else true()
        )
        low_stmt = (
            delete(MemoryEpisode)
            .where(
                scope,
                MemoryEpisode.importance <= 3,
                MemoryEpisode.timestamp < low_cutoff,
                MemoryEpisode.source_type != "archive",
            )
            .returning(MemoryEpisode.id)
        )
        low_deleted = list((await session.execute(low_stmt)).scalars())

        mid_stmt = (
            delete(MemoryEpisode)
            .where(
                scope,
                MemoryEpisode.importance >= 4,
                MemoryEpisode.importance <= 6,
                MemoryEpisode.timestamp < mid_cutoff,
                MemoryEpisode.source_type != "archive",
            )
            .returning(MemoryEpisode.id)
        )
        mid_deleted = list((await session.execute(mid_stmt)).scalars())
        deleted_rows = len(low_deleted) + len(mid_deleted)

    logger.info(
        "memory_retention_completed",
        archived_groups=archived_groups,
        deleted_low=len(low_deleted),
        deleted_mid=len(mid_deleted),
    )
    return (archived_groups, deleted_rows)


async def _compress_candidates(
    session: Any,
    repo: MemoryRepository,
    llm: Any,
    candidates: list[MemoryEpisode],
    character_names: dict[UUID, str],
) -> tuple[int, list[UUID]]:
    """把到期候选按角色×月份分组压缩为归档行

    Returns:
        (成功压缩的组数, 低于最小批可直接删除的小组成员 ID)

    不变量：LLM 摘要失败（或解析失败）时整组跳过、原始行保留——绝不未压缩先删除。
    """
    from src.config import settings as _settings

    groups: dict[tuple[UUID, str], list[MemoryEpisode]] = {}
    for episode in candidates:
        key = (episode.character_id, episode.timestamp.strftime("%Y-%m"))
        groups.setdefault(key, []).append(episode)

    prompts = runtime.get_prompts()
    archived = 0
    small_ids: list[UUID] = []
    for (character_id, month), episodes in groups.items():
        if len(episodes) < _settings.memory_compression_min_batch:
            # 小组无需摘要（收益低于成本），标记为可直删
            small_ids.extend(e.id for e in episodes)
            continue
        digest = await _summarize_group(prompts, llm, character_id, month, episodes, character_names[character_id])
        if not digest:
            continue
        archive = MemoryEpisode(
            character_id=character_id,
            content=f"[归档] {month}：{digest}",
            importance=3,
            # timestamp 继承原事件仅用于展示/排序；保留期按 created_at 计龄（round-5 M2）
            timestamp=episodes[-1].timestamp,
            source_type="archive",
            materialized=False,
        )
        await repo.add(archive)
        await repo.delete_by_ids([e.id for e in episodes])
        archived += 1
    return (archived, small_ids)


async def _summarize_group(
    prompts: Any,
    llm: Any,
    character_id: UUID,
    month: str,
    episodes: list[MemoryEpisode],
    character_name: str,
) -> str | None:
    """LLM 生成单组月度摘要；任何失败返回 None（调用方跳过该组）"""
    if prompts is None:
        logger.warning("memory_compression_prompts_unavailable")
        return None
    memories_text = "\n".join(f"- [{e.timestamp:%d %H:%M}] {e.content}" for e in episodes)
    prompt = prompts.render(
        "memory_compress",
        character_name=character_name,  # 真实角色名而非 UUID（round-3 review M24）
        month=month,
        memories_text=memories_text,
    )
    try:
        response = await llm.chat(prompt)
        text = response.strip()
        if text.startswith("```"):
            text = "\n".join(ln for ln in text.split("\n") if not ln.startswith("```")).strip()
        start, end = text.find("{"), text.rfind("}") + 1
        parsed = json.loads(text[start:end])
        digest = parsed.get("digest")
        return digest.strip() if isinstance(digest, str) and digest.strip() else None
    except Exception as e:
        logger.warning("memory_compression_llm_failed", error=str(e))
        return None
