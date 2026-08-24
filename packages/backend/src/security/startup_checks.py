"""启动期安全检查

默认弱凭据（与 .env.example 保持同步）在 ENVIRONMENT=production 下 fail-fast，
开发模式仅告警（S-3 / 审查 P0-1）。

独立于 src.main 的轻量模块：main.py 在导入期会完成可观测性初始化并注册
全局 TracerProvider，任何需要在测试中引用的安全逻辑都不应放在那里。
"""

from __future__ import annotations

from structlog import get_logger

from src.config import settings

logger = get_logger(__name__)

_INSECURE_DEFAULTS: list[tuple[str, str, str]] = [
    ("admin_password", "admin123", "ADMIN_PASSWORD"),
    ("jwt_secret", "your-super-secret-key-change-in-production", "JWT_SECRET"),
    ("api_key", "your-api-key", "API_KEY"),
]


def check_default_secrets() -> None:
    """生产模式下任一凭据仍为公开默认值即拒绝启动；开发模式逐项告警"""
    for field, default, label in _INSECURE_DEFAULTS:
        value = getattr(settings, field, None)
        if value != default:
            continue
        if settings.environment == "production":
            logger.error(
                "insecure_default_secret_blocked",
                message=f"{label} 仍为默认值 '{default}'，生产模式禁止启动；请在 .env 中设置强密钥",
            )
            raise RuntimeError(f"{label} must be changed from the default in production mode")
        logger.warning(
            "insecure_default_secret",
            message=f"{label} 仍为默认值 '{default}'，请在 .env 中修改为强密钥",
        )
