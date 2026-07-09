"""MCP Web Search Server - Tavily API 集成

为 AI Town 角色提供"网络搜索 → 获取实时信息"的工具能力。
角色可调用此 MCP Server 搜索网页内容或最新新闻。

设计：
- 集成 Tavily Search API（https://api.tavily.com/search）
- 通过环境变量 TAVILY_API_KEY 配置 API Key
- 使用 httpx.AsyncClient 异步请求
- API Key 缺失时优雅降级（返回错误字典，不崩溃）

注意：本包为社区插件骨架，需配置有效 API Key 才能完整运行。

启动方式：
    export TAVILY_API_KEY=tvly-xxxxxxxxxxxx
    uv run python server.py
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP  # FastMCP 2.0+ 导入方式

logger = structlog.get_logger()

mcp = FastMCP("web-search")

TAVILY_API_URL = "https://api.tavily.com/search"
HTTP_TIMEOUT = 30.0
MAX_RESULTS_LIMIT = 10


def _get_api_key() -> str | None:
    """从环境变量读取 Tavily API Key"""
    return os.environ.get("TAVILY_API_KEY")


def _missing_key_error(action: str) -> dict[str, Any]:
    """构造 API Key 缺失错误返回"""
    return {
        "success": False,
        "error": "API key not configured",
        "message": "Set TAVILY_API_KEY environment variable to use web search",
        "action": action,
        "results": [],
        "total": 0,
    }


def _parse_tavily_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    """解析 Tavily 响应中的 results 数组为统一结构"""
    raw_results = data.get("results", []) or []
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score", 0.0),
        }
        for r in raw_results
    ]


@mcp.tool()
async def search(query: str, max_results: int = 5) -> dict:
    """网络搜索（通用）

    使用 Tavily API（topic=general）搜索与查询相关的网页内容。

    Args:
        query: 搜索关键词
        max_results: 返回结果数量上限（1-10，默认 5）

    Returns:
        {
            "success": bool,
            "results": [{"title", "url", "content", "score"}],
            "total": int,
            "error": str | None,
        }
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("search_skipped_no_api_key", query=query)
        return _missing_key_error("search")

    actual_max = max(1, min(max_results, MAX_RESULTS_LIMIT))
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": actual_max,
        "topic": "general",
    }

    logger.info("search_requested", query=query, max_results=actual_max)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(TAVILY_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "search_http_error",
            status=e.response.status_code,
            error=str(e),
        )
        return {
            "success": False,
            "results": [],
            "total": 0,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except httpx.HTTPError as e:
        logger.error("search_request_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "results": [],
            "total": 0,
            "error": f"Request failed: {e}",
        }
    except Exception as e:
        logger.error("search_unexpected_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "results": [],
            "total": 0,
            "error": f"Unexpected error: {e}",
        }

    results = _parse_tavily_results(data)
    logger.info("search_done", query=query, returned=len(results))

    return {
        "success": True,
        "results": results,
        "total": len(results),
        "error": None,
    }


@mcp.tool()
async def search_news(query: str, max_results: int = 5) -> dict:
    """新闻搜索

    使用 Tavily API（topic=news）搜索最新新闻资讯。

    Args:
        query: 搜索关键词
        max_results: 返回结果数量上限（1-10，默认 5）

    Returns:
        {
            "success": bool,
            "results": [{"title", "url", "content", "score"}],
            "total": int,
            "error": str | None,
        }
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("search_news_skipped_no_api_key", query=query)
        return _missing_key_error("search_news")

    actual_max = max(1, min(max_results, MAX_RESULTS_LIMIT))
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": actual_max,
        "topic": "news",
    }

    logger.info("search_news_requested", query=query, max_results=actual_max)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(TAVILY_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "search_news_http_error",
            status=e.response.status_code,
            error=str(e),
        )
        return {
            "success": False,
            "results": [],
            "total": 0,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except httpx.HTTPError as e:
        logger.error("search_news_request_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "results": [],
            "total": 0,
            "error": f"Request failed: {e}",
        }
    except Exception as e:
        logger.error("search_news_unexpected_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "results": [],
            "total": 0,
            "error": f"Unexpected error: {e}",
        }

    results = _parse_tavily_results(data)
    logger.info("search_news_done", query=query, returned=len(results))

    return {
        "success": True,
        "results": results,
        "total": len(results),
        "error": None,
    }


if __name__ == "__main__":
    mcp.run()
