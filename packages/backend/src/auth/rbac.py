"""RBAC 角色权限控制 - 权限模型的单一归属

角色模型与 JWT 的 role claim 同源（jwt_handler.resolve_role），
配置来源为 settings.rbac_roles（"user:role,user:role"）。

设计要点：
- **JWT 与 API Key 同等对待**：两者都解析出角色后走同一套比对逻辑。
  此前 require_role 只认 Bearer JWT，导致 API Key 无法用于任何受角色
  保护的端点——运维自动化只能共享管理员账号密码（审查 §6 安全-01）。
- **API Key 的角色显式可配**：静态 Key 的角色由 settings.api_key_role 决定，
  不再是「写了 scopes=[] 却无人读取」的假权限声明。
- **归属校验集中在此**：此前各端点自行拼 `if user_id != user["user_id"]`，
  62 个端点靠人工保证，漏一个即越权（审查 §6 安全-03）。
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from src.auth.middleware import get_current_user
from src.auth.roles import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER

# 聚合类端点（跨用户读）所需角色：去重前该常量在 characters.py 与 memory.py
# 各存一份，改一处漏一处就会造成权限判定不一致
PRIVILEGED_ROLES = frozenset({ROLE_ADMIN, ROLE_OPERATOR})


async def principal_with_role(request: Request) -> dict[str, Any]:
    """鉴权并返回带角色的主体

    Returns:
        {"user_id", "auth_method", "role"}；未携带角色信息时按最小权限 viewer
    """
    user = await get_current_user(request)
    return {"user_id": user["user_id"], "auth_method": user["auth_method"], "role": user.get("role", ROLE_VIEWER)}


# 依赖类型别名（规避 B008：不在函数默认参数中调用 Depends）
PrincipalWithRole = Annotated[dict[str, Any], Depends(principal_with_role)]


def require_role(*roles: str) -> Callable[[Request], Coroutine[Any, Any, dict[str, Any]]]:
    """要求调用方具有指定角色之一

    JWT 与 API Key 均可满足：JWT 取 role claim，API Key 取签发/配置时
    绑定的角色。此前只认 JWT，等于把机器客户端挡在所有受保护端点之外。

    用法：
        @router.delete("/api/v1/characters/{id}")
        async def delete_character(id: UUID, user=Depends(require_role("admin"))):
            ...
    """

    async def dependency(request: Request) -> dict[str, Any]:
        principal = await principal_with_role(request)
        role = principal["role"]
        if role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required: {roles}, have: {role}",
            )
        return principal

    return dependency


def is_privileged(principal: dict[str, Any]) -> bool:
    """主体是否具特权角色——用于「按归属过滤」而非「直接拒绝」的聚合端点"""
    return principal["role"] in PRIVILEGED_ROLES


def assert_owner_or_privileged(principal: dict[str, Any], resource_user_id: str) -> None:
    """断言主体可访问目标用户的资源：本人或特权角色

    集中承载归属校验（审查 §6 安全-03）：此前各端点自行拼装判断式，
    62 个端点靠人工保证，漏写即越权。集中在唯一实现后，新端点只要
    调用本函数就不会漏。

    Args:
        principal: principal_with_role / require_role 的返回值
        resource_user_id: 资源归属用户 ID

    Raises:
        HTTPException: 403 当既非本人也非特权角色
    """
    if resource_user_id == principal["user_id"]:
        return
    if principal["role"] in PRIVILEGED_ROLES:
        return
    raise HTTPException(status_code=403, detail="无权访问其他用户的资源")
