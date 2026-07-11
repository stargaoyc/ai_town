"""LLM 客户端 - OpenAI + LangChain 统一接口（支持三模态）

提供三种模型能力：
- chat: 对话+图像理解（agnes-2.0-flash），使用 /v1/chat/completions
- image: 图像生成（agnes-image-2.1-flash），使用 /v1/images/generations
- video: 视频生成（agnes-video-v2.0），使用 /v1/videos（异步任务）

多模态输入格式（chat 模型）：
- 文本: 字符串或 {"type": "text", "text": "..."}
- 图像: {"type": "image_url", "image_url": {"url": "https://..."}}
"""
import asyncio
import time
from typing import Any

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from pydantic import BaseModel, create_model
from structlog import get_logger

from src.config import settings

logger = get_logger(__name__)

# 视频生成轮询间隔（秒）
_VIDEO_POLL_INTERVAL = 5
# 视频生成最大轮询次数
_VIDEO_MAX_POLLS = 120


class LLMClient:
    """LLM 客户端 - OpenAI SDK + LangChain"""

    def __init__(self) -> None:
        # OpenAI SDK（用于 embedding、图像生成、视频生成）
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

        # LangChain ChatOpenAI（仅用于对话+图像理解，agnes-2.0-flash）
        self.chat_llm = ChatOpenAI(
            model=settings.model_chat,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )

        # HTTP 客户端（用于视频生成轮询等非 OpenAI SDK 端点）
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    # === Embedding ===

    async def embed(self, text: str) -> list[float]:
        """生成文本嵌入向量

        使用 OpenRouter 多模态 embedding 格式（兼容纯文本）。
        若 embedding_model_key + embedding_model_url 已配置则用专用客户端，
        否则回退到主 OpenAI 客户端。

        Args:
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        # OpenRouter 需要 extra_headers + 多模态 content 格式
        is_openrouter = "openrouter.ai" in (settings.embedding_model_url or "")

        if is_openrouter:
            response = await self._embedding_client.embeddings.create(
                model=settings.model_embedding,
                input=[{"content": [{"type": "text", "text": text}]}],
                encoding_format="float",
                extra_headers={
                    "HTTP-Referer": "https://github.com/ai-town",
                    "X-OpenRouter-Title": "AI Town",
                },
            )
        else:
            response = await self._embedding_client.embeddings.create(
                model=settings.model_embedding,
                input=text,
            )

        embedding = response.data[0].embedding
        logger.debug("embedding_created", dim=len(embedding))
        return embedding

    async def embed_multimodal(
        self,
        text: str,
        image_url: str | None = None,
    ) -> list[float]:
        """生成多模态嵌入向量（文本+图像）

        使用 OpenRouter 多模态 embedding 格式。

        Args:
            text: 输入文本
            image_url: 图像 URL（可选）

        Returns:
            嵌入向量列表
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if image_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url}
            })

        response = await self._embedding_client.embeddings.create(
            model=settings.model_embedding,
            input=[{"content": content}],
            encoding_format="float",
            extra_headers={
                "HTTP-Referer": "https://github.com/ai-town",
                "X-OpenRouter-Title": "AI Town",
            },
        )
        embedding = response.data[0].embedding
        logger.debug(
            "multimodal_embedding_created",
            dim=len(embedding),
            has_image=image_url is not None,
        )
        return embedding

    # === Chat（agnes-2.0-flash：对话+图像理解）===

    async def chat(self, prompt: str, model: str = "chat") -> str:
        """简单对话（用于快速回复）

        Args:
            prompt: 输入提示
            model: 模型类型（仅 chat 有效，strong/flash 已废弃）

        Returns:
            模型回复内容
        """
        if model != "chat":
            logger.warning("chat_model_redirect_to_chat", original_model=model)

        start_perf = time.perf_counter()
        try:
            response = await self.chat_llm.ainvoke(prompt)
            content = response.content
            logger.debug("chat_completed", model="chat", response_length=len(content))
            elapsed = time.perf_counter() - start_perf

            # 提取 token 用量（LangChain response_metadata）
            from src.observability.metrics import (
                LLM_CALL_TOTAL, LLM_CALL_DURATION, LLM_TOKENS_USED, LLM_COST_TOTAL,
            )
            LLM_CALL_TOTAL.labels(model=model, status="success").inc()
            LLM_CALL_DURATION.labels(model=model).observe(elapsed)
            meta = response.response_metadata or {}
            token_usage = meta.get("token_usage") or meta.get("usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", 0))
            completion_tokens = int(token_usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                LLM_TOKENS_USED.labels(model=model, type="prompt").inc(prompt_tokens)
                LLM_TOKENS_USED.labels(model=model, type="completion").inc(completion_tokens)
                # 粗略费用估算：agnes-2.0-flash 约 $0.5/M input, $1.5/M output
                estimated_cost = (prompt_tokens * 0.0000005 + completion_tokens * 0.0000015)
                LLM_COST_TOTAL.inc(estimated_cost)

            from src.observability.langfuse_tracing import trace_llm_call
            trace_llm_call(
                model=model,
                prompt=prompt,
                response=content if isinstance(content, str) else str(content),
                tokens=total_tokens,
                latency_ms=int(elapsed * 1000),
            )
            return content if isinstance(content, str) else str(content)
        except Exception:
            from src.observability.metrics import LLM_CALL_TOTAL
            LLM_CALL_TOTAL.labels(model=model, status="failed").inc()
            raise

    async def multimodal_chat(
        self,
        content: str | list[dict[str, Any]],
        model: str | None = None,
    ) -> str:
        """多模态对话（支持文本+图像理解）

        agnes-2.0-flash 原生支持图像理解（image_url 内容块），
        所有对话请求统一走 chat_llm。

        注意：如果需要图像**生成**，请使用 generate_image() 方法。

        Args:
            content: 输入内容，可以是纯文本字符串或多模态内容列表
            model: 已废弃，始终使用 chat 模型

        Returns:
            模型回复内容
        """
        # 如果是字符串，转换为单文本内容
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        if model is not None and model != "chat":
            logger.warning("multimodal_chat_model_redirect_to_chat", original_model=model)

        # 构建多模态消息
        message = HumanMessage(content=content)  # type: ignore[call-overload]

        effective_model = model or "chat"
        start_perf = time.perf_counter()
        try:
            response = await self.chat_llm.ainvoke([message])
            resp_content = response.content
            logger.debug(
                "multimodal_chat_completed",
                content_types=[c.get("type", "text") for c in content],
                response_length=len(resp_content)
            )
            elapsed = time.perf_counter() - start_perf
            from src.observability.metrics import (
                LLM_CALL_TOTAL, LLM_CALL_DURATION, LLM_TOKENS_USED, LLM_COST_TOTAL,
            )
            LLM_CALL_TOTAL.labels(model=effective_model, status="success").inc()
            LLM_CALL_DURATION.labels(model=effective_model).observe(elapsed)
            meta = response.response_metadata or {}
            token_usage = meta.get("token_usage") or meta.get("usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", 0))
            completion_tokens = int(token_usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                LLM_TOKENS_USED.labels(model=effective_model, type="prompt").inc(prompt_tokens)
                LLM_TOKENS_USED.labels(model=effective_model, type="completion").inc(completion_tokens)
                estimated_cost = (prompt_tokens * 0.0000005 + completion_tokens * 0.0000015)
                LLM_COST_TOTAL.inc(estimated_cost)
            from src.observability.langfuse_tracing import trace_llm_call
            trace_llm_call(
                model=effective_model,
                prompt=str(content),
                response=resp_content if isinstance(resp_content, str) else str(resp_content),
                tokens=total_tokens,
                latency_ms=int(elapsed * 1000),
            )
            return resp_content if isinstance(resp_content, str) else str(resp_content)
        except Exception:
            from src.observability.metrics import LLM_CALL_TOTAL
            LLM_CALL_TOTAL.labels(model=effective_model, status="failed").inc()
            raise

    # === 图像生成（agnes-image-2.1-flash）===

    async def generate_image(
        self,
        prompt: str,
        size: str = "1K",
        ratio: str = "1:1",
        image: list[str] | None = None,
        return_base64: bool = False,
    ) -> str:
        """生成图像

        调用 agnes-image-2.1-flash 的 /v1/images/generations 端点。

        Args:
            prompt: 图像生成或图像编辑的文本指令
            size: 输出尺寸档位（1K/2K/3K/4K），默认 1K
            ratio: 宽高比（1:1/3:4/4:3/16:9/9:16/2:3/3:2/21:9），默认 1:1
            image: 图生图输入图像数组（公共 URL 或 Data URI Base64）
            return_base64: 是否返回 Base64 数据而非 URL

        Returns:
            图像 URL 或 Base64 数据
        """
        # 构建 extra_body
        extra_body: dict[str, Any] = {}
        if image:
            extra_body["image"] = image
        if return_base64:
            extra_body["return_base64"] = True

        response = await self.openai.images.generate(
            model=settings.model_strong,
            prompt=prompt,
            size=size,  # type: ignore[arg-type]
            extra_body=extra_body,
        )

        # 提取结果
        if return_base64:
            result = response.data[0].b64_json
        else:
            result = response.data[0].url

        if result is None:
            raise ValueError("image_generation_no_result")

        logger.info(
            "image_generated",
            prompt_length=len(prompt),
            size=size,
            ratio=ratio,
            has_reference_image=image is not None,
            return_base64=return_base64,
        )
        return result

    # === 视频生成（agnes-video-v2.0）===

    async def generate_video(
        self,
        prompt: str,
        image: str | None = None,
        width: int = 1152,
        height: int = 768,
        num_frames: int = 121,
        frame_rate: int = 24,
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> str:
        """生成视频（异步任务，自动轮询直到完成）

        调用 agnes-video-v2.0 的 /v1/videos 端点创建任务，
        然后轮询 GET /agnesapi?video_id=<ID> 直到视频生成完成。

        Args:
            prompt: 视频内容的文本描述
            image: 图生视频的图片 URL（可选）
            width: 视频宽度，默认 1152
            height: 视频高度，默认 768
            num_frames: 视频帧数（8n+1 规则），默认 121（约5秒）
            frame_rate: 视频帧率，默认 24
            negative_prompt: 反向提示词（可选）
            seed: 随机种子（可选）

        Returns:
            生成视频的 URL
        """
        # 构建请求体
        body: dict[str, Any] = {
            "model": settings.model_flash,
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
        }
        if image:
            body["image"] = image
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if seed is not None:
            body["seed"] = seed

        # 创建视频任务
        base_url = settings.openai_base_url.rstrip("/")
        # 移除 /v1 后缀以获取基础 API URL
        api_base = base_url.removesuffix("/v1")

        client = await self._get_http_client()

        # POST /v1/videos 创建任务
        create_resp = await client.post(
            f"{base_url}/videos",
            json=body,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
        )
        create_resp.raise_for_status()
        task_data = create_resp.json()

        video_id = task_data.get("video_id") or task_data.get("id")
        if not video_id:
            raise ValueError(f"video_task_no_id: {task_data}")

        logger.info("video_task_created", video_id=video_id, status=task_data.get("status"))

        # 轮询视频结果
        video_url = await self._poll_video_result(api_base, video_id)

        logger.info(
            "video_generated",
            video_id=video_id,
            prompt_length=len(prompt),
            has_reference_image=image is not None,
        )
        return video_url

    async def _poll_video_result(self, api_base: str, video_id: str) -> str:
        """轮询视频生成结果

        使用推荐的 GET /agnesapi?video_id=<ID> 端点轮询。

        Args:
            api_base: API 基础 URL（不含 /v1）
            video_id: 视频 ID

        Returns:
            视频文件 URL

        Raises:
            TimeoutError: 超过最大轮询次数
            RuntimeError: 视频生成失败
        """
        client = await self._get_http_client()
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

        for attempt in range(_VIDEO_MAX_POLLS):
            await asyncio.sleep(_VIDEO_POLL_INTERVAL)

            resp = await client.get(
                f"{api_base}/agnesapi",
                params={"video_id": video_id},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "")
            progress = data.get("progress", 0)

            logger.debug(
                "video_poll",
                video_id=video_id,
                status=status,
                progress=progress,
                attempt=attempt + 1,
            )

            if status == "completed":
                url = data.get("url")
                if not url:
                    raise RuntimeError(f"video_completed_no_url: {data}")
                return url

            if status == "failed":
                error = data.get("error")
                raise RuntimeError(f"video_generation_failed: {error}")

        raise TimeoutError(f"video_poll_timeout: video_id={video_id}, max_polls={_VIDEO_MAX_POLLS}")

    # === Structured Output ===

    async def structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        model: str = "chat",
    ) -> dict[str, Any]:
        """结构化输出（用于 LLM 决策）

        使用 LangChain 的 with_structured_output 方法。
        仅使用 chat 模型（文本决策）。

        Args:
            prompt: 输入提示
            schema: 输出结构的 JSON Schema
            model: 已废弃，始终使用 chat 模型

        Returns:
            符合 schema 的结构化输出
        """
        if model != "chat":
            logger.warning("structured_output_model_redirect_to_chat", original_model=model)

        pydantic_model = self._schema_to_pydantic(schema)
        structured_llm = self.chat_llm.with_structured_output(pydantic_model)

        start_perf = time.perf_counter()
        try:
            result = await structured_llm.ainvoke(prompt)
            logger.debug("structured_output_completed", result_type=type(result).__name__)
            elapsed = time.perf_counter() - start_perf
            from src.observability.metrics import (
                LLM_CALL_TOTAL, LLM_CALL_DURATION, LLM_TOKENS_USED, LLM_COST_TOTAL,
            )
            LLM_CALL_TOTAL.labels(model=model, status="success").inc()
            LLM_CALL_DURATION.labels(model=model).observe(elapsed)
            # structured_output 的 result 是 Pydantic 模型，无 response_metadata
            # 使用粗略估算：prompt 长度 / 3 ≈ prompt_tokens
            est_prompt_tokens = max(len(str(prompt)) // 3, 1)
            est_completion_tokens = max(len(str(result)) // 3, 1)
            total_tokens = est_prompt_tokens + est_completion_tokens
            LLM_TOKENS_USED.labels(model=model, type="prompt").inc(est_prompt_tokens)
            LLM_TOKENS_USED.labels(model=model, type="completion").inc(est_completion_tokens)
            estimated_cost = (est_prompt_tokens * 0.0000005 + est_completion_tokens * 0.0000015)
            LLM_COST_TOTAL.inc(estimated_cost)
            from src.observability.langfuse_tracing import trace_llm_call
            result_str = result.model_dump_json() if isinstance(result, BaseModel) else str(result)
            trace_llm_call(
                model=model,
                prompt=prompt,
                response=result_str,
                tokens=total_tokens,
                latency_ms=int(elapsed * 1000),
            )
            if isinstance(result, BaseModel):
                return result.model_dump()
            return result
        except Exception:
            from src.observability.metrics import LLM_CALL_TOTAL
            LLM_CALL_TOTAL.labels(model=model, status="failed").inc()
            raise

    async def multimodal_structured_output(
        self,
        content: str | list[dict[str, Any]],
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        """多模态结构化输出（支持文本+图像理解）

        Args:
            content: 输入内容，可以是纯文本字符串或多模态内容列表
            schema: 输出结构的 JSON Schema
            model: 已废弃，始终使用 chat 模型

        Returns:
            符合 schema 的结构化输出
        """
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        if model is not None and model != "chat":
            logger.warning("multimodal_structured_output_model_redirect_to_chat", original_model=model)

        pydantic_model = self._schema_to_pydantic(schema)
        structured_llm = self.chat_llm.with_structured_output(pydantic_model)

        message = HumanMessage(content=content)  # type: ignore[call-overload]

        result = await structured_llm.ainvoke([message])
        logger.debug(
            "multimodal_structured_output_completed",
            content_types=[c.get("type", "text") for c in content],
            result_type=type(result).__name__
        )
        if isinstance(result, BaseModel):
            return result.model_dump()
        return result

    # === 内部工具 ===

    def _schema_to_pydantic(
        self,
        schema: dict[str, Any],
        model_name: str = "DynamicModel"
    ) -> type[BaseModel]:
        """将 JSON Schema 转换为 Pydantic 模型"""
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        fields: dict[str, Any] = {}
        for field_name, field_schema in properties.items():
            field_type = self._get_field_type(field_schema)
            if field_name in required:
                fields[field_name] = (field_type, ...)
            else:
                fields[field_name] = (field_type | None, None)

        return create_model(model_name, **fields)

    def _get_field_type(self, field_schema: dict[str, Any]) -> type:
        """从 JSON Schema 字段定义中推断 Python 类型"""
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
