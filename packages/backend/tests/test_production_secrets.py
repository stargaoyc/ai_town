"""P0-1 回归测试：生产环境默认凭据 fail-fast

锁定 check_default_secrets 的行为契约：
- 任一默认凭据（ADMIN_PASSWORD / JWT_SECRET / API_KEY）在 production 下抛 RuntimeError
- development 下仅告警不抛错
- 全部已修改时静默通过
"""

import pytest
from structlog.testing import capture_logs

from src.config import settings
from src.security.startup_checks import _INSECURE_DEFAULTS, check_default_secrets


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
