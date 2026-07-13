"""FastAPI 速率限制依赖"""

from fastapi import HTTPException, Request

from src.runtime import get_rate_limiter


def rate_limit(key_prefix: str, max_requests: int = 60, window_seconds: int = 60):
    """创建速率限制依赖

    用法：
        @app.post("/api/v1/messages/send", dependencies=[Depends(rate_limit("msg_send", 60, 60))])
    """
    async def dependency(request: Request):
        limiter = get_rate_limiter()
        if not limiter:
            return  # 限流器不可用时放行

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
