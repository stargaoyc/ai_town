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


_PROBE_TEXT = "embedding dimension probe"


async def probe_embedding_dimension(llm_client: Any) -> None:
    """启动时对 MODEL_EMBEDDING 做一次真实探针调用，校验输出维度与 EMBEDDING_DIM 一致（R6-L4）

    check_embedding_dim 只能证明「配置声明」与「物理列」一致，管不到「换模型后输出
    维度漂移」——后者会在向量写入时逐行失败并 5 次熔断、静默不可恢复。

    语义：
    - 探针调用成功但维度 != EMBEDDING_DIM → fail-fast（RuntimeError，中文信息点名模型与维度）
    - 探针调用失败（网络/上游不可达/预算熔断）→ warning + 指标后放行启动：
      boot 不得硬依赖实时 API（本地离线开发也能起），错配成本由后续写入暴露；
      该权衡的代价在告警文案中点明
    - EMBEDDING_PROBE_ENABLED=false → 直接跳过（离线开发开关）
    """
    from src.observability.metrics import EMBEDDING_PROBE_TOTAL

    if not settings.embedding_probe_enabled:
        logger.info("embedding_probe_disabled", model=settings.model_embedding)
        return

    try:
        probe_vec = await llm_client.embed(_PROBE_TEXT)
    except Exception as e:
        EMBEDDING_PROBE_TOTAL.labels(status="unavailable").inc()
        logger.warning(
            "embedding_probe_unavailable",
            model=settings.model_embedding,
            error=str(e),
            message=(
                "Embedding 探针调用失败（网络或上游不可达），跳过实时维度校验继续启动——"
                "若模型输出维度与 EMBEDDING_DIM 不一致，将在此后的向量写入/检索以运行时报错暴露"
            ),
        )
        return

    if len(probe_vec) != settings.embedding_dim:
        EMBEDDING_PROBE_TOTAL.labels(status="dimension_mismatch").inc()
        raise RuntimeError(
            f"MODEL_EMBEDDING={settings.model_embedding} 实时输出维度 {len(probe_vec)} "
            f"与 EMBEDDING_DIM={settings.embedding_dim} 不一致——向量写入将批量失败后逐行熔断、"
            "静默不可恢复；请修正 .env 的 MODEL_EMBEDDING 或 EMBEDDING_DIM（二者必须一致，"
            "且与 pgvector 物理列维度对齐）"
        )

    EMBEDDING_PROBE_TOTAL.labels(status="ok").inc()
    logger.info(
        "embedding_probe_passed",
        model=settings.model_embedding,
        dim=settings.embedding_dim,
    )
