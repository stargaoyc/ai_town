"""事件演化器 - 节日触发与活跃事件维护

根据虚拟日期触发节日事件，事件持续 N 天后自动结束。
状态存储于 Redis Hash: `world:state:events`（field: event_id → JSON{id, name, description, start_date, end_date}）。
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.core.world.evolutions.base import WorldEvolution
from src.core.world.evolutions.time_evolution import TIME_KEY
from src.paths import find_project_root

logger = get_logger(__name__)

# 事件状态在 Redis 中的 Key
EVENTS_KEY = "world:state:events"


# 节日日历：(month, day) → 事件定义（name / 持续天数 / 描述）
def _load_festival_calendar(path: Path | None = None) -> dict[tuple[int, int], dict[str, Any]]:
    """从 configs/events.yaml 加载节日日历（C-2：唯一真相源 + 启动校验）

    Args:
        path: 显式日历路径；缺省经 find_project_root 定位（测试注入用）

    校验项：
    - date 必须是 MM-DD 格式
    - duration_days 为正整数
    - main_scenes / activities 与 scenes.yaml 词表交叉校验在 town loader 已覆盖场景存在性；
      此处校验结构完整性（id/name/date 必填）

    Returns:
        {(month, day): {name, duration_days, description}} 日历

    Raises:
        FileNotFoundError: configs/events.yaml 不存在（配置挂载缺失）
        ValueError: 节目条目结构非法
    """
    import yaml as _yaml

    calendar_path = path if path is not None else find_project_root() / "configs" / "events.yaml"
    if not calendar_path.is_file():
        raise FileNotFoundError(
            f"configs/events.yaml not found at {calendar_path}; 容器部署需挂载 ./configs:/app/configs"
        )
    with open(calendar_path, encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}

    calendar: dict[tuple[int, int], dict[str, Any]] = {}
    for entry in data.get("events", []):
        event_id = entry.get("id")
        name = entry.get("name")
        raw_date = str(entry.get("date", ""))
        try:
            month, day = (int(x) for x in raw_date.split("-"))
            assert 1 <= month <= 12 and 1 <= day <= 31
        except (ValueError, AssertionError) as e:
            raise ValueError(f"events.yaml 节日 {event_id!r} 的 date 非法: {raw_date!r}") from e
        duration = int(entry.get("duration_days", 1))
        if duration < 1:
            raise ValueError(f"events.yaml 节日 {event_id!r} 的 duration_days 必须为正整数: {duration}")

        calendar[(month, day)] = {
            "name": name or event_id,
            "duration_days": duration,
            "description": entry.get("description", ""),
        }

    logger.info("festival_calendar_loaded", source=str(calendar_path), events=len(calendar))
    return calendar


class EventEvolution(WorldEvolution):
    """事件演化器

    每 Tick：
    1. 根据当前虚拟日期匹配节日日历，触发尚未激活的事件；
    2. 清理 end_date 已过的结束事件。
    """

    name = "event"

    def __init__(self, calendar_path: Path | None = None) -> None:
        # 日历在构造时而非模块导入时加载（round-6 L17a）：import 副作用会让任何
        # 触碰本模块的单测在缺 configs/ 的环境下炸 FileNotFoundError。
        # 引擎在 app lifespan 构造演化器，配置损坏仍在启动期 fail-fast。
        self.festival_calendar = _load_festival_calendar(calendar_path)

    async def setup(self, redis: Redis) -> None:
        """首次运行时确保事件哈希为空"""
        existing = await redis.hgetall(EVENTS_KEY)
        if not existing:
            await redis.delete(EVENTS_KEY)
            logger.info("event_evolution_initialized")

    async def evolve(self, redis: Redis, tick_id: int, world_state: dict[str, Any]) -> dict[str, Any]:
        """触发节日并清理已结束事件"""
        time_state = await self.hgetall_json(redis, TIME_KEY)
        if not time_state or "world_time" not in time_state:
            logger.warning("event_evolution_no_time", tick_id=tick_id)
            return {"active_events": []}

        now = datetime.fromisoformat(time_state["world_time"])
        today = now.date()

        current = await self.hgetall_json(redis, EVENTS_KEY)

        # 1. 触发今日节日
        key = (today.month, today.day)
        festival = self.festival_calendar.get(key)
        if festival:
            event_id = f"{today.year:04d}-{key[0]:02d}-{key[1]:02d}"
            if event_id not in current:
                current[event_id] = {
                    "id": event_id,
                    "name": festival["name"],
                    "description": festival["description"],
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=festival["duration_days"] - 1)).isoformat(),
                }
                logger.info(
                    "event_triggered",
                    event_id=event_id,
                    name=festival["name"],
                    tick_id=tick_id,
                )

        # 2. 清理已结束事件（end_date 早于今天）
        ended = [
            eid
            for eid, ev in current.items()
            if "end_date" in ev and datetime.fromisoformat(ev["end_date"]).date() < today
        ]
        for eid in ended:
            logger.info("event_ended", event_id=eid, tick_id=tick_id)
            current.pop(eid, None)

        # 3. 写回 Redis（无活跃事件时清空 Key）
        if current:
            await self.hset_json(redis, EVENTS_KEY, current)
        else:
            await redis.delete(EVENTS_KEY)

        active_events = list(current.values())
        logger.info("events_updated", tick_id=tick_id, active_count=len(active_events))
        return {"active_events": active_events}
