"""基于 Redis 的速率限制器（防刷）

使用 Redis INCR + EXPIRE 实现固定窗口计数限流：
- 每次请求对计数器 INCR
- 第一次请求时设置 EXPIRE（窗口过期自动清零）
- 计数超过阈值则拒绝

适用于：
- API 接口限流（按用户/IP 限流）
- 消息发送频率控制（防刷屏）
- LLM 调用频率控制（成本保护）
"""

from __future__ import annotations

from redis.asyncio import Redis
from structlog import get_logger

logger = get_logger(__name__)


# 固定窗口限流原子脚本：INCR 后仅在计数为 1（窗口首个请求）时设置 EXPIRE
_RATE_LIMIT_LUA = """
local count = redis.call('incr', KEYS[1])
if count == 1 then
  redis.call('expire', KEYS[1], ARGV[1])
end
return count
"""


class RateLimiter:
    """基于 Redis 的速率限制器

    使用方式：
        redis_client = redis.asyncio.from_url("redis://localhost:6379")
        limiter = RateLimiter(redis_client, key_prefix="rate")
        if await limiter.check("user:123", max_requests=60, window_seconds=60):
            # 允许请求
            ...
        else:
            # 超出限制
            ...
    """

    def __init__(self, redis: Redis, key_prefix: str = "rate") -> None:
        """初始化速率限制器

        Args:
            redis: redis.asyncio.Redis 客户端实例
            key_prefix: Redis key 前缀，默认 "rate"
        """
        self.redis = redis
        self.key_prefix = key_prefix

    def _build_key(self, key: str) -> str:
        """构建完整的 Redis key

        Args:
            key: 业务标识（如用户 ID、IP 地址）

        Returns:
            完整的 Redis key（{prefix}:{key}）
        """
        return f"{self.key_prefix}:{key}"

    async def check(
        self,
        key: str,
        max_requests: int = 60,
        window_seconds: int = 60,
    ) -> bool:
        """检查是否允许请求

        使用 Redis INCR + EXPIRE 实现固定窗口计数：
        - 第一次请求（计数为 1）时设置 EXPIRE
        - 计数 <= max_requests 允许，否则拒绝

        Args:
            key: 业务标识（如用户 ID、IP 地址）
            max_requests: 窗口内允许的最大请求数，默认 60
            window_seconds: 窗口时长（秒），默认 60

        Returns:
            True 表示允许请求，False 表示已被限流
        """
        full_key = self._build_key(key)

        # S-4：INCR 与首次 EXPIRE 必须原子完成——两步实现若在中间崩溃，
        # key 永不过期，用户被永久限流
        count = int(
            await self.redis.eval(
                _RATE_LIMIT_LUA,
                1,
                full_key,
                window_seconds,
            )
        )
        if count == 1:
            logger.debug(
                "rate_limit_window_started",
                key=key,
                window_seconds=window_seconds,
                max_requests=max_requests,
            )

        allowed = count <= max_requests
        if not allowed:
            logger.info(
                "rate_limit_exceeded",
                key=key,
                count=count,
                max_requests=max_requests,
                window_seconds=window_seconds,
            )

        return allowed

    async def get_remaining(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> int:
        """返回剩余配额

        注意：本方法仅查询当前计数，不递增。
        若窗口已过期（key 不存在），返回 max_requests（即满配额）。

        Args:
            key: 业务标识
            max_requests: 窗口内允许的最大请求数
            window_seconds: 窗口时长（秒，保留用于未来扩展，当前未使用）

        Returns:
            剩余可用请求数（最小为 0）
        """
        full_key = self._build_key(key)
        raw = await self.redis.get(full_key)
        count = int(raw) if raw is not None else 0
        remaining = max_requests - count
        return max(remaining, 0)

    async def reset(self, key: str) -> None:
        """重置计数器（清空当前窗口的请求计数）

        适用于管理员手动重置、测试场景等。

        Args:
            key: 业务标识
        """
        full_key = self._build_key(key)
        await self.redis.delete(full_key)
        logger.debug("rate_limit_reset", key=key)
