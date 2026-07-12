"""World Engine - 世界状态推进引擎

World Tick 主循环流程：
1. Redis 分布式锁选主（Leader Election）
2. 每 N 秒执行一次 Tick：
   - 调用所有 Evolution.evolve()
   - 合并世界状态变更
   - 持久化到 Redis world:state
   - 每 M Tick 持久化差分事件到 world_events（仅状态变化时写入）
   - 每 1000 Tick 持久化完整快照到 world_snapshots（冷启动恢复）
3. 锁续租 & 监控

设计要点：
- 单实例运行：通过 Redis 分布式锁确保只有一个实例在推进世界
- 容错性：锁 TTL 自动过期，避免死锁
- 可观测性：每次 Tick 记录日志，便于监控和调试
- 事件去重：仅在状态变化时写入 world_events，避免事件风暴
- 快照闭环：定期快照 + 增量事件，冷启动恢复时间恒定
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.config import settings
from src.core.evolutions import default_evolutions
from src.db.models import WorldEvent, WorldSnapshot
from src.db.repositories import WorldEventRepository, WorldSnapshotRepository
from src.db.session import db
from src.observability.metrics import (
    ACTIVE_CHARACTERS,
    REDIS_CONNECTED,
    WORLD_TICK_DURATION,
    WORLD_TICK_ERRORS,
    WORLD_TICK_ID,
    WORLD_TICK_TOTAL,
)

logger = get_logger(__name__)


class WorldEngine:
    """世界引擎 - 负责 World Tick 主循环

    使用 Redis 分布式锁实现 Leader Election，确保同一时刻只有一个实例在推进世界状态。
    """

    LOCK_KEY = "world:tick:leader"
    LOCK_TTL = 30  # 锁 TTL（秒）
    LOCK_RENEW_INTERVAL = 10  # 续租间隔（秒）

    def __init__(self, redis: Redis):
        """初始化 World Engine

        Args:
            redis: Redis 客户端实例
        """
        self.redis = redis
        self.evolutions = default_evolutions()
        self.tick_id = 0
        self.is_leader = False
        self._stop_event = asyncio.Event()
        self._leader_task: asyncio.Task | None = None
        self._tick_task: asyncio.Task | None = None
        # 上一次持久化的世界状态（用于事件去重，仅在状态变化时写入 world_events）
        self._last_persisted_state: dict[str, Any] = {}

    async def start(self) -> None:
        """启动 World Engine（后台任务）

        创建两个并发任务：
        1. Leader Election 循环：竞争锁、续租
        2. World Tick 循环：推进世界状态

        启动时从 Redis 恢复上次的 tick_id，避免重启后归零。
        """
        # 从 Redis 恢复上次的 tick_id
        try:
            last_tick = await self.redis.hget("world:state", "tick_id")
            if last_tick:
                self.tick_id = int(last_tick)
                logger.info("world_engine_tick_id_restored", tick_id=self.tick_id)
            else:
                logger.info("world_engine_starting_no_previous_tick")
        except Exception as e:
            logger.warning("world_engine_tick_id_restore_failed", error=str(e))

        logger.info("world_engine_starting", tick_id=self.tick_id)
        self._leader_task = asyncio.create_task(self._leader_loop())
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        """停止 World Engine

        设置停止信号，取消所有后台任务，释放锁
        """
        logger.info("world_engine_stopping", tick_id=self.tick_id, is_leader=self.is_leader)
        self._stop_event.set()

        # 等待任务完成
        if self._leader_task:
            self._leader_task.cancel()
            try:
                await self._leader_task
            except asyncio.CancelledError:
                pass

        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass

        # 释放锁
        if self.is_leader:
            await self.redis.delete(self.LOCK_KEY)
            logger.info("world_lock_released")

    async def _leader_loop(self) -> None:
        """Leader Election 循环

        持续尝试获取锁，获得锁后定期续租，失去锁后等待重试
        """
        while not self._stop_event.is_set():
            try:
                # 尝试获取锁
                acquired = await self.redis.set(
                    self.LOCK_KEY,
                    f"leader:{datetime.now().isoformat()}",
                    ex=self.LOCK_TTL,
                    nx=True,  # 仅当不存在时设置
                )

                if acquired:
                    self.is_leader = True
                    logger.info("world_leader_acquired", tick_id=self.tick_id)

                    # 续租循环
                    while not self._stop_event.is_set() and self.is_leader:
                        await asyncio.sleep(self.LOCK_RENEW_INTERVAL)

                        # 续租
                        renewed = await self.redis.expire(self.LOCK_KEY, self.LOCK_TTL)
                        if not renewed:
                            logger.warning("world_leader_lost", tick_id=self.tick_id)
                            self.is_leader = False
                            break

                        logger.debug("world_lock_renewed", tick_id=self.tick_id)
                else:
                    self.is_leader = False
                    # 等待锁过期后重试（加缓冲时间）
                    await asyncio.sleep(self.LOCK_TTL + 5)

            except asyncio.CancelledError:
                logger.info("leader_loop_cancelled")
                raise
            except Exception as e:
                logger.error("leader_loop_error", error=str(e), exc_info=True)
                self.is_leader = False
                await asyncio.sleep(5)

    async def _tick_loop(self) -> None:
        """World Tick 主循环

        定期推进世界状态（仅当成为 Leader 时执行）
        """
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(settings.world_tick_seconds)

                if not self.is_leader:
                    continue

                self.tick_id += 1
                await self._execute_tick()

            except asyncio.CancelledError:
                logger.info("tick_loop_cancelled")
                raise
            except Exception as e:
                logger.error("tick_loop_error", error=str(e), exc_info=True)

    async def _execute_tick(self) -> None:
        """执行一次 World Tick

        流程：
        1. 读取当前世界状态
        2. 执行所有演化器（按依赖顺序）
        3. 持久化到 Redis
        4. 定期持久化快照到 PostgreSQL
        """
        start_time = datetime.now()
        start_perf = time.perf_counter()
        logger.info("world_tick_start", tick_id=self.tick_id)

        # 更新 Gauge 指标
        WORLD_TICK_ID.set(self.tick_id)
        REDIS_CONNECTED.set(1)

        try:
            # 1. 读取当前世界状态
            world_state = await self._load_world_state()

            # 2. 执行所有演化器（按依赖顺序：时间 → 天气 → 场景 → 资源 → 事件）
            updates: dict[str, dict[str, Any]] = {}
            for evolution in self.evolutions:
                try:
                    # 执行演化，获取状态更新
                    result = await evolution.evolve(self.redis, self.tick_id, world_state)
                    updates[evolution.name] = result

                    # 合并到 world_state（供后续 Evolution 使用）
                    world_state.update(result)

                    logger.debug(
                        "evolution_completed",
                        evolution=evolution.name,
                        tick_id=self.tick_id,
                    )
                except Exception as e:
                    logger.error(
                        "evolution_failed",
                        evolution=evolution.name,
                        tick_id=self.tick_id,
                        error=str(e),
                        exc_info=True,
                    )
                    # 继续执行其他演化器，不中断整个 Tick

            # 3. 持久化到 Redis world:state
            await self._save_world_state(world_state)

            # 4. 每 N Tick 持久化差分事件到 PG（仅状态变化时写入）
            if self.tick_id % settings.world_snapshot_interval == 0:
                await self._save_world_events(world_state)

            # 5. 每 1000 Tick 存一次完整快照（冷启动恢复用）
            if self.tick_id % settings.world_full_snapshot_interval == 0:
                await self._save_world_snapshot(world_state)

            duration = (datetime.now() - start_time).total_seconds()
            WORLD_TICK_DURATION.observe(time.perf_counter() - start_perf)
            WORLD_TICK_TOTAL.inc()
            logger.info(
                "world_tick_end",
                tick_id=self.tick_id,
                updates=list(updates.keys()),
                duration_seconds=duration,
            )

        except Exception as e:
            WORLD_TICK_ERRORS.inc()
            logger.error(
                "world_tick_error",
                tick_id=self.tick_id,
                error=str(e),
                exc_info=True,
            )
            raise

    async def _load_world_state(self) -> dict[str, Any]:
        """从 Redis 加载世界状态

        从多个 Redis Hash 中读取各演化器的状态，合并为完整的世界状态

        Returns:
            世界状态字典，包含：
            - tick_id: 当前 Tick ID
            - time: 时间演化器状态
            - weather: 天气演化器状态
            - scenes: 场景演化器状态
            - resources: 资源演化器状态
            - events: 事件演化器状态
        """
        # 读取各演化器的状态
        time_state = await self.redis.hgetall("world:state:time")
        weather_state = await self.redis.hgetall("world:state:weather")
        scenes_state = await self.redis.hgetall("world:state:scenes")
        resources_state = await self.redis.hgetall("world:state:resources")
        events_state = await self.redis.hgetall("world:state:events")

        # 合并为完整的世界状态
        world_state: dict[str, Any] = {
            "tick_id": self.tick_id,
            "time": dict(time_state) if time_state else {},
            "weather": dict(weather_state) if weather_state else {},
            "scenes": dict(scenes_state) if scenes_state else {},
            "resources": dict(resources_state) if resources_state else {},
            "events": dict(events_state) if events_state else {},
        }

        logger.debug("world_state_loaded", tick_id=self.tick_id)
        return world_state

    async def _save_world_state(self, state: dict[str, Any]) -> None:
        """保存世界状态到 Redis

        将世界状态摘要保存到主哈希（world:state），
        同时保留各演化器的详细状态到各自的哈希

        Args:
            state: 世界状态字典
        """
        # 主哈希存储摘要
        time_state = state.get("time", {})
        # weather 字段可能是 dict 或字符串
        weather_raw = state.get("weather", "sunny")
        if isinstance(weather_raw, dict):
            weather = str(weather_raw.get("weather", "sunny"))
            temperature = weather_raw.get("temperature")
        else:
            weather = str(weather_raw) if weather_raw else "sunny"
            temperature = None

        mapping: dict[str, str] = {
            "tick_id": str(self.tick_id),
            "world_time": str(time_state.get("world_time", "")),
            "weather": weather,
            "updated_at": datetime.now().isoformat(),
        }
        if temperature is not None:
            mapping["temperature"] = str(temperature)

        await self.redis.hset("world:state", mapping=mapping)  # type: ignore[arg-type]

        logger.debug("world_state_saved", tick_id=self.tick_id)

    async def _save_world_events(self, world_state: dict[str, Any]) -> None:
        """持久化差分事件到 PostgreSQL（仅状态变化时写入）

        事件去重：对比上一次持久化的状态，仅当维度状态发生变化时才写入事件。
        避免每个 Tick 都写入无变化的事件，防止事件风暴（50 角色 × 无变化 Tick
        也会产生大量无用事件）。

        Args:
            world_state: 世界状态字典
        """
        try:
            time_state = world_state.get("time", {})
            # weather 字段已为字符串（扁平结构）
            weather = world_state.get("weather", "")
            scenes_state = world_state.get("scenes", {})
            resources_state = world_state.get("resources", {})
            events_state = world_state.get("events", {})

            # 上一次持久化的状态（用于去重对比）
            last = self._last_persisted_state
            events: list[WorldEvent] = []

            # 时间事件（虚拟时间变化时写入）
            world_time_str = str(time_state.get("world_time", ""))
            if world_time_str and world_time_str != str(last.get("time", "")):
                events.append(WorldEvent(
                    tick_id=self.tick_id,
                    event_type="time",
                    event_key="default",
                    payload={"virtual_time": world_time_str, "tick_id": self.tick_id},
                ))

            # 天气事件（天气变化时写入）
            weather_str = str(weather)  # weather 已是字符串
            if weather_str and weather_str != str(last.get("weather", "")):
                events.append(WorldEvent(
                    tick_id=self.tick_id,
                    event_type="weather",
                    event_key="default",
                    payload={"weather": weather_str},
                ))

            # 场景事件（场景状态变化时写入）
            scenes_json = json.dumps(scenes_state, sort_keys=True, default=str)
            if scenes_state and scenes_json != last.get("_scenes_json"):
                events.append(WorldEvent(
                    tick_id=self.tick_id,
                    event_type="scene",
                    event_key="default",
                    payload=scenes_state,
                ))

            # 资源事件（资源状态变化时写入）
            resources_json = json.dumps(resources_state, sort_keys=True, default=str)
            if resources_state and resources_json != last.get("_resources_json"):
                events.append(WorldEvent(
                    tick_id=self.tick_id,
                    event_type="resource",
                    event_key="default",
                    payload=resources_state,
                ))

            # 特殊事件（有活跃事件时始终写入）
            if events_state:
                events.append(WorldEvent(
                    tick_id=self.tick_id,
                    event_type="event",
                    event_key="default",
                    payload=list(events_state.values()),
                ))

            if events:
                async with db.session() as session:
                    repo = WorldEventRepository(session)
                    await repo.add_batch(events)
                    # session 会自动 commit（session 上下文管理器）

            # 更新去重基线
            self._last_persisted_state = {
                "time": world_time_str,
                "weather": weather,
                "_scenes_json": scenes_json,
                "_resources_json": resources_json,
            }

            logger.info("world_events_saved", tick_id=self.tick_id, count=len(events))

        except Exception as e:
            logger.error(
                "world_events_save_failed",
                tick_id=self.tick_id,
                error=str(e),
                exc_info=True,
            )
            # 不抛出异常，避免影响主循环

    async def _save_world_snapshot(self, world_state: dict[str, Any]) -> None:
        """持久化完整世界快照到 PostgreSQL（冷启动恢复用）

        每 1000 Tick 存一次完整状态快照。冷启动时：
        1. 加载最新快照
        2. 回放该 Tick 之后的增量 world_events
        3. 恢复完整世界状态

        这保证冷启动时间恒定，不随运行时长线性增长。
        """
        try:
            time_state = world_state.get("time", {})
            # weather 字段已为字符串（扁平结构）
            weather = world_state.get("weather", "")
            scenes_state = world_state.get("scenes", {})
            resources_state = world_state.get("resources", {})
            events_state = world_state.get("events", {})

            # 解析虚拟时间
            world_time_str = str(time_state.get("world_time", ""))
            world_time = None
            if world_time_str:
                try:
                    world_time = datetime.fromisoformat(world_time_str)
                except (ValueError, TypeError):
                    pass

            snapshot = WorldSnapshot(
                tick_id=self.tick_id,
                world_time=world_time,
                weather=str(weather),  # 直接使用字符串
                locations=scenes_state if scenes_state else None,
                resources=resources_state if resources_state else None,
                active_events=events_state if events_state else None,
            )

            async with db.session() as session:
                repo = WorldSnapshotRepository(session)
                await repo.add(snapshot)
                # session 会自动 commit

            logger.info("world_snapshot_saved", tick_id=self.tick_id)

        except Exception as e:
            logger.error(
                "world_snapshot_save_failed",
                tick_id=self.tick_id,
                error=str(e),
                exc_info=True,
            )
            # 不抛出异常，避免影响主循环