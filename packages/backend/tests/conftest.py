"""测试通用 fixtures"""
import os

# Settings() 在 src/config.py 导入时即实例化，需要这些环境变量；
# 测试不会真正连接数据库/Redis，此处仅提供占位值避免导入失败。
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.actions import Action, ActionCategory, ActionRegistry
from src.modules.duration.calculator import DurationCalculator


@pytest.fixture
def sample_state():
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
def registry():
    """空 Action 注册表"""
    return ActionRegistry()


@pytest.fixture
def populated_registry():
    """包含预置 Action 的注册表"""
    reg = ActionRegistry()
    from src.actions import register_all
    register_all(reg)
    return reg


@pytest.fixture
def duration_calculator():
    """耗时计算器"""
    return DurationCalculator()
