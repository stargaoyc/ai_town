"""LLM 模块 - OpenAI + LangChain 统一接口（支持三模态）

提供三个模型配置，分别对应三个模态：
- chat: 文本模型（agnes-2.0-flash）
- strong: 图像模型（agnes-image-2.1-flash）
- flash: 视频模型（agnes-video-v2.0）

多模态输入格式：
- 文本: 字符串或 {"type": "text", "text": "..."}
- 图像: {"type": "image_url", "image_url": {"url": "https://..."}}
- 视频: {"type": "video_url", "video_url": {"url": "https://..."}}

方法：
- chat(prompt): 纯文本对话
- multimodal_chat(content): 多模态对话
- structured_output(prompt, schema): 纯文本结构化输出
- multimodal_structured_output(content, schema): 多模态结构化输出
"""
from src.llm.client import LLMClient
from src.llm.prompts import PromptTemplates

__all__ = ["LLMClient", "PromptTemplates"]