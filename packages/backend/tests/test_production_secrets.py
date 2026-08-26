"""P0-1 回归测试：生产环境默认凭据 fail-fast

锁定 check_default_secrets 的行为契约：
- 任一默认凭据（ADMIN_PASSWORD / JWT_SECRET / API_KEY）在 production 下抛 RuntimeError
- development 下仅告警不抛错
- 全部已修改时静默通过

R5-H5 扩展：check_onebot_access_token 同一契约——production 下未配
ONEBOT_ACCESS_TOKEN 抛 RuntimeError，development 仅首次告警。
"""

import pytest
from structlog.testing import capture_logs

import src.security.startup_checks as startup_checks_module
from src.config import settings
from src.security.startup_checks import _INSECURE_DEFAULTS, check_default_secrets, check_onebot_access_token


def _patch_all_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    for field, _default, _label in _INSECURE_DEFAULTS:
        monkeypatch.setattr(settings, field, f"secure-{field}", raising=False)


async def test_production_rejects_each_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production", raising=False)

    for field, default, label in _INSECURE_DEFAULTS:
        _patch_all_secure(monkeypatch)
        monkeypatch.setattr(settings, field, default, raising=False)
        with pytest.raises(RuntimeError, match=label):
            check_default_secrets()


async def test_development_warns_but_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "development", raising=False)
    _patch_all_secure(monkeypatch)
    monkeypatch.setattr(settings, "jwt_secret", "your-super-secret-key-change-in-production", raising=False)

    with capture_logs() as logs:
        check_default_secrets()

    warnings = [e for e in logs if e.get("log_level") == "warning" and "JWT_SECRET" in str(e.get("message", ""))]
    assert len(warnings) == 1


async def test_all_secure_passes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production", raising=False)
    _patch_all_secure(monkeypatch)

    with capture_logs() as logs:
        check_default_secrets()

    insecure = [e for e in logs if "insecure" in str(e.get("event", ""))]
    assert not insecure


# === R5-H5：OneBot 反向 WS access token ===


async def test_onebot_token_missing_rejects_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production", raising=False)
    monkeypatch.setattr(settings, "onebot_access_token", None, raising=False)

    with pytest.raises(RuntimeError, match="ONEBOT_ACCESS_TOKEN"):
        check_onebot_access_token()


async def test_onebot_token_missing_warns_once_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "development", raising=False)
    monkeypatch.setattr(settings, "onebot_access_token", None, raising=False)
    monkeypatch.setattr(startup_checks_module, "_ONEBOT_TOKEN_WARNED", False)

    with capture_logs() as first:
        check_onebot_access_token()
    with capture_logs() as second:
        check_onebot_access_token()

    assert len([e for e in first if e.get("event") == "onebot_access_token_missing"]) == 1
    assert not [e for e in second if e.get("event") == "onebot_access_token_missing"]


async def test_onebot_token_configured_passes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "onebot_access_token", "secret-token", raising=False)

    with capture_logs() as logs:
        check_onebot_access_token()

    assert not [e for e in logs if "onebot_access_token" in str(e.get("event", ""))]
