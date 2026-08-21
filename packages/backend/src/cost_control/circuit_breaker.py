"""LLM 调用熔断器 - 连续失败熔断

状态机：
- CLOSED:    正常放行，累计连续失败数；成功则重置计数
- OPEN:      熔断中，拒绝调用；经过 ``recovery_timeout`` 秒后自动转 HALF_OPEN
- HALF_OPEN: 放行一次试探调用；成功 → CLOSED，失败 → OPEN

Redis Key: ``llm:circuit_breaker`` (Hash)
- state:              CLOSED / OPEN / HALF_OPEN
- failure_count:      连续失败次数
- last_failure_time:  最近一次失败的时间戳（unix 秒）

多实例共享：所有实例读写同一 Redis key，状态全局一致。
"""

from __future__ import annotations

import time
from enum import StrEnum

from redis.asyncio import Redis
from structlog import get_logger

logger = get_logger(__name__)

_CB_KEY = "llm:circuit_breaker"


class CircuitState(StrEnum):
    """熔断器状态"""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpen(Exception):
    """熔断器处于 OPEN 状态时抛出，表示当前拒绝调用

    Attributes:
        state: 当前状态字符串
        failure_count: 连续失败次数
        last_failure_time: 最近一次失败时间戳（unix 秒）
    """

    def __init__(
        self,
        state: str = "OPEN",
        failure_count: int = 0,
        last_failure_time: float = 0.0,
    ) -> None:
        self.state = state
        self.failure_count = failure_count
        self.last_failure_time = last_failure_time
        super().__init__(f"Circuit breaker OPEN: failures={failure_count} last_failure_ts={last_failure_time}")


class CircuitBreaker:
    """LLM 调用熔断器

    基于连续失败次数触发熔断，熔断超时后进入半开状态放行试探调用。

    Args:
        redis: Redis 异步客户端（支持多实例共享状态）
        failure_threshold: 连续失败阈值，达到后进入 OPEN
        recovery_timeout: OPEN 状态恢复时长（秒），过后进入 HALF_OPEN

    典型用法：
        cb = CircuitBreaker(redis, failure_threshold=5, recovery_timeout=60)
        if await cb.can_execute():
            try:
                result = await call_llm(...)
                await cb.record_success()
            except Exception:
                await cb.record_failure()
                raise
    """

    def __init__(
        self,
        redis: Redis,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ) -> None:
        self.redis = redis
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

    async def _read_state(self) -> tuple[CircuitState, int, float]:
        """从 Redis 读取当前状态

        Returns:
            (state, failure_count, last_failure_time)
            key 不存在时返回 (CLOSED, 0, 0.0)
        """
        raw = await self.redis.hgetall(_CB_KEY)
        if not raw:
            return CircuitState.CLOSED, 0, 0.0
        try:
            state = CircuitState(str(raw.get("state", "CLOSED")))
        except ValueError:
            state = CircuitState.CLOSED
        failure_count = int(raw.get("failure_count", 0))
        last_failure_time = float(raw.get("last_failure_time", 0.0))
        return state, failure_count, last_failure_time

    async def _write_state(
        self,
        state: CircuitState,
        failure_count: int,
        last_failure_time: float,
    ) -> None:
        await self.redis.hset(
            _CB_KEY,
            mapping={
                "state": state.value,
                "failure_count": str(failure_count),
                "last_failure_time": str(last_failure_time),
            },
        )

    async def snapshot(self) -> tuple[CircuitState, int, float]:
        """读取当前熔断器状态快照（只读）

        Returns:
            (state, failure_count, last_failure_time)
        """
        return await self._read_state()

    async def can_execute(self) -> bool:
        """检查是否允许调用

        - CLOSED → True
        - OPEN 且已过 ``recovery_timeout`` → 转 HALF_OPEN，返回 True
        - OPEN 且未超时 → False
        - HALF_OPEN → True（放行一次试探）

        Returns:
            是否允许执行
        """
        state, failure_count, last_failure_time = await self._read_state()
        now = time.time()

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            if now - last_failure_time >= self.recovery_timeout:
                # 进入 HALF_OPEN，放行试探调用
                await self._write_state(CircuitState.HALF_OPEN, failure_count, last_failure_time)
                logger.info(
                    "circuit_breaker_half_open",
                    failure_count=failure_count,
                    recovery_timeout=self.recovery_timeout,
                )
                return True
            return False

        # HALF_OPEN：放行试探
        return True

    async def record_success(self) -> None:
        """记录一次成功调用

        - HALF_OPEN → CLOSED（恢复，重置失败计数）
        - CLOSED → 重置失败计数
        - OPEN → 不应发生（can_execute 会拦截），忽略
        """
        state, failure_count, last_failure_time = await self._read_state()

        if state == CircuitState.HALF_OPEN:
            await self._write_state(CircuitState.CLOSED, 0, last_failure_time)
            logger.info("circuit_breaker_recovered", state="CLOSED")
            return

        if state == CircuitState.CLOSED and failure_count != 0:
            await self._write_state(CircuitState.CLOSED, 0, last_failure_time)
            return

        # OPEN 状态收到 success（异常时序），不处理
        return

    async def record_failure(self) -> None:
        """记录一次失败调用，达阈值后进入 OPEN

        - HALF_OPEN → OPEN（再次熔断）
        - CLOSED → failure_count+1，达 ``failure_threshold`` 则 OPEN
        - OPEN → 刷新 last_failure_time（保持熔断）
        """
        state, failure_count, last_failure_time = await self._read_state()
        now = time.time()
        new_count = failure_count + 1

        if state == CircuitState.HALF_OPEN:
            await self._write_state(CircuitState.OPEN, new_count, now)
            logger.warning("circuit_breaker_reopened", failure_count=new_count)
            return

        if state == CircuitState.OPEN:
            # 保持 OPEN，刷新最近失败时间
            await self._write_state(CircuitState.OPEN, new_count, now)
            return

        # CLOSED
        if new_count >= self.failure_threshold:
            await self._write_state(CircuitState.OPEN, new_count, now)
            logger.warning(
                "circuit_breaker_opened",
                failure_count=new_count,
                threshold=self.failure_threshold,
            )
        else:
            await self._write_state(CircuitState.CLOSED, new_count, now)
            logger.info(
                "circuit_breaker_failure_recorded",
                failure_count=new_count,
                threshold=self.failure_threshold,
            )


# === 单例管理 ===

_breaker: CircuitBreaker | None = None


def set_circuit_breaker(redis: Redis, failure_threshold: int = 5, recovery_timeout: int = 60) -> None:
    """初始化全局熔断器单例（在 lifespan 中调用）"""
    global _breaker
    _breaker = CircuitBreaker(redis, failure_threshold, recovery_timeout)


def get_circuit_breaker() -> CircuitBreaker | None:
    """获取全局熔断器单例（未初始化返回 None）"""
    return _breaker
