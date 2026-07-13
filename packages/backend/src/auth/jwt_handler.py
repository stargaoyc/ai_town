"""JWT 令牌生成与验证 - 基于 python-jose

职责：
1. 生成 JWT（包含 user_id + 自定义 claims + 过期时间）
2. 解码验证 JWT（签名校验 + 过期校验）
3. 失败统一抛 HTTPException(401)

设计要点：
- 使用 python-jose[cryptography] 的 jwt 模块
- 配置从 src.config.settings 读取（jwt_secret / jwt_algorithm / jwt_expire_hours）
- 模块级便捷函数委托给单例 _handler，便于在路由中直接 import 使用
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from jose import JWTError, jwt
from structlog import get_logger

from src.config import settings

logger = get_logger(__name__)


class JWTHandler:
    """JWT 处理器 - 生成与验证 JSON Web Token"""

    def __init__(self, secret: str, algorithm: str, expire_hours: int) -> None:
        self.secret = secret
        self.algorithm = algorithm
        self.expire_hours = expire_hours

    def create_token(self, user_id: str, claims: dict[str, Any] | None = None) -> str:
        """生成 JWT

        Args:
            user_id: 用户标识（写入 `sub` 声明）
            claims: 额外自定义声明（可选，会合并进 payload）

        Returns:
            编码后的 JWT 字符串
        """
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(hours=self.expire_hours),
            "role": _resolve_role(user_id),
        }
        if claims:
            payload.update(claims)

        token = jwt.encode(payload, self.secret, algorithm=self.algorithm)
        logger.debug("jwt_created", user_id=user_id)
        return token

    def decode_token(self, token: str) -> dict[str, Any]:
        """解码并验证 JWT

        Args:
            token: JWT 字符串

        Returns:
            解码后的 payload dict

        Raises:
            HTTPException: 401 当 token 过期、签名无效或格式错误
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self.secret,
                algorithms=[self.algorithm],
            )
        except JWTError as e:
            logger.warning("jwt_decode_failed", error=str(e))
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
            ) from e
        return payload


# 模块级单例 - 从 settings 读取配置
_handler = JWTHandler(
    secret=settings.jwt_secret,
    algorithm=settings.jwt_algorithm,
    expire_hours=settings.jwt_expire_hours,
)


def _resolve_role(user_id: str) -> str:
    """根据用户名解析 RBAC 角色

    角色解析规则：
    1. 若 settings.rbac_roles 为空（未配置），默认返回 "admin"（向后兼容）
    2. 若已配置 rbac_roles，解析 "user:role" 映射
    3. 用户名命中映射则返回对应角色
    4. 用户名未命中映射则返回 "viewer"（最小权限原则）
    """
    raw = settings.rbac_roles
    if not raw:
        return "admin"
    mapping: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        name, role = item.split(":", 1)
        mapping[name.strip()] = role.strip()
    return mapping.get(user_id, "viewer")


def create_token(user_id: str, claims: dict[str, Any] | None = None) -> str:
    """模块级便捷函数 - 生成 JWT（委托给单例 _handler）"""
    return _handler.create_token(user_id, claims)


def decode_token(token: str) -> dict[str, Any]:
    """模块级便捷函数 - 解码验证 JWT（委托给单例 _handler）"""
    return _handler.decode_token(token)
