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

from sqlalchemy import func, select, update
from structlog import get_logger

from src import runtime
from src.config import settings
from src.db.models import PersonMemory
from src.db.repositories import CharacterRepository
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

            async with db.session() as session:
                cutoff = datetime.now(UTC) - timedelta(days=14)
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
                    continue
                stmt = (
                    update(PersonMemory)
                    .where(
                        PersonMemory.heat > 0,
                        PersonMemory.last_interaction_at < cutoff,
                    )
                    .values(heat=func.floor(PersonMemory.heat / 2))
                )
                await session.execute(stmt)
                await session.commit()
                logger.info("person_memory_heat_decayed", rows=stale_count)

        except asyncio.CancelledError:
            logger.info("person_memory_heat_decay_loop_cancelled")
            raise
        except Exception as e:
            logger.error("person_memory_heat_decay_loop_error", error=str(e), exc_info=True)
