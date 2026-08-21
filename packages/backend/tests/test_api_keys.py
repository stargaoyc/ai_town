"""src/auth/api_keys.py 单元测试

覆盖 APIKey 的生成、验证、撤销。
使用全新 APIKeyManager 实例避免状态污染。
"""

import pytest

from src.auth.api_keys import APIKeyManager


@pytest.fixture
def manager() -> APIKeyManager:
    return APIKeyManager()


# ---------------------------------------------------------------------------
# generate_key
# ---------------------------------------------------------------------------


def test_generate_key_has_sk_prefix(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    assert key.startswith("sk-")


def test_generate_key_returns_non_empty(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    assert len(key) > len("sk-")


def test_generate_key_unique_each_call(manager: APIKeyManager) -> None:
    keys = {manager.generate_key("user1") for _ in range(5)}
    assert len(keys) == 5


def test_generate_key_stores_user_id_and_scopes(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1", scopes=["read", "write"])
    info = manager.validate_key(key)
    assert info is not None
    assert info["user_id"] == "user1"
    assert info["scopes"] == ["read", "write"]
    assert "created_at" in info


# ---------------------------------------------------------------------------
# validate_key
# ---------------------------------------------------------------------------


def test_validate_key_valid_returns_dict_with_user_id(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    info = manager.validate_key(key)
    assert info is not None
    assert isinstance(info, dict)
    assert info["user_id"] == "user1"


def test_validate_key_invalid_returns_none(manager: APIKeyManager) -> None:
    assert manager.validate_key("sk-nonexistent-key") is None


def test_validate_key_empty_returns_none(manager: APIKeyManager) -> None:
    assert manager.validate_key("") is None


def test_validate_key_returns_copy_not_internal_reference(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    info = manager.validate_key(key)
    assert info is not None
    # 修改返回的副本不应影响内部状态
    info["user_id"] = "tampered"
    info2 = manager.validate_key(key)
    assert info2 is not None
    assert info2["user_id"] == "user1"


def test_validate_key_revoked_returns_none(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    assert manager.revoke_key(key) is True
    assert manager.validate_key(key) is None


# ---------------------------------------------------------------------------
# revoke_key
# ---------------------------------------------------------------------------


def test_revoke_key_existing_returns_true(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    assert manager.revoke_key(key) is True


def test_revoke_key_nonexistent_returns_false(manager: APIKeyManager) -> None:
    assert manager.revoke_key("sk-does-not-exist") is False


def test_revoke_key_twice_second_returns_false(manager: APIKeyManager) -> None:
    key = manager.generate_key("user1")
    assert manager.revoke_key(key) is True
    assert manager.revoke_key(key) is False
