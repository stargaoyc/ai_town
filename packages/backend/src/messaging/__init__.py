"""消息服务模块 - 用户与角色的对话管理

模块包含：
- MessageService: 消息处理核心服务（用户消息接收、LLM 回复生成、上下文压缩）
"""
from src.messaging.service import MessageService

__all__ = [
    "MessageService",
]
