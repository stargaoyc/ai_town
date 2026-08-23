"""平台适配器模块 - 对接外部消息平台

模块包含：
- OneBotAdapter: QQ 机器人接入（OneBot v12 反向 WebSocket）

通过环境变量配置凭证与默认对话角色后可用：
- OneBot: ONEBOT_DEFAULT_CHARACTER_ID
"""

from src.adapters.onebot import OneBotAdapter

__all__ = [
    "OneBotAdapter",
]
