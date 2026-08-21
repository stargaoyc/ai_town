"""src/auth/jwt_handler.py 单元测试

覆盖 JWT 生成与解码，使用测试密钥构造 JWTHandler 实例，
不依赖全局 settings。
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

from src.auth.jwt_handler import JWTHandler
from src.auth.jwt_handler import create_token as module_create_token
from src.auth.jwt_handler import decode_token as module_decode_token

# 测试用密钥与算法（不依赖全局 settings）
_TEST_SECRET = "test-secret-key-for-unit-tests"
_ALGORITHM = "HS256"


@pytest.fixture
def handler() -> JWTHandler:
    return JWTHandler(secret=_TEST_SECRET, algorithm=_ALGORITHM, expire_hours=1)


# ---------------------------------------------------------------------------
# create_token
# ---------------------------------------------------------------------------


def test_create_token_returns_non_empty_string(handler: JWTHandler) -> None:
    token = handler.create_token("user1")
    assert isinstance(token, str)
    assert len(token) > 0


def test_create_token_has_three_segments(handler: JWTHandler) -> None:
    token = handler.create_token("user1")
    parts = token.split(".")
    assert len(parts) == 3
    for part in parts:
        assert len(part) > 0


def test_create_token_different_users_produce_different_tokens(handler: JWTHandler) -> None:
    t1 = handler.create_token("user1")
    t2 = handler.create_token("user2")
    assert t1 != t2


# ---------------------------------------------------------------------------
# decode_token
# ---------------------------------------------------------------------------


def test_decode_token_returns_payload(handler: JWTHandler) -> None:
    token = handler.create_token("user123", claims={"role": "admin"})
    payload = handler.decode_token(token)
    assert isinstance(payload, dict)
    assert payload["sub"] == "user123"
    assert "iat" in payload
    assert "exp" in payload
    assert payload["role"] == "admin"


def test_decode_token_expired_raises_401(handler: JWTHandler) -> None:
    now = datetime.now(UTC)
    expired_payload = {
        "sub": "user1",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    expired_token = jose_jwt.encode(expired_payload, _TEST_SECRET, algorithm=_ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        handler.decode_token(expired_token)
    assert exc_info.value.status_code == 401


def test_decode_token_invalid_signature_raises_401(handler: JWTHandler) -> None:
    handler.create_token("user1")
    # 用错误密钥生成，签名不匹配
    bad_token = jose_jwt.encode(
        {"sub": "user1", "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(hours=1)},
        "wrong-secret",
        algorithm=_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc_info:
        handler.decode_token(bad_token)
    assert exc_info.value.status_code == 401


def test_decode_token_malformed_raises_401(handler: JWTHandler) -> None:
    with pytest.raises(HTTPException) as exc_info:
        handler.decode_token("not-a-valid-token")
    assert exc_info.value.status_code == 401


def test_decode_token_garbage_raises_401(handler: JWTHandler) -> None:
    with pytest.raises(HTTPException) as exc_info:
        handler.decode_token("invalid.token.here")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 模块级便捷函数
# ---------------------------------------------------------------------------


def test_module_level_create_and_decode_roundtrip() -> None:
    token = module_create_token("module_user")
    payload = module_decode_token(token)
    assert payload["sub"] == "module_user"
    assert "iat" in payload
    assert "exp" in payload


def test_module_level_decode_invalid_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        module_decode_token("garbage-token")
    assert exc_info.value.status_code == 401
