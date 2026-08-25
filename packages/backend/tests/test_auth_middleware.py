"""AuthMiddleware 公开路径边界单元测试（R4-H2）。

直接以假 ASGI 下游驱动中间件，不启动完整 FastAPI 应用——
验证 characters/memories 前缀收紧后隐私子路由必须登录，
以及 alerts webhook 精确豁免对任意方法生效。
"""

from typing import Any

from starlette.types import Receive, Scope, Send

from src.auth.middleware import AuthMiddleware

_CHARACTER_UUID = "01964000-0000-7000-8000-000000000001"


class _Downstream:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.called = True


async def _passthrough(path: str, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None) -> bool:
    downstream = _Downstream()
    scope: Scope = {"type": "http", "path": path, "method": method, "headers": headers or []}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: Any) -> None:
        pass

    await AuthMiddleware(downstream)(scope, receive, send)
    return downstream.called


async def test_public_world_prefix_passes_without_auth() -> None:
    assert await _passthrough("/api/v1/world/state") is True


async def test_memories_root_requires_auth() -> None:
    assert await _passthrough(f"/api/v1/memories/{_CHARACTER_UUID}") is False


async def test_person_memory_subroute_requires_auth() -> None:
    path = f"/api/v1/characters/{_CHARACTER_UUID}/person-memory"
    assert await _passthrough(path) is False


async def test_person_memory_list_requires_auth() -> None:
    path = f"/api/v1/characters/{_CHARACTER_UUID}/person-memory/list"
    assert await _passthrough(path) is False


async def test_character_detail_requires_auth() -> None:
    assert await _passthrough(f"/api/v1/characters/{_CHARACTER_UUID}") is False


async def test_diaries_subroute_requires_auth() -> None:
    path = f"/api/v1/characters/{_CHARACTER_UUID}/diaries"
    assert await _passthrough(path) is False


async def test_alerts_webhook_exact_path_passes_for_any_method() -> None:
    assert await _passthrough("/api/v1/system/alerts/webhook", method="POST") is True


async def test_alerts_webhook_prefix_does_not_leak_exemption() -> None:
    assert await _passthrough("/api/v1/system/alerts/webhook/other") is False


async def test_invalid_bearer_still_rejected_on_private_path() -> None:
    called = await _passthrough(
        f"/api/v1/memories/{_CHARACTER_UUID}",
        headers=[(b"authorization", b"Bearer not-a-jwt")],
    )
    assert called is False
