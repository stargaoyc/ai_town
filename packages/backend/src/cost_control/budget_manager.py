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

from src.config import settings

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

# === 预留 / 结算 / 释放（审查 §4.8.2 成本-01 + 成本-02）===
#
# LLM 调用的费用只有调用结束后才知道，因此「调用前检查 + 调用后记账」两步
# 天然无法原子：并发下 N 个调用可同时通过检查，实际支出可达 N × 单次成本。
# 正确做法是「预留-结算」：调用前把预估费用计入 reserved（在途额度），
# 使 已用 + 在途 <= 预算 成为硬不变量；调用结束按实际费用结算，
# 失败则释放预留——不释放会让在途额度永久侵蚀预算。

# ARGV=[预估费用, 全局预算, 分域预算(<=0 表示不做分域限制), ttl]
# KEYS[2] 在无分域时传入 KEYS[1]，配合 sbudget<=0 被跳过，保证键声明合法
_LUA_RESERVE = """
local gkey = KEYS[1]
local skey = KEYS[2]
local cost = tonumber(ARGV[1])
local gbudget = tonumber(ARGV[2])
local sbudget = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local function inuse(key)
  local c = tonumber(redis.call('HGET', key, 'cost') or '0')
  local r = tonumber(redis.call('HGET', key, 'reserved') or '0')
  return c, r
end

local gc, gr = inuse(gkey)
if gc + gr + cost > gbudget then
  return {1, gc, gr, 'global'}
end
if sbudget > 0 then
  local sc, sr = inuse(skey)
  if sc + sr + cost > sbudget then
    return {1, sc, sr, 'scope'}
  end
end

redis.call('HINCRBYFLOAT', gkey, 'reserved', cost)
redis.call('EXPIRE', gkey, ttl)
if sbudget > 0 then
  redis.call('HINCRBYFLOAT', skey, 'reserved', cost)
  redis.call('EXPIRE', skey, ttl)
end
return {0, gc, gr + cost, 'ok'}
"""

# ARGV=[预估费用, 实际费用, tokens, 分域预算(<=0 跳过), ttl]
_LUA_SETTLE = """
local gkey = KEYS[1]
local skey = KEYS[2]
local estimated = tonumber(ARGV[1])
local actual = tonumber(ARGV[2])
local tokens = tonumber(ARGV[3])
local sbudget = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

redis.call('HINCRBYFLOAT', gkey, 'reserved', -estimated)
local gc = redis.call('HINCRBYFLOAT', gkey, 'cost', actual)
redis.call('HINCRBY', gkey, 'tokens', tokens)
redis.call('HINCRBY', gkey, 'count', 1)
redis.call('EXPIRE', gkey, ttl)
if sbudget > 0 then
  redis.call('HINCRBYFLOAT', skey, 'reserved', -estimated)
  redis.call('HINCRBYFLOAT', skey, 'cost', actual)
  redis.call('HINCRBY', skey, 'tokens', tokens)
  redis.call('HINCRBY', skey, 'count', 1)
  redis.call('EXPIRE', skey, ttl)
end
return {0, tonumber(gc)}
"""

# 释放预留并钳制下限为 0：重复释放或浮点漂移都不允许把 reserved 压成负数
_LUA_RELEASE = """
local gkey = KEYS[1]
local skey = KEYS[2]
local estimated = tonumber(ARGV[1])
local sbudget = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local function release(key)
  local r = tonumber(redis.call('HGET', key, 'reserved') or '0')
  if r <= 0 then
    r = 0
  else
    r = r - estimated
    if r < 0 then r = 0 end
  end
  redis.call('HSET', key, 'reserved', tostring(r))
  redis.call('EXPIRE', key, ttl)
  return r
end

local gr = release(gkey)
local sr = 0
if sbudget > 0 then sr = release(skey) end
return {0, gr, sr}
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
            ``{"tokens": int, "cost": float, "count": int, "reserved": float}``，
            key 不存在时各字段为 0。reserved 为在途预留额度（未结算部分）。
        """
        key = self._today_key()
        raw = await self.redis.hgetall(key)
        if not raw:
            return {"tokens": 0, "cost": 0.0, "count": 0, "reserved": 0.0}
        return {
            "tokens": int(raw.get("tokens", 0)),
            "cost": float(raw.get("cost", 0.0)),
            "count": int(raw.get("count", 0)),
            "reserved": float(raw.get("reserved", 0.0)),
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
        # 在途预留额度必须计入已用：否则并发调用看到的是结算前的旧值，
        # 预留机制的意义正是让「已用 + 在途」一起参与判定（审查 §4.8.2）
        used = usage["cost"] + usage["reserved"]
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
            "settled": usage["cost"],
            "reserved": usage["reserved"],
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

    # === 预留 / 结算 / 释放 ===

    @staticmethod
    def _scope_key(scope: str, identifier: str) -> str:
        """分域计费键：llm:cost:{date}:{scope}:{identifier}"""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"llm:cost:{today}:{scope}:{identifier}"

    @staticmethod
    def _scope_budget(scope: str) -> float:
        """分域预算上限；<=0 表示该维度不限制"""
        if scope == "char":
            return float(settings.llm_daily_budget_per_character_usd)
        if scope == "user":
            return float(settings.llm_daily_budget_per_user_usd)
        return 0.0

    async def reserve(
        self,
        estimated_cost: float,
        scope: str | None = None,
        identifier: str | None = None,
    ) -> float:
        """原子预留额度（审查 §4.8.2 成本-01 / 成本-02）

        把预估费用计入 reserved，使「已用 + 在途 <= 预算」成为硬不变量。
        分域维度（角色/用户）在同一脚本内一并校验，避免两个维度各自原子、
        合起来仍可击穿的窗口。

        Args:
            estimated_cost: 预估费用 USD
            scope: 分域类型，"char"（角色）/ "user"（用户）；None 表示仅校验全局
            identifier: 分域标识（角色 ID / 用户 ID）

        Returns:
            实际预留的金额（等于 estimated_cost）

        Raises:
            BudgetExceeded: 全局或分域额度不足
        """
        gkey = self._today_key()
        sbudget = self._scope_budget(scope) if scope else 0.0
        skey = self._scope_key(scope, identifier or "") if (scope and sbudget > 0) else gkey
        result = await self.redis.eval(
            _LUA_RESERVE,
            2,
            gkey,
            skey,
            str(float(estimated_cost)),
            str(float(self.daily_budget_usd)),
            str(float(sbudget)),
            _KEY_TTL_SECONDS,
        )
        flag = int(result[0])
        if flag == 1:
            used = float(result[1]) + float(result[2])
            kind = str(result[3])
            budget = self.daily_budget_usd if kind == "global" else sbudget
            logger.warning(
                "budget_reserve_rejected",
                scope=scope or "global",
                identifier=identifier,
                kind=kind,
                used=used,
                budget=budget,
            )
            raise BudgetExceeded(used=used, budget=budget, remaining=budget - used)
        return estimated_cost

    async def settle(
        self,
        estimated_cost: float,
        actual_cost: float,
        tokens: int,
        scope: str | None = None,
        identifier: str | None = None,
    ) -> dict[str, Any]:
        """结算一次调用：释放预留额度并计入实际费用

        Args:
            estimated_cost: 预留时使用的预估费用
            actual_cost: 实际费用 USD
            tokens: 实际消耗 token 数
            scope: 分域类型（与 reserve 保持一致）
            identifier: 分域标识

        Returns:
            更新后的全局 usage
        """
        gkey = self._today_key()
        sbudget = self._scope_budget(scope) if scope else 0.0
        skey = self._scope_key(scope, identifier or "") if (scope and sbudget > 0) else gkey
        await self.redis.eval(
            _LUA_SETTLE,
            2,
            gkey,
            skey,
            str(float(estimated_cost)),
            str(float(actual_cost)),
            int(tokens),
            str(float(sbudget)),
            _KEY_TTL_SECONDS,
        )
        return await self.get_today_usage()

    async def release(
        self,
        estimated_cost: float,
        scope: str | None = None,
        identifier: str | None = None,
    ) -> None:
        """释放预留额度（调用失败路径）

        不释放会让在途额度永久侵蚀可用预算——连续失败即耗尽预算，
        即使实际分文未花。
        """
        gkey = self._today_key()
        sbudget = self._scope_budget(scope) if scope else 0.0
        skey = self._scope_key(scope, identifier or "") if (scope and sbudget > 0) else gkey
        try:
            await self.redis.eval(
                _LUA_RELEASE,
                2,
                gkey,
                skey,
                str(float(estimated_cost)),
                str(float(sbudget)),
                _KEY_TTL_SECONDS,
            )
        except Exception as e:
            # 释放失败不能掩盖原始调用异常，仅记录供对账
            logger.warning("budget_reserve_release_failed", error=str(e))


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
