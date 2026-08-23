"""FastAPI 速率限制依赖"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request

from src.runtime import get_rate_limiter


def rate_limit(
    key_prefix: str,
    max_requests: int = 60,
    window_seconds: int = 60,
    *,
    fail_closed: bool = False,
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    """创建速率限制依赖

    用法：
        @app.post("/api/v1/messages/send", dependencies=[Depends(rate_limit("msg_send", 60, 60))])

    Args:
        fail_closed: 限流器不可用时的策略。登录等暴力破解防护端点应设 True
            （拒绝而非放行，S-4）；普通业务端点保持 False（可用性优先）。
    """

    async def dependency(request: Request) -> None:
        limiter = get_rate_limiter()
        if not limiter:
            if fail_closed:
                from fastapi import HTTPException

                raise HTTPException(status_code=503, detail="Rate limiter unavailable")
            return  # 限流器不可用时放行（fail-open）

        # 从请求中提取用户标识（IP 或用户名）
        client_ip = request.client.host if request.client else "unknown"
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            from src.auth import decode_token

            try:
                payload = decode_token(auth_header[7:])
                user_id = payload.get("sub", client_ip)
            except Exception:
                user_id = client_ip
        else:
            user_id = client_ip

        key = f"{key_prefix}:{user_id}"
        allowed = await limiter.check(key, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Retry after {window_seconds}s.",
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
