"""draw_image 工具单元测试 - 生成→QQ 链路源头"""

from __future__ import annotations

from typing import Any, cast

import pytest

from src.llm.client import LLMClient
from src.tools import media


class StubLLM:
    def __init__(self, url: str | None = "https://cdn.example.com/out/1.png", raise_exc: Exception | None = None):
        self._url = url
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def generate_image(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self._raise:
            raise self._raise
        return self._url or ""


def _patch_llm(monkeypatch: pytest.MonkeyPatch, stub: Any) -> None:
    monkeypatch.setattr(media, "get_llm", lambda: cast(LLMClient, stub))


class TestDrawImage:
    async def test_success_returns_url_and_cq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = StubLLM("https://cdn.example.com/out/1.png")
        _patch_llm(monkeypatch, stub)

        result = await media.draw_image("一只在樱花树下的猫", ratio="16:9")

        assert result["success"] is True
        assert result["url"] == "https://cdn.example.com/out/1.png"
        assert result["cq_code"] == "[CQ:image,file=https://cdn.example.com/out/1.png]"
        assert stub.calls[0]["ratio"] == "16:9"

    async def test_invalid_ratio_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = StubLLM("https://cdn.example.com/x.png")
        _patch_llm(monkeypatch, stub)

        await media.draw_image("测试", ratio="7:3")

        assert stub.calls[0]["ratio"] == "1:1"

    async def test_llm_failure_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, StubLLM(raise_exc=RuntimeError("provider down")))

        result = await media.draw_image("测试")

        assert result["success"] is False
        assert "provider down" in result["error"]

    async def test_llm_not_initialized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, None)

        result = await media.draw_image("测试")

        assert result["success"] is False

    async def test_data_uri_result_has_no_cq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Base64 Data URI 不能作为 CQ 图片 URL，cq_code 应为空"""
        stub = StubLLM("data:image/png;base64,iVBORw0KGgo=")
        _patch_llm(monkeypatch, stub)

        result = await media.draw_image("测试")

        assert result["success"] is True
        assert result["cq_code"] == ""


class TestGenerateVideoClip:
    async def test_submit_returns_pending_receipt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """P0-1：视频生成改为异步受理——立即返回回执，不再同步轮询"""

        class VideoLLM:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def generate_video(self, **kwargs: Any) -> str:
                self.calls.append(kwargs)
                return "https://cdn.example.com/out/clip.mp4"

        stub = VideoLLM()
        _patch_llm(monkeypatch, stub)

        result = await media.generate_video_clip("猫追蝴蝶", frames=30)

        assert result["success"] is True
        assert result["pending"] is True
        # 30 对齐到 8n+1 -> 33
        assert result["frames"] == 33
        # 无 character_id 时不起后台任务，LLM 不应被调用
        assert stub.calls == []

    async def test_background_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """后台半场轮询失败只记日志，不向调用方抛错"""

        class FailLLM:
            async def generate_video(self, **kwargs: Any) -> str:
                raise TimeoutError("video_poll_timeout")

        captured: dict[str, Any] = {}

        def _fake_spawn(coro: Any, name: str) -> None:
            captured["name"] = name
            coro.close()  # 失败路径无需真正执行轮询

        monkeypatch.setattr(
            "src.core.background.spawn_background",
            _fake_spawn,
        )
        _patch_llm(monkeypatch, FailLLM())

        result = await media.generate_video_clip("测试", frames=25, character_id="01964000-0000-7000-8000-000000000001")

        assert result["success"] is True
        assert result["pending"] is True
        assert "media.generate_video" in captured["name"]

    async def test_llm_not_initialized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, None)

        result = await media.generate_video_clip("测试", frames=25)

        assert result["success"] is False
