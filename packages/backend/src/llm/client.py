"""LLM 客户端 - OpenAI + LangChain 统一接口（支持三模态）

提供三个模型配置，分别对应三个模态：
- chat: 文本模型（agnes-2.0-flash）
- strong: 图像模型（agnes-image-2.1-flash）
- flash: 视频模型（agnes-video-v2.0）

多模态输入格式：
- 文本: 字符串或 {"type": "text", "text": "..."}
- 图像: {"type": "image_url", "image_url": {"url": "https://..."}}
- 视频: {"type": "video_url", "video_url": {"url": "https://..."}}
"""
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from pydantic import BaseModel, create_model
from structlog import get_logger

from src.config import settings

logger = get_logger(__name__)


class LLMClient:
    """LLM 客户端 - OpenAI SDK + LangChain"""

    def __init__(self) -> None:
        # OpenAI SDK（用于 embedding 和简单调用）
        self.openai = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

        # Embedding 专用客户端（独立 API Key + URL，如 OpenRouter）
        # 未配置时回退到主客户端
        if settings.embedding_model_key and settings.embedding_model_url:
            self._embedding_client = AsyncOpenAI(
                api_key=settings.embedding_model_key,
                base_url=settings.embedding_model_url,
            )
        else:
            self._embedding_client = self.openai

        # LangChain ChatOpenAI（用于结构化输出和 Agent）
        self.chat_llm = ChatOpenAI(
            model=settings.model_chat,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

        self.strong_llm = ChatOpenAI(
            model=settings.model_strong,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

        self.flash_llm = ChatOpenAI(
            model=settings.model_flash,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

    async def embed(self, text: str) -> list[float]:
        """生成文本嵌入向量

        优先使用 embedding 专用客户端（embedding_model_key + embedding_model_url），
        未配置时回退到主 OpenAI 客户端。

        Args:
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        response = await self._embedding_client.embeddings.create(
            model=settings.model_embedding,
            input=text,
        )
        embedding = response.data[0].embedding
        logger.debug("embedding_created", dim=len(embedding))
        return embedding

    async def chat(self, prompt: str, model: str = "chat") -> str:
        """简单对话（用于快速回复）

        Args:
            prompt: 输入提示
            model: 模型类型（chat/strong/flash）

        Returns:
            模型回复内容
        """
        llm = self._get_llm(model)
        response = await llm.ainvoke(prompt)
        content = response.content
        logger.debug("chat_completed", model=model, response_length=len(content))
        return content if isinstance(content, str) else str(content)

    async def multimodal_chat(
        self,
        content: str | list[dict[str, Any]],
        model: str = "chat"
    ) -> str:
        """多模态对话（支持文本+图像+视频）

        Args:
            content: 输入内容，可以是纯文本字符串或多模态内容列表
                     多模态格式示例：
                     [
                         {"type": "text", "text": "描述这张图片"},
                         {"type": "image_url", "image_url": {"url": "https://..."}},
                         {"type": "video_url", "video_url": {"url": "https://..."}}
                     ]
            model: 模型类型（chat/strong/flash）

        Returns:
            模型回复内容
        """
        llm = self._get_llm(model)

        # 如果是字符串，转换为单文本内容
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        # 构建多模态消息
        message = HumanMessage(content=content)  # type: ignore[call-overload]
        response = await llm.ainvoke([message])

        resp_content = response.content
        logger.debug(
            "multimodal_chat_completed",
            model=model,
            content_types=[c.get("type", "text") for c in content],
            response_length=len(resp_content)
        )
        return resp_content if isinstance(resp_content, str) else str(resp_content)

    async def structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        model: str = "strong"
    ) -> dict[str, Any]:
        """结构化输出（用于 LLM 决策）

        使用 LangChain 的 with_structured_output 方法

        Args:
            prompt: 输入提示
            schema: 输出结构的 JSON Schema
            model: 模型类型（chat/strong）

        Returns:
            符合 schema 的结构化输出
        """
        llm = self._get_llm(model)

        # 将 dict schema 转换为 Pydantic 模型
        pydantic_model = self._schema_to_pydantic(schema)

        # 使用 with_structured_output 进行结构化输出
        structured_llm = llm.with_structured_output(pydantic_model)
        result = await structured_llm.ainvoke(prompt)

        logger.debug("structured_output_completed", model=model, result_type=type(result).__name__)

        # 将 Pydantic 模型转换为字典
        if isinstance(result, BaseModel):
            return result.model_dump()
        return result

    async def multimodal_structured_output(
        self,
        content: str | list[dict[str, Any]],
        schema: dict[str, Any],
        model: str = "strong"
    ) -> dict[str, Any]:
        """多模态结构化输出（支持文本+图像+视频）

        Args:
            content: 输入内容，可以是纯文本字符串或多模态内容列表
            schema: 输出结构的 JSON Schema
            model: 模型类型（chat/strong）

        Returns:
            符合 schema 的结构化输出
        """
        llm = self._get_llm(model)

        # 将 dict schema 转换为 Pydantic 模型
        pydantic_model = self._schema_to_pydantic(schema)

        # 使用 with_structured_output 进行结构化输出
        structured_llm = llm.with_structured_output(pydantic_model)

        # 如果是字符串，转换为单文本内容
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        # 构建多模态消息
        message = HumanMessage(content=content)  # type: ignore[call-overload]
        result = await structured_llm.ainvoke([message])

        logger.debug(
            "multimodal_structured_output_completed",
            model=model,
            content_types=[c.get("type", "text") for c in content],
            result_type=type(result).__name__
        )

        # 将 Pydantic 模型转换为字典
        if isinstance(result, BaseModel):
            return result.model_dump()
        return result

    def _get_llm(self, model: str) -> ChatOpenAI:
        """获取对应的 LLM 实例

        Args:
            model: 模型类型（chat/strong/flash）

        Returns:
            对应的 ChatOpenAI 实例
        """
        if model == "strong":
            return self.strong_llm
        elif model == "flash":
            return self.flash_llm
        else:  # 默认使用 chat
            return self.chat_llm

    def _schema_to_pydantic(
        self,
        schema: dict[str, Any],
        model_name: str = "DynamicModel"
    ) -> type[BaseModel]:
        """将 JSON Schema 转换为 Pydantic 模型

        Args:
            schema: JSON Schema 字典
            model_name: 生成的模型名称

        Returns:
            Pydantic 模型类
        """
        # 从 schema 中提取字段定义
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # 构建字段定义
        fields: dict[str, Any] = {}
        for field_name, field_schema in properties.items():
            field_type = self._get_field_type(field_schema)
            if field_name in required:
                fields[field_name] = (field_type, ...)
            else:
                fields[field_name] = (field_type | None, None)

        # 动态创建 Pydantic 模型
        return create_model(model_name, **fields)

    def _get_field_type(self, field_schema: dict[str, Any]) -> type:
        """从 JSON Schema 字段定义中推断 Python 类型

        Args:
            field_schema: 字段的 JSON Schema

        Returns:
            对应的 Python 类型
        """
        json_type = field_schema.get("type", "string")

        type_mapping = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        return type_mapping.get(json_type, str)