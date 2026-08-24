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
    async def test_success_returns_url_and_cq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict[str, Any]] = []

        class VideoLLM:
            async def generate_video(self, **kwargs: Any) -> str:
                calls.append(kwargs)
                return "https://cdn.example.com/out/clip.mp4"

        _patch_llm(monkeypatch, VideoLLM())

        result = await media.generate_video_clip("猫追蝴蝶", frames=30)

        assert result["success"] is True
        assert result["url"] == "https://cdn.example.com/out/clip.mp4"
        assert result["cq_code"] == "[CQ:video,file=https://cdn.example.com/out/clip.mp4]"
        # 30 对齐到 8n+1 -> 33
        assert calls[0]["num_frames"] == 33

    async def test_failure_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FailLLM:
            async def generate_video(self, **kwargs: Any) -> str:
                raise TimeoutError("video_poll_timeout")

        _patch_llm(monkeypatch, FailLLM())

        result = await media.generate_video_clip("测试", frames=25)

        assert result["success"] is False
        assert "video_poll_timeout" in result["error"]

    async def test_llm_not_initialized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, None)

        result = await media.generate_video_clip("测试", frames=25)

        assert result["success"] is False
