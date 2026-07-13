"""RBAC 角色权限控制"""

from fastapi import HTTPException, Request

from src.auth import decode_token


def require_role(*roles: str):
    """要求用户具有指定角色之一

    用法：
        @app.delete("/api/v1/characters/{id}")
        async def delete_character(id: UUID, user=Depends(require_role("admin", "operator"))):
            ...
    """
    async def dependency(request: Request):
        # 从请求头获取 token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Not authenticated")

        token = auth_header[7:]
        try:
            payload = decode_token(token)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token") from None

        user_role = payload.get("role", "viewer")
        if user_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required: {roles}, have: {user_role}"
            )

        return {"username": payload.get("sub"), "role": user_role}

    return dependency


# 角色权限矩阵
ROLE_PERMISSIONS = {
    "admin": {"read", "write", "delete", "config", "admin"},
    "operator": {"read", "write", "trigger_tick"},
    "viewer": {"read"},
}
