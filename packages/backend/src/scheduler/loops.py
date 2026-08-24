"""后台业务循环 - 从 main.py 下沉的三个长驻任务

职责边界：
- character_tick_loop: 定期对所有活跃角色执行 Tick（含 429 限流退避）
- diary_scheduler_loop: 按世界时间触发日/周/月/年日记生成
- reconciliation_loop: Redis vs PG 状态对账与自动修复

装配方式：main.py lifespan 以 asyncio.create_task 启动，shutdown 时统一 cancel。
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, false, func, select, true, update
from sqlalchemy.engine import CursorResult
from structlog import get_logger

from src import runtime
from src.config import settings
from src.db.models import MemoryEpisode, PersonMemory, PersonMemoryEntry, Plan
from src.db.repositories import CharacterRepository, MemoryRepository
from src.db.session import db
from src.memory.diary_service import DiaryService

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

    生成是幂等的：DiaryService 会跳过当前周期已存在日记的角色。
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
                world_time = datetime.fromisoformat(world_time_raw)
            except ValueError:
                logger.warning("diary_scheduler_invalid_world_time", raw=world_time_raw)
                continue

            hour = world_time.hour
            day_of_year = world_time.timetuple().tm_yday

            # 当日计划滚动过期（随世界时间检查，30 分钟粒度足够日级语义）
            try:
                await expire_daily_plans()
            except Exception as e:
                logger.warning("daily_plan_expire_failed", error=str(e))

            # 根据世界时间确定需要生成的周期
            periods_to_generate: list[str] = []
            if hour >= 22 or hour < 6:
                periods_to_generate.append("day")
            if day_of_year % 7 == 0:
                periods_to_generate.append("week")
            if day_of_year % 30 == 0:
                periods_to_generate.append("month")
            if day_of_year % 365 == 0:
                periods_to_generate.append("year")

            if not periods_to_generate:
                continue

            logger.info(
                "diary_scheduler_trigger",
                periods=periods_to_generate,
                world_hour=hour,
                world_day_of_year=day_of_year,
            )

            service = DiaryService(session_factory=db.session)
            for period in periods_to_generate:
                try:
                    summary = await service.generate_diaries_for_all_characters(period)
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
        response = await llm.chat(prompt, model="chat")
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

    每 24 小时执行一次，两阶段：
    1. 压缩归档：到期低价值记忆按角色×月份 LLM 压缩成归档行（source_type='archive'）
    2. 分级删除：importance<=3 超 low_importance_days、4-6 超 mid_importance_days；
       importance>=7 永久保留；归档行豁免删除

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
        except asyncio.CancelledError:
            logger.info("memory_retention_loop_cancelled")
            raise
        except Exception as e:
            logger.error("memory_retention_loop_error", error=str(e), exc_info=True)


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
                archived_groups, deletable_small_ids = await _compress_candidates(session, repo, llm, candidates)

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
        digest = await _summarize_group(prompts, llm, character_id, month, episodes)
        if not digest:
            continue
        archive = MemoryEpisode(
            character_id=character_id,
            content=f"[归档] {month}：{digest}",
            importance=3,
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
) -> str | None:
    """LLM 生成单组月度摘要；任何失败返回 None（调用方跳过该组）"""
    if prompts is None:
        logger.warning("memory_compression_prompts_unavailable")
        return None
    memories_text = "\n".join(f"- [{e.timestamp:%d %H:%M}] {e.content}" for e in episodes)
    prompt = prompts.render(
        "memory_compress",
        character_name=str(character_id),
        month=month,
        memories_text=memories_text,
    )
    try:
        response = await llm.chat(prompt, model="chat")
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
