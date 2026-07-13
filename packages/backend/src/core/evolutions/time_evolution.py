"""时间演化器 - 推进虚拟世界时钟

每 Tick 推进 `world_tick_minutes` 分钟，维护时段（day_phase）与季节（season）。
状态存储于 Redis Hash: `world:state:time`，字段：world_time / tick_id / day_phase / season。

初始虚拟时间可通过环境变量 `WORLD_INITIAL_TIME` 配置（ISO 格式），
未配置时默认使用当前现实日期的 08:00，让虚拟时间与现实时间保持同步。
"""
from datetime import datetime, timedelta

from redis.asyncio import Redis
from structlog import get_logger

from src.config import settings
from src.core.evolutions.base import WorldEvolution

logger = get_logger(__name__)


def _get_initial_world_time() -> datetime:
    """获取初始虚拟时间

    优先使用环境变量 `WORLD_INITIAL_TIME` 配置的 ISO 格式时间；
    未配置时默认使用当前现实日期的 08:00（本地时区），让虚拟时间与现实时间保持同步。
    """
    configured = settings.world_initial_time.strip()
    if configured:
        try:
            return datetime.fromisoformat(configured)
        except (ValueError, TypeError):
            logger.warning("world_initial_time_parse_failed", value=configured)
    # 默认：当前现实日期 08:00
    return datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)


# 时间状态在 Redis 中的 Key
TIME_KEY = "world:state:time"


def compute_day_phase(hour: int) -> str:
    """根据小时计算一天中的阶段

    morning(6-12) / afternoon(12-18) / evening(18-22) / night(22-6)
    """
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def compute_season(month: int) -> str:
    """根据月份计算季节（北半球）"""
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


class TimeEvolution(WorldEvolution):
    """时间演化器

    每个 Tick 将虚拟时间推进 `settings.world_tick_minutes` 分钟，
    并同步更新 day_phase 与 season，供其他演化器（天气/场景/事件）联动使用。
    """

    name = "time"

    async def setup(self, redis: Redis) -> None:
        """首次运行时初始化时间状态

        初始时间通过 `WORLD_INITIAL_TIME` 环境变量配置（ISO 格式），
        未配置时默认使用当前现实日期的 08:00。
        """
        existing = await redis.hgetall(TIME_KEY)
        if not existing:
            initial = _get_initial_world_time()
            await self.hset_json(redis, TIME_KEY, {
                "world_time": initial.isoformat(),
                "tick_id": 0,
                "day_phase": compute_day_phase(initial.hour),
                "season": compute_season(initial.month),
            })
            logger.info("time_evolution_initialized", world_time=initial.isoformat())

    async def evolve(self, redis: Redis, tick_id: int, world_state: dict) -> dict:
        """推进虚拟时钟一个 Tick"""
        state = await self.hgetall_json(redis, TIME_KEY)
        if state and "world_time" in state:
            current = datetime.fromisoformat(state["world_time"])
        else:
            # 未初始化时回退到初始时间（可配置）
            current = _get_initial_world_time()

        # 推进 N 分钟（从配置读取，默认 10）
        step_minutes = settings.world_tick_minutes
        new_time = current + timedelta(minutes=step_minutes)
        day_phase = compute_day_phase(new_time.hour)
        season = compute_season(new_time.month)

        new_state = {
            "world_time": new_time.isoformat(),
            "tick_id": tick_id,
            "day_phase": day_phase,
            "season": season,
        }
        await self.hset_json(redis, TIME_KEY, new_state)

        logger.info(
            "time_advanced",
            tick_id=tick_id,
            world_time=new_time.isoformat(),
            day_phase=day_phase,
            season=season,
            step_minutes=step_minutes,
        )

        # 返回需要合并到 world:state 主哈希的摘要字段
        return {
            "current_time": new_time.isoformat(),
            "world_time": new_time.isoformat(),
            "day_phase": day_phase,
            "season": season,
            "tick_id": tick_id,
        }
