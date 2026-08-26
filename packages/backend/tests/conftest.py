"""测试通用 fixtures"""

import os
from typing import Any, cast

# Settings() 在 src/config.py 导入时即实例化，需要这些环境变量；
# 测试不会真正连接数据库/Redis，此处仅提供占位值避免导入失败。
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef-0123456789abcdef")


import pytest
from redis.asyncio import Redis

from src.actions import ActionRegistry
from src.modules.duration.calculator import DurationCalculator


class FakeRedis:
    """SceneLoader 构造用替身：load_configs_sync 不触碰 Redis，仅满足类型"""


@pytest.fixture
def sample_state() -> dict[str, Any]:
    """标准角色状态字典"""
    return {
        "location": "home",
        "stamina": 80,
        "satiety": 60,
        "mood": "calm",
        "money": 500,
        "phone_battery": 75,
        "social_energy": 60,
        "current_action": None,
    }


@pytest.fixture
def registry() -> ActionRegistry:
    """空 Action 注册表"""
    return ActionRegistry()


@pytest.fixture
def populated_registry() -> ActionRegistry:
    """包含预置 Action 的注册表（注入真实场景配置以解析 scene_tags）"""
    from src.actions import register_all
    from src.modules.town.loader import SceneLoader
    from src.paths import find_project_root

    loader = SceneLoader(cast(Redis, FakeRedis()))
    project_root = find_project_root()
    loader.load_configs_sync(
        project_root / "configs" / "scenes.yaml",
        project_root / "configs" / "world-map.yaml",
    )
    reg = ActionRegistry(scene_loader=loader)
    register_all(reg)
    return reg


@pytest.fixture
def duration_calculator() -> DurationCalculator:
    """耗时计算器"""
    return DurationCalculator()
