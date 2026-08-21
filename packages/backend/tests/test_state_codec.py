"""state_codec 编解码回归测试（P0-2）

验证 `char:{id}:state` 哈希的统一序列化契约：
- 标量直接 str，复合类型（dict/list）JSON 编码
- 数值字段读回转 int，复合字段读回 JSON 解析
- 历史 `str(dict)` repr 数据（inventory）解析失败返回 {}，不抛异常
"""

from src.core.state_codec import decode_state_value, encode_state_mapping, encode_state_value


class TestEncodeStateValue:
    def test_scalar_int_to_str(self) -> None:
        assert encode_state_value(80) == "80"

    def test_scalar_str_passthrough(self) -> None:
        assert encode_state_value("home") == "home"

    def test_dict_json_encoded(self) -> None:
        assert encode_state_value({"item_1": 2}) == '{"item_1": 2}'

    def test_list_json_encoded(self) -> None:
        assert encode_state_value(["a", "b"]) == '["a", "b"]'

    def test_none_to_empty(self) -> None:
        assert encode_state_value(None) == ""


class TestDecodeStateValue:
    def test_numeric_key_returns_int(self) -> None:
        assert decode_state_value("stamina", "80") == 80

    def test_numeric_key_accepts_bytes(self) -> None:
        assert decode_state_value("money", b"500") == 500

    def test_composite_key_parses_json(self) -> None:
        assert decode_state_value("inventory", '{"item_1": 2}') == {"item_1": 2}

    def test_composite_key_accepts_bytes(self) -> None:
        assert decode_state_value("current_action", b'{"action_id": "wait"}') == {"action_id": "wait"}

    def test_plain_key_returns_str(self) -> None:
        assert decode_state_value("location", "cafe") == "cafe"

    def test_plain_key_accepts_bytes(self) -> None:
        assert decode_state_value("mood", b"happy") == "happy"

    def test_bad_inventory_json_returns_empty_dict(self) -> None:
        # 历史 str(dict) repr 数据：解析失败返回 {}，不抛异常
        assert decode_state_value("inventory", "{'item_1': 2}") == {}

    def test_bad_current_action_json_returns_none(self) -> None:
        assert decode_state_value("current_action", "{'action_id': 'wait'}") is None


class TestEncodeStateMapping:
    def test_filters_none_values(self) -> None:
        mapping = encode_state_mapping({"location": "home", "current_action": None})
        assert mapping == {"location": "home"}

    def test_roundtrip_preserves_types(self) -> None:
        state = {
            "location": "cafe",
            "stamina": 80,
            "satiety": 60,
            "mood": "calm",
            "money": 500,
            "phone_battery": 75,
            "social_energy": 60,
            "inventory": {"coffee": 2},
            "current_action": {"action_id": "chat_with", "params": {}},
        }
        encoded = encode_state_mapping(state)
        decoded = {k: decode_state_value(k, v) for k, v in encoded.items()}
        assert decoded == state

    def test_roundtrip_inventory_survives(self) -> None:
        # P0-2 核心回归：inventory dict 往返后仍是 dict，而非 Python repr 字符串
        encoded = encode_state_mapping({"inventory": {"coffee": 2, "book": 1}})
        assert encoded["inventory"] == '{"coffee": 2, "book": 1}'
        decoded = decode_state_value("inventory", encoded["inventory"])
        assert isinstance(decoded, dict)
        assert decoded == {"coffee": 2, "book": 1}
