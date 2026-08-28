"""messaging/service.py - 出站回复过滤单测（审查 安全-05）

覆盖：
- UUID 泄露剥离
- 内部字段名剥离
- JSON 残留剥离
- 正常文本不受影响
"""

from __future__ import annotations

from src.messaging.service import _filter_outbound_reply

_UUID = "019f4c52-eeea-731e-bf9e-27d4ef72d04a"


class TestFilterOutboundReply:
    def test_uuid_leak_replaced(self) -> None:
        out = _filter_outbound_reply(f"我的编号是 {_UUID}，记住了吗")
        assert _UUID not in out
        assert "[uuid]" in out

    def test_field_name_leak_replaced(self) -> None:
        out = _filter_outbound_reply("user_id 是 123，character_id 是 456")
        assert "[field_name]" in out
        assert "user_id" not in out

    def test_json_key_leak_replaced(self) -> None:
        out = _filter_outbound_reply('{"response": "你好", "emotion": "happy"}')
        assert '"response"' not in out
        assert "[json_key]" in out

    def test_normal_text_unchanged(self) -> None:
        text = "今天天气不错，我们去公园散步吧！"
        assert _filter_outbound_reply(text) == text

    def test_empty_text_unchanged(self) -> None:
        assert _filter_outbound_reply("") == ""
