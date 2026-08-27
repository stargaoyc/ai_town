"""日预算管理器 - 基于 Redis 的 LLM 成本统计与预算控制

Redis Key 设计：
- ``llm:cost:{YYYY-MM-DD}`` (Hash)
  - tokens:  累计 token 数（int）
  - cost:    累计费用 USD（float）
  - count:   累计调用次数（int）
- TTL: 48 小时（自动清理过期数据，避免 key 堆积）

多实例共享：所有实例共用同一 Redis，状态全局一致。
日期按 UTC 滚动，UTC 00:00 自动切换到新 key。

典型用法：
    mgr = BudgetManager(redis, daily_budget_usd=10.0)
    # 已知 cost 时原子检查+记录
    await mgr.check_and_record(tokens=1500, cost=0.002)
    # 仅查询
    usage = await mgr.get_today_usage()
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

logger = get_logger(__name__)

# Redis key 模板与字段
_COST_KEY_TEMPLATE = "llm:cost:{date}"
_KEY_TTL_SECONDS = 48 * 3600  # 48 小时

# 原子「检查并记录」Lua 脚本
# 入参：KEYS[1]=cost key, ARGV=[tokens, cost, budget, ttl]
# 返回：{0, tokens_total, cost_total, count_total}  成功（已写入）
#       {1, tokens_total, cost_total, count_total}  超预算（未写入）
_LUA_CHECK_AND_RECORD = """
local key = KEYS[1]
local tokens = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local budget = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local cur_cost = tonumber(redis.call('HGET', key, 'cost') or '0')
if cur_cost + cost > budget then
  local cur_tokens = tonumber(redis.call('HGET', key, 'tokens') or '0')
  local cur_count = tonumber(redis.call('HGET', key, 'count') or '0')
  return {1, cur_tokens, cur_cost, cur_count}
end
local new_tokens = redis.call('HINCRBY', key, 'tokens', tokens)
local new_cost = redis.call('HINCRBYFLOAT', key, 'cost', cost)
local new_count = redis.call('HINCRBY', key, 'count', 1)
redis.call('EXPIRE', key, ttl)
return {0, new_tokens, new_cost, new_count}
"""


class BudgetExceeded(Exception):
    """预算超出异常

    当日 LLM 调用累计成本超过 ``daily_budget_usd`` 时抛出。

    Attributes:
        used: 当日已用费用 USD
        budget: 日预算上限 USD
        remaining: 剩余预算 USD（可能为负）
    """

    def __init__(self, used: float, budget: float, remaining: float) -> None:
        self.used = used
        self.budget = budget
        self.remaining = remaining
        super().__init__(f"Daily LLM budget exceeded: used=${used:.4f} budget=${budget:.4f} remaining=${remaining:.4f}")


class BudgetManager:
    """日预算管理器

    使用 Redis Hash 统计当日 LLM 调用的 token / cost / count，
    并在调用前检查是否超出日预算上限。

    Args:
        redis: Redis 异步客户端（建议 ``decode_responses=True``）
        daily_budget_usd: 日预算上限（USD）
        warning_threshold: 告警阈值比例（0-1），达到时 ``check_budget`` 返回 warning=True
    """

    def __init__(
        self,
        redis: Redis,
        daily_budget_usd: float = 10.0,
        warning_threshold: float = 0.8,
    ) -> None:
        self.redis = redis
        self.daily_budget_usd = daily_budget_usd
        self.warning_threshold = warning_threshold

    @staticmethod
    def _today_key() -> str:
        """返回当日（UTC）的 Redis key"""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return _COST_KEY_TEMPLATE.format(date=today)

    async def get_today_usage(self) -> dict[str, Any]:
        """获取当日累计使用量

        Returns:
            ``{"tokens": int, "cost": float, "count": int}``，
            key 不存在时各字段为 0。
        """
        key = self._today_key()
        raw = await self.redis.hgetall(key)
        if not raw:
            return {"tokens": 0, "cost": 0.0, "count": 0}
        return {
            "tokens": int(raw.get("tokens", 0)),
            "cost": float(raw.get("cost", 0.0)),
            "count": int(raw.get("count", 0)),
        }

    async def record_usage(self, tokens: int, cost: float) -> dict[str, Any]:
        """记录一次 LLM 调用的 usage

        使用 Redis HINCRBY（tokens/count）+ HINCRBYFLOAT（cost）累加，
        并刷新 TTL 为 48 小时。

        Args:
            tokens: 本次调用消耗的 token 数
            cost: 本次调用费用 USD

        Returns:
            更新后的当日 usage（``{"tokens", "cost", "count"}``）
        """
        key = self._today_key()
        pipe = self.redis.pipeline()
        pipe.hincrby(key, "tokens", int(tokens))
        pipe.hincrbyfloat(key, "cost", float(cost))
        pipe.hincrby(key, "count", 1)
        pipe.expire(key, _KEY_TTL_SECONDS)
        tokens_total, cost_total, count_total, _ = await pipe.execute()
        logger.info(
            "usage_recorded",
            key=key,
            tokens_delta=int(tokens),
            cost_delta=float(cost),
            tokens_total=int(tokens_total),
            cost_total=float(cost_total),
            count_total=int(count_total),
        )
        return {
            "tokens": int(tokens_total),
            "cost": float(cost_total),
            "count": int(count_total),
        }

    async def check_budget(self) -> dict[str, Any]:
        """检查预算状态（只读，不修改计数）

        Returns:
            ``{
                "remaining": float,   # 剩余预算
                "used": float,        # 已用费用
                "budget": float,      # 日预算上限
                "ratio": float,       # 已用比例 0-1
                "exceeded": bool,     # 是否超预算（used >= budget）
                "warning": bool,      # 是否达到告警阈值
                "tier": str,          # "ok" / "warning" / "exceeded"（分级降级，round-7 P0-2）
            }``
        """
        usage = await self.get_today_usage()
        used = usage["cost"]
        budget = self.daily_budget_usd
        remaining = budget - used
        ratio = used / budget if budget > 0 else 0.0
        exceeded = used >= budget
        warning = ratio >= self.warning_threshold
        tier = "exceeded" if exceeded else ("warning" if warning else "ok")
        return {
            "remaining": remaining,
            "used": used,
            "budget": budget,
            "ratio": ratio,
            "exceeded": exceeded,
            "warning": warning,
            "tier": tier,
        }

    async def check_and_record(self, tokens: int, cost: float) -> None:
        """原子检查预算并记录 usage

        通过 Lua 脚本保证「检查 + 记录」在 Redis 侧原子执行，
        支持多实例并发：若累计费用 + 本次费用超过日预算，则不写入并抛出
        ``BudgetExceeded``。

        适用于调用前已知 cost 的场景；对于 LLM 调用（cost 仅在调用后可知），
        应在装饰器中使用 ``check_budget``（调用前）+ ``record_usage``（调用后）。

        Args:
            tokens: 本次调用消耗的 token 数
            cost: 本次调用费用 USD

        Raises:
            BudgetExceeded: 累计费用超出日预算
        """
        key = self._today_key()
        result = await self.redis.eval(
            _LUA_CHECK_AND_RECORD,
            1,
            key,
            int(tokens),
            str(float(cost)),
            str(float(self.daily_budget_usd)),
            _KEY_TTL_SECONDS,
        )
        exceeded_flag = int(result[0])
        cost_total = float(result[2])

        if exceeded_flag == 1:
            tokens_total = int(result[1])
            count_total = int(result[3])
            logger.warning(
                "budget_exceeded",
                key=key,
                used=cost_total,
                projected=cost_total + float(cost),
                budget=self.daily_budget_usd,
                tokens_total=tokens_total,
                count=count_total,
            )
            raise BudgetExceeded(
                used=cost_total,
                budget=self.daily_budget_usd,
                remaining=self.daily_budget_usd - cost_total,
            )


# === 模块级单例 ===
_budget_manager: BudgetManager | None = None


def get_budget_manager() -> BudgetManager:
    """获取 BudgetManager 单例

    需先调用 :func:`set_budget_manager` 注入 Redis 完成初始化。

    Returns:
        BudgetManager 实例

    Raises:
        RuntimeError: 未初始化（未注入 Redis）
    """
    if _budget_manager is None:
        raise RuntimeError("BudgetManager not initialized. Call set_budget_manager(redis, ...) first.")
    return _budget_manager


def set_budget_manager(
    redis: Redis,
    daily_budget_usd: float = 10.0,
    warning_threshold: float = 0.8,
) -> BudgetManager:
    """初始化并设置 BudgetManager 单例

    通常在应用启动（lifespan）阶段调用，注入共享的 Redis 客户端。

    Args:
        redis: Redis 异步客户端
        daily_budget_usd: 日预算上限 USD
        warning_threshold: 告警阈值比例

    Returns:
        初始化后的 BudgetManager 实例
    """
    global _budget_manager
    _budget_manager = BudgetManager(
        redis=redis,
        daily_budget_usd=daily_budget_usd,
        warning_threshold=warning_threshold,
    )
    logger.info(
        "budget_manager_initialized",
        daily_budget_usd=daily_budget_usd,
        warning_threshold=warning_threshold,
    )
    return _budget_manager
