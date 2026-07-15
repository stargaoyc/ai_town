"""MCP 客户端 - 角色调用 MCP 工具的桥梁

功能：
- 列出所有可用工具及其描述
- 通过 HTTP/SSE 调用 MCP Server 的工具
- 工具结果返回给角色 Tick 引擎，注入记忆
- 支持按 Server 粒度启用/禁用（状态存储在 Redis hash `mcp:enabled`）

使用方式：
    client = MCPClient()
    tools = await client.list_tools()
    result = await client.call_tool("weather", "get_current_weather", {"city": "东京"})
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from structlog import get_logger

logger = get_logger(__name__)

# Redis hash key：存储各 MCP Server 的启用状态（值为 "true" / "false"）
MCP_ENABLED_KEY = "mcp:enabled"


# MCP Server 配置（与 api/mcp.py 中的 _MCP_SERVERS_CONFIG 保持一致）
# 已移除 code-executor 和 web-search（外部能力，非内部业务所需）
MCP_SERVERS: list[dict[str, Any]] = [
    {
        "name": "weather",
        "env_key": "MCP_WEATHER_SERVER",
        "default_port": 8003,
        "tools": {
            "get_current_weather": {"desc": "查询当前天气", "params": {"city": "城市名"}},
            "get_forecast": {"desc": "查询天气预报", "params": {"city": "城市名", "days": 3}},
        },
    },
    {
        "name": "shop-simulator",
        "env_key": "MCP_SHOP_SERVER",
        "default_port": 8004,
        "tools": {
            "list_items": {"desc": "查看商店商品列表", "params": {"category": "商品类别（可选）"}},
            "get_item_details": {"desc": "查看商品详情", "params": {"item_id": "商品ID"}},
            "buy_item": {"desc": "购买商品", "params": {"item_id": "商品ID", "quantity": 1}},
        },
    },
    {
        "name": "knowledge-base",
        "env_key": "MCP_KB_SERVER",
        "default_port": 8005,
        "tools": {
            "query_kb": {"desc": "查询小镇设定库（世界规则、角色关系、场景信息）", "params": {"question": "查询问题"}},
        },
    },
    {
        "name": "character-social",
        "env_key": "MCP_SOCIAL_SERVER",
        "default_port": 8006,
        "tools": {
            "give_gift": {"desc": "给其他角色送礼", "params": {"target": "目标角色", "item": "礼物名称"}},
            "invite_date": {"desc": "邀请其他角色约会", "params": {"target": "目标角色", "location": "地点"}},
        },
    },
]


def _get_redis():
    """延迟获取全局 Redis 客户端（避免循环导入）"""
    from src.runtime import get_redis

    return get_redis()


async def get_enabled_servers() -> set[str]:
    """从 Redis 读取已启用的 MCP Server 名称集合

    Redis hash `mcp:enabled` 存储 {server_name: "true"|"false"}。
    未配置（hash 为空）时默认全部启用。

    Returns:
        已启用的 Server 名称集合；Redis 不可用时返回全部 Server 名。
    """
    r = _get_redis()
    if r is None:
        return {cfg["name"] for cfg in MCP_SERVERS}
    try:
        raw = await r.hgetall(MCP_ENABLED_KEY)
        if not raw:
            return {cfg["name"] for cfg in MCP_SERVERS}
        result: set[str] = set()
        for name, enabled in raw.items():
            name_str = name.decode("utf-8") if isinstance(name, (bytes, bytearray)) else str(name)
            enabled_str = enabled.decode("utf-8") if isinstance(enabled, (bytes, bytearray)) else str(enabled)
            if enabled_str.lower() in ("true", "1", "yes"):
                result.add(name_str)
        return result
    except Exception:
        logger.warning("mcp_enabled_read_failed", exc_info=True)
        return {cfg["name"] for cfg in MCP_SERVERS}


async def is_server_enabled(server_name: str) -> bool:
    """检查单个 MCP Server 是否启用"""
    enabled = await get_enabled_servers()
    return server_name in enabled


class MCPClient:
    """MCP 工具客户端

    通过 HTTP 调用 MCP Server 的工具接口。
    如果 Server 离线，返回错误信息但不中断 Tick 流程。
    支持按 Server 粒度启用/禁用（状态存储在 Redis）。
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def get_server_endpoint(self, server_name: str) -> str | None:
        """获取 MCP Server 的 HTTP 端点"""
        for cfg in MCP_SERVERS:
            if cfg["name"] == server_name:
                return os.environ.get(
                    cfg["env_key"],
                    f"http://localhost:{cfg['default_port']}",
                )
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        """列出所有已启用 Server 的可用工具（静态配置，不依赖 Server 在线）

        被 Redis 中标记为禁用的 Server 的工具不会被列出。
        """
        enabled = await get_enabled_servers()
        tools = []
        for cfg in MCP_SERVERS:
            if cfg["name"] not in enabled:
                continue
            for tool_name, tool_info in cfg["tools"].items():
                tools.append(
                    {
                        "server": cfg["name"],
                        "tool": tool_name,
                        "description": tool_info["desc"],
                        "params": tool_info["params"],
                        "full_name": f"{cfg['name']}.{tool_name}",
                    }
                )
        return tools

    async def format_tools_for_prompt(self) -> str:
        """格式化工具列表供 LLM Prompt 使用（仅包含已启用的 Server）"""
        tools = await self.list_tools()
        if not tools:
            return "（暂无可用工具，可在设置页启用 MCP 插件）"
        lines = []
        for t in tools:
            params_str = ", ".join(f"{k}: {v}" for k, v in t["params"].items())
            lines.append(f"- {t['full_name']}({params_str}): {t['description']}")
        return "\n".join(lines)

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用 MCP Server 的工具

        Args:
            server_name: 服务器名称（如 "weather"）
            tool_name: 工具名称（如 "get_current_weather"）
            args: 工具参数

        Returns:
            {"success": bool, "result": ..., "error": ...}
        """
        args = args or {}
        endpoint = self.get_server_endpoint(server_name)
        if not endpoint:
            return {"success": False, "error": f"Unknown server: {server_name}", "result": None}

        # 检查该 Server 是否已被禁用
        if not await is_server_enabled(server_name):
            return {
                "success": False,
                "error": f"MCP Server '{server_name}' is disabled",
                "result": None,
            }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # 尝试通过 MCP JSON-RPC 协议调用
                # FastMCP 2.0+ SSE 端点
                resp = await client.post(
                    f"{endpoint}/messages/",
                    json={
                        "method": "tools/call",
                        "params": {
                            "name": tool_name,
                            "arguments": args,
                        },
                    },
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        return {"success": True, "result": data, "error": None}
                    except Exception:
                        return {"success": True, "result": resp.text, "error": None}
                else:
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                        "result": None,
                    }
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"MCP Server '{server_name}' is not running at {endpoint}",
                "result": None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "result": None}

    async def call_tool_by_full_name(
        self,
        full_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过全名调用工具（如 "weather.get_current_weather"）"""
        parts = full_name.split(".", 1)
        if len(parts) != 2:
            return {"success": False, "error": f"Invalid tool name: {full_name}", "result": None}
        return await self.call_tool(parts[0], parts[1], args)
