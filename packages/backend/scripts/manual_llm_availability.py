"""LLM 模型可用性测试脚本

测试所有配置的大语言模型和嵌入模型的连接与响应。
- Chat（agnes-2.0-flash）: /v1/chat/completions
- Image Gen（agnes-image-2.1-flash）: /v1/images/generations
- Video Gen（agnes-video-v2.0）: /v1/videos（仅创建任务，不轮询）
- Embedding（nvidia/llama-nemotron-embed-vl-1b-v2:free）: OpenRouter 多模态格式
"""

import asyncio
import time
from datetime import datetime
from typing import Any

import httpx
from openai import AsyncOpenAI

from src.config import settings


async def test_chat_model(client: AsyncOpenAI, model: str, name: str) -> dict:
    """测试 Chat 模型可用性"""
    start = time.perf_counter()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "你好，请用一句话介绍自己。"}],
            max_tokens=50,
            timeout=15.0,
        )
        elapsed = time.perf_counter() - start
        content = response.choices[0].message.content or ""
        return {
            "name": name,
            "model": model,
            "status": "[OK]",
            "latency_ms": int(elapsed * 1000),
            "response_preview": content[:50] + "..." if len(content) > 50 else content,
            "tokens": response.usage.total_tokens if response.usage else 0,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "model": model,
            "status": "[FAILED]",
            "latency_ms": int(elapsed * 1000),
            "error": str(e)[:100],
        }


async def test_chat_image_understanding(client: AsyncOpenAI, model: str, name: str) -> dict:
    """测试 Chat 模型图像理解（agnes-2.0-flash 支持 image_url）"""
    start = time.perf_counter()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请用一句话描述这张图片。"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://live.staticflickr.com/3851/14825276609_098cac593d_b.jpg"},
                        },
                    ],
                }
            ],
            max_tokens=50,
            timeout=30.0,
        )
        elapsed = time.perf_counter() - start
        content = response.choices[0].message.content or ""
        return {
            "name": name,
            "model": model,
            "status": "[OK]",
            "latency_ms": int(elapsed * 1000),
            "response_preview": content[:50] + "..." if len(content) > 50 else content,
            "tokens": response.usage.total_tokens if response.usage else 0,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "model": model,
            "status": "[FAILED]",
            "latency_ms": int(elapsed * 1000),
            "error": str(e)[:100],
        }


async def test_image_generation(client: AsyncOpenAI, model: str, name: str) -> dict:
    """测试图像生成（agnes-image-2.1-flash）: POST /v1/images/generations"""
    start = time.perf_counter()
    try:
        response = await client.images.generate(
            model=model,
            prompt="A cute cat sitting on a windowsill, anime style",
            size="1K",
            extra_body={},
            timeout=30.0,
        )
        elapsed = time.perf_counter() - start
        image_url = response.data[0].url if response.data else None
        return {
            "name": name,
            "model": model,
            "status": "[OK]",
            "latency_ms": int(elapsed * 1000),
            "response_preview": f"URL: {image_url[:40]}..." if image_url else "No URL",
            "tokens": 0,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "model": model,
            "status": "[FAILED]",
            "latency_ms": int(elapsed * 1000),
            "error": str(e)[:100],
        }


async def test_video_generation(api_key: str, base_url: str, model: str, name: str) -> dict:
    """测试视频生成（agnes-video-v2.0）: POST /v1/videos（仅创建任务）"""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url}/videos",
                json={
                    "model": model,
                    "prompt": "A cat walking on the beach at sunset",
                    "height": 768,
                    "width": 1152,
                    "num_frames": 81,
                    "frame_rate": 24,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        elapsed = time.perf_counter() - start
        video_id = data.get("video_id", data.get("id", "unknown"))
        status = data.get("status", "unknown")
        return {
            "name": name,
            "model": model,
            "status": "[OK]",
            "latency_ms": int(elapsed * 1000),
            "response_preview": f"video_id={video_id}, status={status}",
            "tokens": 0,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "model": model,
            "status": "[FAILED]",
            "latency_ms": int(elapsed * 1000),
            "error": str(e)[:100],
        }


async def test_embedding_text(client: AsyncOpenAI, model: str, name: str) -> dict:
    """测试文本 Embedding（OpenRouter 多模态格式）"""
    start = time.perf_counter()
    try:
        is_openrouter = "openrouter.ai" in (settings.embedding_model_url or "")
        kwargs: dict[str, Any] = {
            "model": model,
            "timeout": 15.0,
        }

        if is_openrouter:
            kwargs["input"] = [{"content": [{"type": "text", "text": "测试文本"}]}]
            kwargs["encoding_format"] = "float"
            kwargs["extra_headers"] = {
                "HTTP-Referer": "https://github.com/ai-town",
                "X-OpenRouter-Title": "AI Town",
            }
        else:
            kwargs["input"] = "测试文本"

        response = await client.embeddings.create(**kwargs)
        elapsed = time.perf_counter() - start
        embedding = response.data[0].embedding
        return {
            "name": name,
            "model": model,
            "status": "[OK]",
            "latency_ms": int(elapsed * 1000),
            "embedding_dim": len(embedding),
            "tokens": response.usage.total_tokens if response.usage else 0,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "model": model,
            "status": "[FAILED]",
            "latency_ms": int(elapsed * 1000),
            "error": str(e)[:100],
        }


async def test_embedding_multimodal(client: AsyncOpenAI, model: str, name: str) -> dict:
    """测试多模态 Embedding（文本+图像）"""
    start = time.perf_counter()
    try:
        response = await client.embeddings.create(
            model=model,
            input=[
                {
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://live.staticflickr.com/3851/14825276609_098cac593d_b.jpg"},
                        },
                    ]
                }
            ],  # type: ignore[arg-type]
            encoding_format="float",
            extra_headers={
                "HTTP-Referer": "https://github.com/ai-town",
                "X-OpenRouter-Title": "AI Town",
            },
            timeout=15.0,
        )
        elapsed = time.perf_counter() - start
        embedding = response.data[0].embedding
        return {
            "name": name,
            "model": model,
            "status": "[OK]",
            "latency_ms": int(elapsed * 1000),
            "embedding_dim": len(embedding),
            "tokens": response.usage.total_tokens if response.usage else 0,
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "name": name,
            "model": model,
            "status": "[FAILED]",
            "latency_ms": int(elapsed * 1000),
            "error": str(e)[:100],
        }


async def main():
    print("=" * 80)
    print(f"LLM 模型可用性测试 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 主 OpenAI 客户端（AgnesAI）
    main_client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )

    # Embedding 专用客户端（OpenRouter）
    if settings.embedding_model_key and settings.embedding_model_url:
        embedding_client = AsyncOpenAI(
            api_key=settings.embedding_model_key,
            base_url=settings.embedding_model_url,
        )
    else:
        embedding_client = main_client

    base_url = settings.openai_base_url.rstrip("/")

    # 测试任务列表
    tasks = [
        test_chat_model(main_client, settings.model_chat, "Chat (对话)"),
        test_chat_image_understanding(main_client, settings.model_chat, "Chat (图像理解)"),
        test_image_generation(main_client, settings.model_strong, "Image Gen (图像生成)"),
        test_video_generation(settings.openai_api_key, base_url, settings.model_flash, "Video Gen (视频生成)"),
        test_embedding_text(embedding_client, settings.model_embedding, "Embed-Text"),
        test_embedding_multimodal(embedding_client, settings.model_embedding, "Embed-Multi"),
    ]

    # 并发执行测试（视频生成较慢，可单独执行）
    print("\n[1/6] Chat 对话...")
    print("[2/6] Chat 图像理解...")
    print("[3/6] Image Gen 图像生成...")
    print("[4/6] Video Gen 视频生成（仅创建任务）...")
    print("[5/6] Embed-Text 文本嵌入...")
    print("[6/6] Embed-Multi 多模态嵌入...")

    results = await asyncio.gather(*tasks)

    # 打印结果表格
    print("\n" + "-" * 110)
    print(f"| {'名称':<20} | {'模型ID':<40} | {'状态':<10} | {'耗时ms':<8} | {'详情':<60} |")
    print("-" * 110)

    for r in results:
        detail = ""
        if "response_preview" in r:
            detail = f"回复: {r['response_preview']} ({r['tokens']} tokens)"
        elif "embedding_dim" in r:
            detail = f"维度: {r['embedding_dim']} ({r['tokens']} tokens)"
        elif "error" in r:
            detail = f"错误: {r['error']}"

        print(f"| {r['name']:<20} | {r['model']:<40} | {r['status']:<10} | {r['latency_ms']:<8} | {detail:<60} |")

    print("-" * 110)

    # 统计
    ok_count = sum(1 for r in results if "[OK]" in r["status"])
    fail_count = len(results) - ok_count
    print(f"\n总计: {len(results)} 个测试, 可用: {ok_count}, 失败: {fail_count}")

    # 配置信息
    print("\n" + "=" * 80)
    print("配置信息:")
    print(f"  主 API Base URL: {settings.openai_base_url}")
    print(f"  Chat 模型: {settings.model_chat} → /v1/chat/completions")
    print(f"  Image Gen 模型: {settings.model_strong} → /v1/images/generations")
    print(f"  Video Gen 模型: {settings.model_flash} → /v1/videos + GET /agnesapi")
    print(f"  Embedding API URL: {settings.embedding_model_url or settings.openai_base_url}")
    print(f"  Embedding 模型: {settings.model_embedding}")
    print(f"  Embedding 维度配置: {settings.embedding_dim}")
    print(f"  LLM 超时: {settings.llm_timeout}s, 最大重试: {settings.llm_max_retries}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
