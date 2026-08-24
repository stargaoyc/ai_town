"""World Tick fencing 校验单元测试（旧 leader 停顿苏醒防护）"""

from __future__ import annotations

from typing import Any

from src.core.world.engine import WorldEngine


class StubRedis:
    def __init__(self, lock_value: str | None) -> None:
        self._lock_value = lock_value

    async def get(self, key: str) -> str | None:
        return self._lock_value


def _engine(redis: Any) -> WorldEngine:
    # 引擎构造依赖完整注册表/LLM，fencing 校验仅用 redis 与两个属性，
    # 以 object.__new__ 绕过构造（测试规范 §5.2）
    engine = object.__new__(WorldEngine)
    engine.redis = redis
    engine.is_leader = True
    engine._leader_token = "token-a"  # noqa: SLF001 - 测试目标即私有状态
    return engine


class TestIsStillLeader:
    async def test_matching_token_is_leader(self) -> None:
        engine = _engine(StubRedis("token-a"))
        assert await engine._is_still_leader() is True

    async def test_lock_taken_by_other_loses_leadership(self) -> None:
        engine = _engine(StubRedis("token-b"))
        assert await engine._is_still_leader() is False

    async def test_lock_expired_loses_leadership(self) -> None:
        engine = _engine(StubRedis(None))
        assert await engine._is_still_leader() is False

    async def test_local_flag_off_short_circuits(self) -> None:
        engine = _engine(StubRedis("token-a"))
        engine.is_leader = False
        assert await engine._is_still_leader() is False

    async def test_redis_error_fails_closed(self) -> None:
        class ExplodingRedis:
            async def get(self, key: str) -> str | None:
                raise RuntimeError("redis down")

        engine = _engine(ExplodingRedis())
        # Redis 异常时按「失去领导权」处理——宁可跳过一个 Tick 也不双写
        assert await engine._is_still_leader() is False
