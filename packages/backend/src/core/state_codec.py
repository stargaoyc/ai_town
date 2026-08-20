"""Redis 角色状态编解码器（单一真相源）

`char:{id}:state` 哈希的值编码规则（P0-2 统一）：
- 标量字段（location/mood/stamina/satiety/money/phone_battery/social_energy）直接 str()
- 复合字段（inventory/current_action 等 dict/list）统一 JSON 编码

全库所有读写 `char:{id}:state` 的代码必须经由本模块，禁止自行 str()/json.dumps()。
历史问题：`str(dict)` 产生 Python repr（单引号），下游 `isinstance(x, dict)` 判断
失效，导致 inventory/current_action 双重序列化与静默清空。
"""

import json
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)

# 数值字段：Redis 读回时转回 int
_NUMERIC_KEYS = frozenset({"stamina", "satiety", "money", "phone_battery", "social_energy"})

# 复合字段：JSON 编码/解码
_COMPOSITE_KEYS = frozenset({"inventory", "current_action"})


def encode_state_value(value: Any) -> str:
    """将单个状态值编码为 Redis 哈希存储字符串。

    复合类型（dict/list）JSON 编码，标量直接 str()。
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def decode_state_value(key: str, value: bytes | str) -> Any:
    """从 Redis 哈希读回状态值并还原类型。

    数值字段转 int；复合字段 JSON 解析（失败视为历史坏数据，返回空值并告警）；
    其余字段返回字符串。
    """
    if isinstance(value, (bytes, bytearray)):
        text = value.decode("utf-8")
    else:
        text = str(value)
    if key in _NUMERIC_KEYS:
        return int(text)
    if key in _COMPOSITE_KEYS:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("state_decode_failed", key=key, raw=text[:200])
            return {} if key == "inventory" else None
    return text


def encode_state_mapping(mapping: dict[str, Any]) -> dict[str, str]:
    """编码整个状态映射，过滤 None 值（None 字段不落 Redis）。"""
    return {k: encode_state_value(v) for k, v in mapping.items() if v is not None}
