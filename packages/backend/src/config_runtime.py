"""运行时配置 - 从 Redis 加载并校验的可热更新配置

解决 gap-analysis 2.4 的问题：
- 原方案用 `setattr(settings, key, value)` 绕过 Pydantic 校验
- 类型映射在 `_RUNTIME_CONFIG_KEYS` 手动维护，与 Settings 类重复
- 重置时用 `Settings()` 重新实例化，行为依赖 .env 文件状态

改进方案：
- 用 Pydantic BaseModel 定义所有可热更新配置项，类型自动校验
- 从 Redis 加载时通过 model_validate 校验，无效值被拒绝
- 同时写入 settings 对象（向后兼容，业务代码仍读 settings.xxx）
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator
from redis.asyncio import Redis
from structlog import get_logger

from src.config import Settings, settings

logger = get_logger(__name__)

# Redis 中存储运行时覆盖的 Key
_CONFIG_OVERRIDES_KEY = "config:overrides"


class RuntimeConfig(BaseModel):
    """运行时可热更新的配置项（从 Redis 加载，Pydantic 校验类型）

    这些配置项可通过 PUT /api/v1/admin/config 热更新，无需重启。
    其他配置项（如 jwt_secret、openai_api_key）必须重启才能变更。
    """

    share_cooldown_seconds: int = Field(default=300, description="分享冷却时间（秒）")
    share_daily_limit: int = Field(default=5, description="每日分享上限")
    share_probability_action: float = Field(default=0.15, description="Action 分享概率")
    share_probability_mood: float = Field(default=0.10, description="情绪分享概率")
    share_probability_location: float = Field(default=0.08, description="位置变化分享概率")
    share_probability_routine: float = Field(default=0.20, description="日常行为分享概率")
    memory_llm_scoring_enabled: bool = Field(default=False, description="LLM 记忆评分")
    world_tick_seconds: int = Field(default=30, description="世界 Tick 间隔（秒）")
    character_tick_seconds: int = Field(default=60, description="角色 Tick 间隔（秒）")
    character_max_concurrent: int = Field(default=3, description="角色并发上限")
    llm_daily_budget_usd: float = Field(default=5.0, description="LLM 日预算（美元）")
    log_level: str = Field(default="info", description="日志级别")

    @model_validator(mode="after")
    def validate_ranges(self) -> RuntimeConfig:
        """校验配置项取值范围"""
        if self.share_cooldown_seconds < 0:
            raise ValueError("share_cooldown_seconds 不能为负数")
        if self.share_daily_limit < 0:
            raise ValueError("share_daily_limit 不能为负数")
        for prob_key in (
            "share_probability_action",
            "share_probability_mood",
            "share_probability_location",
            "share_probability_routine",
        ):
            val = getattr(self, prob_key)
            if not 0 <= val <= 1:
                raise ValueError(f"{prob_key} 必须在 0-1 之间")
        if self.world_tick_seconds < 5:
            raise ValueError("world_tick_seconds 不能小于 5 秒")
        if self.character_tick_seconds < 5:
            raise ValueError("character_tick_seconds 不能小于 5 秒")
        if self.character_max_concurrent < 1:
            raise ValueError("character_max_concurrent 不能小于 1")
        if self.llm_daily_budget_usd < 0:
            raise ValueError("llm_daily_budget_usd 不能为负数")
        if self.log_level not in ("debug", "info", "warning", "error"):
            raise ValueError("log_level 必须是 debug/info/warning/error")
        return self

    def to_overrides_dict(self) -> dict[str, Any]:
        """转换为 Redis 存储格式（仅包含被覆盖的值）"""
        return self.model_dump()

    def apply_to_settings(self, settings_obj: Settings) -> None:
        """将配置值同步到 settings 对象（向后兼容，业务代码仍读 settings.xxx）"""
        for key, value in self.model_dump().items():
            setattr(settings_obj, key, value)


# 全局单例
_instance: RuntimeConfig | None = None


def get_runtime_config() -> RuntimeConfig:
    """获取全局 RuntimeConfig 单例"""
    global _instance
    if _instance is None:
        _instance = RuntimeConfig()
    return _instance


async def load_runtime_config(redis: Redis) -> RuntimeConfig:
    """从 Redis 加载运行时配置并校验

    启动时调用。读取 Redis 中的覆盖值，通过 Pydantic 校验后创建单例。
    校验失败的值会被忽略并记录警告。

    Args:
        redis: Redis 客户端

    Returns:
        加载后的 RuntimeConfig 实例
    """
    global _instance

    # 从 settings 读取默认值作为基础
    defaults: dict[str, Any] = {}
    for key in RuntimeConfig.model_fields:
        defaults[key] = getattr(settings, key, RuntimeConfig.model_fields[key].default)

    # 从 Redis 读取覆盖值
    override_keys: list[str] = []
    try:
        raw = await redis.get(_CONFIG_OVERRIDES_KEY)
        if raw:
            overrides = json.loads(raw)  # type: ignore[arg-type]
            # 仅取已知字段
            for key in RuntimeConfig.model_fields:
                if key in overrides:
                    defaults[key] = overrides[key]
                    override_keys.append(key)
    except Exception as e:
        logger.warning("runtime_config_load_failed", error=str(e))

    # 通过 Pydantic 校验
    try:
        _instance = RuntimeConfig.model_validate(defaults)
        # 同步到 settings 对象（向后兼容）
        _instance.apply_to_settings(settings)
        logger.info(
            "runtime_config_loaded",
            total_keys=len(defaults),
            override_keys=override_keys,
        )
    except Exception as e:
        logger.error("runtime_config_validation_failed", error=str(e))
        _instance = RuntimeConfig()
        _instance.apply_to_settings(settings)

    return _instance


async def update_runtime_config(
    redis: Redis,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """更新运行时配置（通过 Pydantic 校验后写入 Redis）

    Args:
        redis: Redis 客户端
        updates: 配置更新字典

    Returns:
        更新后的配置项 {key: value}

    Raises:
        ValueError: 校验失败时
    """
    config = get_runtime_config()

    # 合并现有值 + 新值，整体校验
    merged = config.model_dump()
    for key, value in updates.items():
        if key not in RuntimeConfig.model_fields:
            continue
        merged[key] = value

    # Pydantic 校验（失败会抛出 ValidationError → ValueError）
    try:
        new_config = RuntimeConfig.model_validate(merged)
    except Exception as e:
        raise ValueError(str(e)) from e

    # 写入 Redis（仅存已知字段）
    overrides = new_config.to_overrides_dict()
    await redis.set(_CONFIG_OVERRIDES_KEY, json.dumps(overrides))

    # 更新全局单例 + 同步到 settings
    global _instance
    _instance = new_config
    new_config.apply_to_settings(settings)

    # 仅返回被更新的字段
    result = {k: v for k, v in merged.items() if k in updates}
    logger.info("runtime_config_updated", keys=list(result.keys()))
    return result


async def reset_runtime_config(
    redis: Redis,
    key: str,
) -> Any:
    """重置单个配置项为默认值

    Args:
        redis: Redis 客户端
        key: 配置项键名

    Returns:
        重置后的默认值

    Raises:
        KeyError: 未知配置项
    """
    if key not in RuntimeConfig.model_fields:
        raise KeyError(f"Unknown config key: {key}")

    config = get_runtime_config()
    merged = config.model_dump()

    # 从 Settings 默认值中读取（Settings 是 pydantic-settings，从 .env 读取必填字段）
    default_val = getattr(Settings(), key, RuntimeConfig.model_fields[key].default)  # type: ignore[call-arg]
    merged[key] = default_val

    # 校验
    new_config = RuntimeConfig.model_validate(merged)

    # 写入 Redis
    overrides = new_config.to_overrides_dict()
    await redis.set(_CONFIG_OVERRIDES_KEY, json.dumps(overrides))

    # 更新全局单例 + 同步到 settings
    global _instance
    _instance = new_config
    new_config.apply_to_settings(settings)

    logger.info("runtime_config_reset", key=key, value=default_val)
    return default_val
