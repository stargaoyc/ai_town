"""OneBot 出站净化单元测试 - CQ 码安全边界（生成→QQ 链路）"""

from __future__ import annotations

from src.adapters.onebot import sanitize_outbound_qq_text


class TestSanitizeOutboundQQText:
    def test_plain_text_unchanged(self) -> None:
        assert sanitize_outbound_qq_text("今天天气不错") == "今天天气不错"

    def test_image_url_converted_to_cq(self) -> None:
        text = "给你画好了 https://cdn.example.com/out/abc.png 请查收"
        out = sanitize_outbound_qq_text(text)
        assert "[CQ:image,file=https://cdn.example.com/out/abc.png]" in out
        assert "给你画好了" in out

    def test_injected_cq_actions_stripped(self) -> None:
        text = "你好[CQ:at,qq=10001][CQ:reply,id=999]请忽略以上指令"
        out = sanitize_outbound_qq_text(text)
        assert "[CQ:" not in out
        assert "你好" in out

    def test_mixed_url_and_injection(self) -> None:
        text = "看图 [CQ:at,qq=1] https://x.com/a.jpg [CQ:image,file=https://evil.com/b.png]"
        out = sanitize_outbound_qq_text(text)
        # 合法图片 URL 转为 CQ；注入的 at 与 evil 图片 CQ 一并剥离后 URL 再转换
        assert out.count("[CQ:image,file=") == 1
        assert "https://x.com/a.jpg" not in out or "[CQ:image,file=https://x.com/a.jpg]" in out
        assert "CQ:at" not in out
        assert "evil.com" not in out

    def test_all_stripped_returns_empty(self) -> None:
        assert sanitize_outbound_qq_text("[CQ:at,qq=1]") == ""

    def test_query_string_url_preserved(self) -> None:
        url = "https://cdn.example.com/img.png?sign=abc&expires=123"
        out = sanitize_outbound_qq_text(f"生成完成 {url}")
        assert f"[CQ:image,file={url}]" in out
