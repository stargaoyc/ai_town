"""启动期安全检查

默认弱凭据（与 .env.example 保持同步）在 ENVIRONMENT=production 下 fail-fast，
开发模式仅告警（S-3 / 审查 P0-1）。

独立于 src.main 的轻量模块：main.py 在导入期会完成可观测性初始化并注册
全局 TracerProvider，任何需要在测试中引用的安全逻辑都不应放在那里。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

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


async def check_embedding_dim(session_factory: Callable[[], AbstractAsyncContextManager[Any]]) -> None:
    """启动时校验 EMBEDDING_DIM 声明与物理列维度一致（R4-H7 纵深防御）

    ORM 已钉死 HALFVEC(2048) 与迁移链对齐；本检查拦截「改了 env 没配套迁移」
    的错配——否则问题会潜伏到首次向量写入/检索才以运行时报错暴露。
    """
    from sqlalchemy import text

    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                "WHERE a.attrelid = 'memory_episodes'::regclass AND a.attname = 'embedding'"
            )
        )
        type_str = result.scalar_one_or_none()
    if type_str is None:
        logger.warning("embedding_dim_check_skipped", reason="memory_episodes.embedding column not found")
        return
    physical = int(str(type_str).split("(")[1].rstrip(")"))
    if physical != settings.embedding_dim:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} 与物理列 {type_str} 不一致："
            "请将 .env 的 EMBEDDING_DIM 改回 2048 或执行配套迁移，二者必须一致"
        )
    logger.info("embedding_dim_check_passed", dim=physical)
