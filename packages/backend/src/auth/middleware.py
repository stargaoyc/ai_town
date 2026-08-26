"""FastAPI 鉴权依赖 - JWT + API Key 双模式

职责：
1. 从请求头读取凭证（Authorization: Bearer 或 X-API-Key）
2. 优先校验 Bearer JWT，其次校验 API Key
3. 返回 user info dict 供路由使用
4. 无有效凭证统一抛 HTTPException(401, "Not authenticated")

API Key 校验来源：
1. settings.api_key（静态配置，可为 None）
2. APIKeyManager（动态生成的内存 Key）

使用方式：
    from fastapi import Depends
    from src.auth import auth_dependency

    @app.get("/protected")
    async def protected(user: dict = Depends(auth_dependency)):
        return {"user_id": user["user_id"]}
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Receive, Scope, Send
from structlog import get_logger

from src.auth.api_keys import api_key_manager
from src.auth.jwt_handler import decode_token
from src.config import settings

logger = get_logger(__name__)

# 鉴权失败统一响应
_NOT_AUTHENTICATED = HTTPException(status_code=401, detail="Not authenticated")


async def auth_dependency(request: Request) -> dict[str, Any]:
    """FastAPI 鉴权依赖 - 支持 JWT 与 API Key 双模式

    校验顺序：
    1. Authorization: Bearer <jwt_token>
    2. X-API-Key: <api_key>

    Args:
        request: FastAPI Request 对象

    Returns:
        用户信息 dict: {"user_id": str, "auth_method": "jwt"|"api_key"}

    Raises:
        HTTPException: 401 当无有效凭证
    """
    # 1. 优先检查 Bearer JWT
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if token:
                try:
                    payload = decode_token(token)
                    user_id = payload.get("sub") or ""
                    return {"user_id": str(user_id), "auth_method": "jwt"}
                except HTTPException:
                    # JWT 无效，降级尝试 API Key
                    pass

    # 2. 检查 X-API-Key
    api_key = request.headers.get("X-API-Key")
    if api_key:
        user_info = _validate_api_key(api_key)
        if user_info is not None:
            return {
                "user_id": str(user_info["user_id"]),
                "auth_method": "api_key",
            }

    # 无有效凭证
    logger.warning(
        "auth_failed",
        path=request.url.path,
        has_auth_header=auth_header is not None,
        has_api_key=api_key is not None,
    )
    raise _NOT_AUTHENTICATED


def _validate_api_key(key: str) -> dict[str, Any] | None:
    """校验 API Key - 同时检查静态配置与动态生成的 Key

    Args:
        key: API Key 字符串

    Returns:
        user info dict（含 user_id）或 None
    """
    # 1. 静态配置的 API Key（settings.api_key）
    # 使用 compare_digest 防止时序攻击
    if settings.api_key and secrets.compare_digest(key, settings.api_key):
        return {
            "user_id": "static",
            "scopes": [],
            "created_at": None,
        }

    # 2. 动态生成的 API Key（APIKeyManager）
    return api_key_manager.validate_key(key)


# auth_dependency 的别名
get_current_user = auth_dependency


class AuthMiddleware:
    """ASGI 鉴权中间件：仅 /api/ 路径需要鉴权，WebSocket 和其他路径豁免

    鉴权策略：
    - 非 /api/ 路径（/health, /metrics, /docs 等）→ 豁免
    - /api/v1/auth/login → 豁免（登录接口）
    - GET /api/v1/ 只读公开端点 → 豁免（Dashboard 无需登录可查看）
    - 其他 /api/ 请求（POST/PUT/DELETE）→ 需要 JWT 或 API Key
    """

    # 公开只读 GET 路径前缀（无需登录即可查看）
    # P0-8：移除 messages/conversations/admin 前缀——聊天记录、管理日志、运行时配置
    # 含用户隐私与运维敏感信息，必须登录后按归属校验访问
    # R4-H2：characters/memories 前缀同样移除——person-memory/diaries/记忆流等
    # 用户衍生内容挂在其下，前缀级豁免会击穿隐私边界；Dashboard 已登录流量始终携带 token，不受影响
    PUBLIC_GET_PREFIXES = (
        "/api/v1/world",
        "/api/v1/actions",
        "/api/v1/town/scenes",
        "/api/v1/modules",
    )

    # 精确豁免：自带独立鉴权（非 JWT）的端点。
    # alerts/webhook 由 Alertmanager 以 bearer token 调用，无法提供 JWT，
    # 鉴权在端点内完成（settings.alert_webhook_token）
    PUBLIC_EXACT_PATHS = frozenset({"/api/v1/system/alerts/webhook"})

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def _public_rate_limit_ok(self, scope: Scope) -> bool:
        """公开 GET 的每 IP 固定窗口限流（P1-25）；限流器未就绪时放行"""
        from src.config import settings as _settings
        from src.runtime import get_rate_limiter

        limit = _settings.public_get_rate_limit_per_minute
        if limit <= 0:
            return True
        limiter = get_rate_limiter()
        if limiter is None:
            return True
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        return await limiter.check(f"public_get:{ip}", max_requests=limit, window_seconds=60)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan 直接透传
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # 豁免：非 /api/ 路径、登录接口
        if not path.startswith("/api/") or path == "/api/v1/auth/login":
            await self.app(scope, receive, send)
            return

        # 豁免：自带独立鉴权的精确路径（Alertmanager webhook 等）
        if path in self.PUBLIC_EXACT_PATHS:
            await self.app(scope, receive, send)
            return

        # 豁免：GET 只读公开端点（Dashboard 无需登录可查看）
        if method == "GET":
            for prefix in self.PUBLIC_GET_PREFIXES:
                if path.startswith(prefix):
                    # P1-25：公开端点免鉴权不免滥用——按客户端 IP 限流，
                    # 抑制对 world/tick 时序等公开读的高频爬取
                    if not await self._public_rate_limit_ok(scope):
                        await _send_429(send)
                        return
                    await self.app(scope, receive, send)
                    return

        # 从 headers 中提取 Authorization
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        api_key_header = headers.get(b"x-api-key", b"").decode()

        authenticated = False
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                decode_token(token)
                authenticated = True
            except Exception:
                pass
        elif api_key_header and _validate_api_key(api_key_header):
            authenticated = True

        if not authenticated:
            await _send_401(send)
            return

        await self.app(scope, receive, send)


async def _send_401(send: Send) -> None:
    body = b'{"detail":"Not authenticated"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


async def _send_429(send: Send) -> None:
    body = b'{"detail":"Too many requests"}'
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
                [b"retry-after", b"60"],
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )
