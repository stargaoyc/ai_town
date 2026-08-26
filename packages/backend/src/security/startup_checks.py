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

# 开发模式告警每进程只发一次：适配器每次构造都会跑本检查，逐条刷屏无增量信息
_ONEBOT_TOKEN_WARNED = False


def check_onebot_access_token() -> None:
    """OneBot 反向 WS 端点未配令牌时生产拒绝启动；开发仅首次告警（R5-H5）

    /ws/onebot/v12 无鉴权时任何客户端可伪造消息事件驱动 LLM 消耗并操纵机器人
    向任意群/私聊发消息，属未鉴权控制面暴露，不能靠事后日志兜底。
    """
    global _ONEBOT_TOKEN_WARNED

    if settings.onebot_access_token:
        return
    if settings.environment == "production":
        logger.error(
            "onebot_access_token_missing_blocked",
            message="ONEBOT_ACCESS_TOKEN 未配置，未鉴权的 /ws/onebot/v12 允许伪造 QQ 事件，生产模式禁止启动",
        )
        raise RuntimeError("ONEBOT_ACCESS_TOKEN must be set when ENVIRONMENT=production")
    if not _ONEBOT_TOKEN_WARNED:
        _ONEBOT_TOKEN_WARNED = True
        logger.warning(
            "onebot_access_token_missing",
            message="ONEBOT_ACCESS_TOKEN 未配置，/ws/onebot/v12 接受任意客户端连接；生产环境将拒绝启动",
        )


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


def check_cors_origins() -> None:
    """生产模式下 CORS_ORIGINS 为空即拒绝启动（P0-7）

    main.py 的 CORS 中间件在未配置时静默降级为仅同源——开发便利，
    但生产环境前端跨域会以浏览器侧模糊报错暴露且无任何服务端信号。
    生产必须在部署清单显式声明前端来源列表。
    """
    if settings.environment != "production":
        if not settings.cors_origins.strip():
            logger.warning(
                "cors_origins_not_configured_dev",
                message="CORS_ORIGINS 未配置，跨域请求将被拒绝；生产环境将拒绝启动",
            )
        return
    if not settings.cors_origins.strip():
        logger.error(
            "cors_origins_missing_blocked",
            message="CORS_ORIGINS 未配置，生产模式下前端跨域必然失败，禁止启动；请配置实际前端域名列表",
        )
        raise RuntimeError("CORS_ORIGINS must be set when ENVIRONMENT=production")


# 需要与 EMBEDDING_DIM 对齐的向量列：memory_episodes 自迁移 0005、
# reflections 自迁移 0015 均为 halfvec(2048)；漏掉任一列都会把错配
# 潜伏到该列首次向量写入/检索才暴露
_VECTOR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("memory_episodes", "embedding"),
    ("reflections", "embedding"),
)


async def check_embedding_dim(session_factory: Callable[[], AbstractAsyncContextManager[Any]]) -> None:
    """启动时校验 EMBEDDING_DIM 声明与全部向量列物理维度一致（R4-H7 纵深防御）

    ORM 已钉死 HALFVEC(2048) 与迁移链对齐；本检查拦截「改了 env 没配套迁移」
    的错配——否则问题会潜伏到首次向量写入/检索才以运行时报错暴露。
    halfvec 的维度记录在列 typmod 上（information_schema 对其返回 NULL），
    必须经 pg_attribute + format_type 读取。
    """
    from sqlalchemy import text

    mismatches: list[str] = []
    async with session_factory() as session:
        for table, column in _VECTOR_COLUMNS:
            result = await session.execute(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                    "WHERE a.attrelid = CAST(:table AS regclass) AND a.attname = :column"
                ),
                {"table": table, "column": column},
            )
            type_str = result.scalar_one_or_none()
            if type_str is None:
                # 列不存在（全新库尚未跑迁移）只告警不阻断，语义与原单列检查一致
                logger.warning("embedding_dim_check_skipped", reason=f"{table}.{column} column not found")
                continue
            physical = int(str(type_str).split("(")[1].rstrip(")"))
            if physical != settings.embedding_dim:
                mismatches.append(f"{table}.{column} 为 {type_str}")
    if mismatches:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} 与物理列不一致：{'；'.join(mismatches)}——"
            "请将 .env 的 EMBEDDING_DIM 改回 2048 或执行配套迁移，二者必须一致"
        )
    logger.info("embedding_dim_check_passed", dim=settings.embedding_dim, columns=len(_VECTOR_COLUMNS))
