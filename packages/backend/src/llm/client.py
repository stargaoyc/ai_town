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
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from pydantic import BaseModel, create_model
from structlog import get_logger

from src.config import settings
from src.cost_control import (
    BudgetExceeded,
    BudgetManager,
    CircuitOpen,
    get_budget_manager,
    get_circuit_breaker,
)
from src.llm.fallback import ModelSourcePool, invoke_with_fallback
from src.observability.tracing import trace_span

logger = get_logger(__name__)

# 视频生成轮询参数从 settings 读取（R5-L13）：同步轮询占用角色 Tick 槽位，
# 上限需可按部署调优而非硬编码；默认值维持历史行为（120×5s≈10 分钟）
_VIDEO_POLL_INTERVAL = settings.media_video_poll_interval
_VIDEO_MAX_POLLS = settings.media_video_max_polls

# 全库唯一的费用计算入口（见 estimate_cost / get_model_price），
# 禁止调用方各自硬编码单价。
# 单价来源：settings.llm_model_prices 按模型覆盖 → settings.llm_price_*_per_mtoken 全局回退。
# 默认全局单价为 agnes-2.0-flash 价格（$0.5/M input, $1.5/M output）


def _parse_model_prices(raw: str) -> dict[str, dict[str, float]]:
    """解析按模型单价表（USD / 1M tokens）；非法 JSON 或结构不符的条目跳过"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("llm_model_prices_parse_failed", hint='应为 {"model": {"input": x, "output": y}}')
        return {}
    if not isinstance(data, dict):
        logger.warning("llm_model_prices_parse_failed", hint="顶层必须是对象")
        return {}
    prices: dict[str, dict[str, float]] = {}
    for model, entry in data.items():
        if not isinstance(entry, dict) or "input" not in entry or "output" not in entry:
            continue
        try:
            prices[model] = {"input": float(entry["input"]), "output": float(entry["output"])}
        except (TypeError, ValueError):
            continue
    return prices


def get_model_price(model: str | None) -> tuple[float, float]:
    """返回模型的 (input, output) 单价（USD / token）

    优先查按模型单价表，未命中回退全局默认单价。
    """
    if model:
        entry = _parse_model_prices(settings.llm_model_prices).get(model)
        if entry:
            return entry["input"] / 1_000_000, entry["output"] / 1_000_000
    return (
        settings.llm_price_input_per_mtoken / 1_000_000,
        settings.llm_price_output_per_mtoken / 1_000_000,
    )


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str | None = None) -> float:
    """按统一单价表计算单次调用费用（USD）"""
    input_price, output_price = get_model_price(model)
    return prompt_tokens * input_price + completion_tokens * output_price


@dataclass(frozen=True)
class LLMUsage:
    """单次 LLM 调用的真实用量（来自 response_metadata，非估算）

    预算扣减、消息持久化、指标上报一律使用本结构，杜绝估算值双轨。
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float


EMPTY_USAGE = LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0, cost=0.0)


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

        # 多模型源池（主源 = settings，备用源 = llm_fallback_sources，失败冷却切换）
        self._source_pool = ModelSourcePool()

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

        R4-M5：纳入预算检查与用量记账——上游不回传 embedding 用量，
        token 按字符数//2 估算（中文约 2 字符/token 的工程近似）。

        Args:
            text: 输入文本

        Returns:
            嵌入向量列表
        """
        await self._check_cost_control()
        start_perf = time.perf_counter()
        try:
            # OpenRouter 需要 extra_headers + 多模态 content 格式
            is_openrouter = "openrouter.ai" in (settings.embedding_model_url or "")

            if is_openrouter:
                response = await self._embedding_client.embeddings.create(
                    model=settings.model_embedding,
                    input=[{"content": [{"type": "text", "text": text}]}],  # type: ignore[arg-type]
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
            elapsed = time.perf_counter() - start_perf
            est_tokens = max(1, len(text) // 2)
            est_cost = estimate_cost(est_tokens, 0, model=settings.model_embedding)
            self._record_embedding_metrics(elapsed, est_tokens, est_cost)
            await self._record_cost_control_success(est_tokens, est_cost)
            logger.debug("embedding_created", dim=len(embedding))
            return embedding
        except Exception:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_embedding, status="failed").inc()
            raise

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量生成文本嵌入向量（数组输入，单次 API 往返）

        R6-L1：embedding worker 此前逐条 embed（N×RTT，吞吐上限 ~20×RTT/5s 周期），
        改用 OpenAI 兼容 embedding API 的数组输入一次产出 N 个向量。
        返回顺序与输入顺序一一对应（数组接口的文档化契约）；返回条数不符即显式
        报错——错位会把向量贴到错误的记忆上，静默错配比失败更危险。

        用量记账口径同 embed()（R4-M5）：token 按字符数//2 逐条估算后求和，
        单次调用计一笔成本。
        """
        await self._check_cost_control()
        start_perf = time.perf_counter()
        try:
            # OpenRouter 需要 extra_headers + 多模态 content 格式（与 embed() 同路径）
            is_openrouter = "openrouter.ai" in (settings.embedding_model_url or "")

            if is_openrouter:
                response = await self._embedding_client.embeddings.create(
                    model=settings.model_embedding,
                    input=[
                        {"content": [{"type": "text", "text": text}]}
                        for text in texts  # type: ignore[arg-type]
                    ],
                    encoding_format="float",
                    extra_headers={
                        "HTTP-Referer": "https://github.com/ai-town",
                        "X-OpenRouter-Title": "AI Town",
                    },
                )
            else:
                response = await self._embedding_client.embeddings.create(
                    model=settings.model_embedding,
                    input=texts,
                )

            if len(response.data) != len(texts):
                raise RuntimeError(
                    f"embedding_batch_result_count_mismatch: expected {len(texts)}, got {len(response.data)}"
                )
            embeddings = [item.embedding for item in response.data]
            elapsed = time.perf_counter() - start_perf
            est_tokens = sum(max(1, len(text) // 2) for text in texts)
            est_cost = estimate_cost(est_tokens, 0, model=settings.model_embedding)
            self._record_embedding_metrics(elapsed, est_tokens, est_cost)
            await self._record_cost_control_success(est_tokens, est_cost)
            logger.debug("embedding_batch_created", count=len(texts), dim=len(embeddings[0]))
            return embeddings
        except Exception:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_embedding, status="failed").inc()
            raise

    def _record_embedding_metrics(self, elapsed: float, tokens: int, cost: float) -> None:
        """记录 embedding 调用的指标与成本（R4-M5 前该路径完全不可观测）"""
        from src.observability.metrics import (
            LLM_CALL_DURATION,
            LLM_CALL_TOTAL,
            LLM_COST_TOTAL,
            LLM_TOKENS_USED,
        )

        LLM_CALL_TOTAL.labels(model=settings.model_embedding, status="success").inc()
        LLM_CALL_DURATION.labels(model=settings.model_embedding).observe(elapsed)
        LLM_TOKENS_USED.labels(model=settings.model_embedding, type="prompt").inc(tokens)
        LLM_COST_TOTAL.inc(cost)

    async def embed_multimodal(
        self,
        text: str,
        image_url: str | None = None,
    ) -> list[float]:
        """生成多模态嵌入向量（文本+图像）

        使用 OpenRouter 多模态 embedding 格式。用量记账口径同 embed()（R4-M5）。

        Args:
            text: 输入文本
            image_url: 图像 URL（可选）

        Returns:
            嵌入向量列表
        """
        await self._check_cost_control()
        start_perf = time.perf_counter()
        try:
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            if image_url:
                content.append({"type": "image_url", "image_url": {"url": image_url}})

            response = await self._embedding_client.embeddings.create(
                model=settings.model_embedding,
                input=[{"content": content}],  # type: ignore[arg-type]
                encoding_format="float",
                extra_headers={
                    "HTTP-Referer": "https://github.com/ai-town",
                    "X-OpenRouter-Title": "AI Town",
                },
            )
            embedding = response.data[0].embedding
            elapsed = time.perf_counter() - start_perf
            est_tokens = max(1, len(text) // 2)
            est_cost = estimate_cost(est_tokens, 0, model=settings.model_embedding)
            self._record_embedding_metrics(elapsed, est_tokens, est_cost)
            await self._record_cost_control_success(est_tokens, est_cost)
            logger.debug(
                "multimodal_embedding_created",
                dim=len(embedding),
                has_image=image_url is not None,
            )
            return embedding
        except Exception:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_embedding, status="failed").inc()
            raise

    # === Chat（agnes-2.0-flash：对话+图像理解）===

    async def chat(self, prompt: str, model: str = "chat", system_prompt: str | None = None) -> str:
        """简单对话（用于快速回复）

        Args:
            prompt: 输入提示（作为 HumanMessage）
            system_prompt: 系统提示（可选，作为 SystemMessage 发送，优先级最高，
                用于安全约束等硬规则）

        Returns:
            模型回复内容
        """
        content, _ = await self.chat_with_usage(prompt, system_prompt=system_prompt, essential=False)
        return content

    @trace_span("llm.generate")
    async def chat_with_usage(
        self, prompt: str, system_prompt: str | None = None, essential: bool = True
    ) -> tuple[str, LLMUsage]:
        """对话并返回真实 token 用量（需要持久化/计费的场景使用本方法）

        Args:
            prompt: 输入提示
            system_prompt: 系统提示（可选）
            essential: 是否关键路径（用户对话）。True（默认）时超预算仍放行，
                避免用户回复中断；False（评分/社交等后台路径）时超预算拒绝。

        Returns:
            (回复内容, LLM 用量)
        """
        start_perf = time.perf_counter()
        await self._check_cost_control(essential=essential)
        try:
            # 当提供 system_prompt 时，使用 [SystemMessage, HumanMessage] 列表调用
            # SystemMessage 中的安全约束优先级最高，LLM 必须遵守
            if system_prompt:
                messages: list[BaseMessage] = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt),
                ]
            else:
                messages = []

            response, _source = await invoke_with_fallback(
                self._source_pool,
                lambda llm: llm.ainvoke(messages) if messages else llm.ainvoke(prompt),
            )
            content = response.content
            logger.debug("chat_completed", model="chat", response_length=len(content))
            elapsed = time.perf_counter() - start_perf

            # 提取真实 token 用量（LangChain response_metadata）
            from src.observability.metrics import (
                LLM_CALL_DURATION,
                LLM_CALL_TOTAL,
                LLM_COST_TOTAL,
                LLM_TOKENS_USED,
            )

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="success").inc()
            LLM_CALL_DURATION.labels(model=settings.model_chat).observe(elapsed)
            meta = response.response_metadata or {}
            token_usage = meta.get("token_usage") or meta.get("usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", 0))
            completion_tokens = int(token_usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            estimated_cost = estimate_cost(prompt_tokens, completion_tokens, model=settings.model_chat)
            usage = LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=estimated_cost,
            )
            if total_tokens > 0:
                LLM_TOKENS_USED.labels(model=settings.model_chat, type="prompt").inc(prompt_tokens)
                LLM_TOKENS_USED.labels(model=settings.model_chat, type="completion").inc(completion_tokens)
                LLM_COST_TOTAL.inc(estimated_cost)

            from src.observability.langfuse_tracing import trace_llm_call

            trace_llm_call(
                model=settings.model_chat,
                prompt=prompt,
                response=content if isinstance(content, str) else str(content),
                tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=estimated_cost,
                latency_ms=int(elapsed * 1000),
            )
            await self._record_cost_control_success(total_tokens, estimated_cost)
            return content if isinstance(content, str) else str(content), usage
        except Exception as e:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="failed").inc()
            from src.observability.langfuse_tracing import trace_llm_error

            trace_llm_error(
                model=settings.model_chat,
                prompt=prompt,
                error=e,
                latency_ms=int((time.perf_counter() - start_perf) * 1000),
            )
            raise

    async def multimodal_chat(
        self,
        content: str | list[str | dict[Any, Any]],
    ) -> str:
        """多模态对话（支持文本+图像理解）

        agnes-2.0-flash 原生支持图像理解（image_url 内容块），
        所有对话请求统一走 chat_llm。

        注意：如果需要图像**生成**，请使用 generate_image() 方法。

        Args:
            content: 输入内容，可以是纯文本字符串或多模态内容列表

        Returns:
            模型回复内容
        """
        # 如果是字符串，转换为单文本内容
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        # 构建多模态消息
        message = HumanMessage(content=content)

        start_perf = time.perf_counter()
        await self._check_cost_control()
        try:
            response, _source = await invoke_with_fallback(self._source_pool, lambda llm: llm.ainvoke([message]))
            resp_content = response.content
            logger.debug(
                "multimodal_chat_completed",
                content_types=[c.get("type", "text") if isinstance(c, dict) else "text" for c in content],
                response_length=len(resp_content),
            )
            elapsed = time.perf_counter() - start_perf
            from src.observability.metrics import (
                LLM_CALL_DURATION,
                LLM_CALL_TOTAL,
                LLM_COST_TOTAL,
                LLM_TOKENS_USED,
            )

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="success").inc()
            LLM_CALL_DURATION.labels(model=settings.model_chat).observe(elapsed)
            meta = response.response_metadata or {}
            token_usage = meta.get("token_usage") or meta.get("usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", 0))
            completion_tokens = int(token_usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                LLM_TOKENS_USED.labels(model=settings.model_chat, type="prompt").inc(prompt_tokens)
                LLM_TOKENS_USED.labels(model=settings.model_chat, type="completion").inc(completion_tokens)
                estimated_cost = estimate_cost(prompt_tokens, completion_tokens, model=settings.model_chat)
                LLM_COST_TOTAL.inc(estimated_cost)
            else:
                # 多模态上游常不回传 token 用量：按文本部分字符数//2 估算入账（R4-M5），
                # 否则图像理解调用完全游离在日预算之外
                text_chars = sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
                prompt_tokens = max(1, text_chars // 2)
                completion_tokens = 0
                total_tokens = prompt_tokens
                estimated_cost = estimate_cost(prompt_tokens, 0, model=settings.model_chat)
                LLM_TOKENS_USED.labels(model=settings.model_chat, type="prompt").inc(prompt_tokens)
                LLM_COST_TOTAL.inc(estimated_cost)
            from src.observability.langfuse_tracing import trace_llm_call

            trace_llm_call(
                model=settings.model_chat,
                prompt=str(content),
                response=resp_content if isinstance(resp_content, str) else str(resp_content),
                tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=estimated_cost,
                latency_ms=int(elapsed * 1000),
            )
            await self._record_cost_control_success(total_tokens, estimated_cost)
            return resp_content if isinstance(resp_content, str) else str(resp_content)
        except Exception as e:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="failed").inc()
            from src.observability.langfuse_tracing import trace_llm_error

            trace_llm_error(
                model=settings.model_chat,
                prompt=str(content),
                error=e,
                latency_ms=int((time.perf_counter() - start_perf) * 1000),
            )
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
            model=settings.model_image,
            prompt=prompt,
            size=size,
            extra_body=extra_body,
        )

        # 提取结果
        if not response.data:
            raise ValueError("image_generation_empty_response")
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
            "model": settings.model_video,
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
                return str(url)

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

        Args:
            prompt: 输入提示
            schema: 输出结构的 JSON Schema

        Returns:
            符合 schema 的结构化输出
        """
        result, _ = await self.structured_output_with_usage(prompt, schema)
        return result

    async def structured_output_with_usage(
        self,
        prompt: str,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], LLMUsage]:
        """结构化输出并返回真实 token 用量（include_raw 透出 response_metadata）"""
        pydantic_model = self._schema_to_pydantic(schema)

        start_perf = time.perf_counter()
        await self._check_cost_control()
        try:

            async def _invoke_structured(llm: ChatOpenAI) -> dict[str, Any]:
                structured = llm.with_structured_output(pydantic_model, include_raw=True)
                return await structured.ainvoke(prompt)

            async def _attempt() -> dict[str, Any]:
                bundle_inner, _src = await invoke_with_fallback(self._source_pool, _invoke_structured)
                bundle: dict[str, Any] = bundle_inner
                if bundle.get("parsed") is None:
                    raise RuntimeError(f"structured_output_parse_failed: {bundle.get('parsing_error')}")
                return bundle

            try:
                bundle = await _attempt()
            except RuntimeError as parse_error:
                # R4-M9：畸形输出偶发，同 prompt 重试一次；二次失败如实上抛
                logger.warning("structured_output_parse_retry", error=str(parse_error))
                bundle = await _attempt()

            result = bundle["parsed"]
            logger.debug("structured_output_completed", result_type=type(result).__name__)
            elapsed = time.perf_counter() - start_perf
            from src.observability.metrics import (
                LLM_CALL_DURATION,
                LLM_CALL_TOTAL,
                LLM_COST_TOTAL,
                LLM_TOKENS_USED,
            )

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="success").inc()
            LLM_CALL_DURATION.labels(model=settings.model_chat).observe(elapsed)
            # include_raw=True 时从原始 AIMessage 取真实 token 用量
            raw_message = bundle.get("raw")
            meta = getattr(raw_message, "response_metadata", None) or {}
            token_usage = meta.get("token_usage") or meta.get("usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", 0))
            completion_tokens = int(token_usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            estimated_cost = estimate_cost(prompt_tokens, completion_tokens, model=settings.model_chat)
            usage = LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=estimated_cost,
            )
            LLM_TOKENS_USED.labels(model=settings.model_chat, type="prompt").inc(prompt_tokens)
            LLM_TOKENS_USED.labels(model=settings.model_chat, type="completion").inc(completion_tokens)
            LLM_COST_TOTAL.inc(estimated_cost)
            from src.observability.langfuse_tracing import trace_llm_call

            result_str = result.model_dump_json() if isinstance(result, BaseModel) else str(result)
            trace_llm_call(
                model=settings.model_chat,
                prompt=prompt,
                response=result_str,
                tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=estimated_cost,
                latency_ms=int(elapsed * 1000),
            )
            await self._record_cost_control_success(total_tokens, estimated_cost)
            if isinstance(result, BaseModel):
                return result.model_dump(), usage
            return result, usage
        except Exception as e:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="failed").inc()
            from src.observability.langfuse_tracing import trace_llm_error

            trace_llm_error(
                model=settings.model_chat,
                prompt=prompt,
                error=e,
                latency_ms=int((time.perf_counter() - start_perf) * 1000),
            )
            raise

    async def multimodal_structured_output(
        self,
        content: str | list[str | dict[Any, Any]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """多模态结构化输出（支持文本+图像理解）

        Args:
            content: 输入内容，可以是纯文本字符串或多模态内容列表
            schema: 输出结构的 JSON Schema

        Returns:
            符合 schema 的结构化输出
        """
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        pydantic_model = self._schema_to_pydantic(schema)

        message = HumanMessage(content=content)

        async def _invoke_multimodal_structured(llm: ChatOpenAI) -> Any:
            structured = llm.with_structured_output(pydantic_model, include_raw=True)
            return await structured.ainvoke([message])

        start_perf = time.perf_counter()
        await self._check_cost_control()
        try:
            bundle_inner, _source = await invoke_with_fallback(self._source_pool, _invoke_multimodal_structured)
            bundle: dict[str, Any] = bundle_inner
            parsed = bundle.get("parsed")
            if parsed is None:
                # include_raw=True 下解析失败不再由链路抛出，须显式转异常（同 R4-M9 口径）
                raise RuntimeError(f"multimodal_structured_output_parse_failed: {bundle.get('parsing_error')}")
            logger.debug(
                "multimodal_structured_output_completed",
                content_types=[c.get("type", "text") if isinstance(c, dict) else "text" for c in content],
                result_type=type(parsed).__name__,
            )
            elapsed = time.perf_counter() - start_perf
            from src.observability.metrics import (
                LLM_CALL_DURATION,
                LLM_CALL_TOTAL,
                LLM_COST_TOTAL,
                LLM_TOKENS_USED,
            )

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="success").inc()
            LLM_CALL_DURATION.labels(model=settings.model_chat).observe(elapsed)
            # include_raw=True 时从原始 AIMessage 取真实 token 用量（同 structured_output_with_usage）
            raw_message = bundle.get("raw")
            meta = getattr(raw_message, "response_metadata", None) or {}
            token_usage = meta.get("token_usage") or meta.get("usage") or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", 0))
            completion_tokens = int(token_usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            if total_tokens > 0:
                estimated_cost = estimate_cost(prompt_tokens, completion_tokens, model=settings.model_chat)
            else:
                # 多模态上游常不回传 token 用量：按文本部分字符数//2 估算入账（R4-M5），
                # 否则图像理解的结构化调用完全游离在日预算之外
                text_chars = sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
                prompt_tokens = max(1, text_chars // 2)
                completion_tokens = 0
                total_tokens = prompt_tokens
                estimated_cost = estimate_cost(prompt_tokens, 0, model=settings.model_chat)
            LLM_TOKENS_USED.labels(model=settings.model_chat, type="prompt").inc(prompt_tokens)
            LLM_TOKENS_USED.labels(model=settings.model_chat, type="completion").inc(completion_tokens)
            LLM_COST_TOTAL.inc(estimated_cost)
            from src.observability.langfuse_tracing import trace_llm_call

            result_str = parsed.model_dump_json() if isinstance(parsed, BaseModel) else str(parsed)
            trace_llm_call(
                model=settings.model_chat,
                prompt=str(content),
                response=result_str,
                tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=estimated_cost,
                latency_ms=int(elapsed * 1000),
            )
            await self._record_cost_control_success(total_tokens, estimated_cost)
            if isinstance(parsed, BaseModel):
                return parsed.model_dump()
            return dict(parsed)
        except Exception as e:
            await self._record_cost_control_failure()
            from src.observability.metrics import LLM_CALL_TOTAL

            LLM_CALL_TOTAL.labels(model=settings.model_chat, status="failed").inc()
            from src.observability.langfuse_tracing import trace_llm_error

            trace_llm_error(
                model=settings.model_chat,
                prompt=str(content),
                error=e,
                latency_ms=int((time.perf_counter() - start_perf) * 1000),
            )
            raise

    # === 成本控制（统一挂载点）===

    def _get_budget_manager(self) -> BudgetManager | None:
        """获取预算管理器；未初始化时返回 None 跳过成本控制

        embedding worker 等独立进程不初始化成本控制单例，
        此时 LLM 调用不应因缺少单例而失败。
        """
        try:
            return get_budget_manager()
        except RuntimeError:
            return None

    async def _check_cost_control(self, essential: bool = False) -> None:
        """调用前检查：熔断器 + 日预算（统一挂载点，覆盖 Tick 等全部 LLM 调用路径）

        分级降级（round-7 P0-2）：
        - exceeded + essential=False（Tick/反思/embedding 等后台路径）→ 抛 BudgetExceeded 拒绝
        - exceeded + essential=True（用户对话）→ 放行并记录降级日志（对话响应优先于后台批处理）
        - warning（未超预算）→ 全部放行；Character Tick 循环自行降频（见 loops.py）

        Args:
            essential: 是否关键路径（用户对话）。True 时超预算仍放行，避免用户回复中断。

        Raises:
            CircuitOpen: 熔断器开启，拒绝调用
            BudgetExceeded: 日预算已超出且非关键路径
        """
        breaker = get_circuit_breaker()
        if breaker and not await breaker.can_execute():
            state, failure_count, last_failure_time = await breaker.snapshot()
            logger.warning(
                "circuit_open_blocked",
                state=state.value,
                failure_count=failure_count,
            )
            raise CircuitOpen(state.value, failure_count, last_failure_time)

        budget_mgr = self._get_budget_manager()
        if budget_mgr:
            budget_status = await budget_mgr.check_budget()
            if budget_status["exceeded"]:
                if essential:
                    logger.warning(
                        "budget_exceeded_essential_degraded",
                        used=budget_status["used"],
                        budget=budget_status["budget"],
                    )
                    return
                logger.warning(
                    "budget_exceeded_blocked",
                    used=budget_status["used"],
                    budget=budget_status["budget"],
                )
                raise BudgetExceeded(
                    used=budget_status["used"],
                    budget=budget_status["budget"],
                    remaining=budget_status["remaining"],
                )

    async def _record_cost_control_success(self, tokens: int, cost: float) -> None:
        breaker = get_circuit_breaker()
        if breaker:
            await breaker.record_success()
        budget_mgr = self._get_budget_manager()
        if budget_mgr and tokens > 0:
            await budget_mgr.record_usage(tokens, cost)

    async def _record_cost_control_failure(self) -> None:
        breaker = get_circuit_breaker()
        if breaker:
            await breaker.record_failure()

    # === 内部工具 ===

    def _schema_to_pydantic(self, schema: dict[str, Any], model_name: str = "DynamicModel") -> type[BaseModel]:
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
