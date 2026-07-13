"""MCP Server 管理 API 路由"""

import asyncio
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from structlog import get_logger

from src.auth.rbac import require_role
from src.config import settings
from src.runtime import get_redis

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])
logger = get_logger(__name__)


# MCP Server 配置映射（环境变量 → 服务器元数据）
_MCP_SERVERS_CONFIG = [
    {
        "name": "code-executor",
        "env_key": "MCP_CODE_SERVER",
        "default_port": 8001,
        "type": "self-developed",
        "tools": ["execute_python", "list_allowed_modules"],
        "description": "Python 代码沙箱执行（subprocess 隔离 + 模块白名单）",
    },
    {
        "name": "web-search",
        "env_key": "MCP_SEARCH_SERVER",
        "default_port": 8002,
        "type": "community",
        "tools": ["search", "search_news"],
        "description": "网络搜索（Tavily API 集成）",
    },
    {
        "name": "weather",
        "env_key": "MCP_WEATHER_SERVER",
        "default_port": 8003,
        "type": "community",
        "tools": ["get_current_weather", "get_forecast", "get_weather_by_coords"],
        "description": "天气查询（OpenWeatherMap 集成）",
    },
    {
        "name": "shop-simulator",
        "env_key": "MCP_SHOP_SERVER",
        "default_port": 8004,
        "type": "self-developed",
        "tools": ["list_items", "get_item_details", "buy_item", "sell_item", "get_shop_categories"],
        "description": "商店模拟（小镇经济系统，24 件默认商品）",
    },
    {
        "name": "knowledge-base",
        "env_key": "MCP_KB_SERVER",
        "default_port": 8005,
        "type": "self-developed",
        "tools": ["query_kb", "list_categories"],
        "description": "小镇设定库查询（世界规则/角色/场景/行动/记忆系统）",
    },
    {
        "name": "character-social",
        "env_key": "MCP_SOCIAL_SERVER",
        "default_port": 8006,
        "type": "self-developed",
        "tools": ["give_gift", "invite_date", "resolve_conflict"],
        "description": "角色社交系统（送礼/约会/冲突解决）",
    },
]


@router.get("/servers")
async def list_mcp_servers():
    """列出所有已配置的 MCP Server

    Returns:
        MCP Server 列表（含连接地址、工具清单、类型、启用状态）
    """
    from src.mcp import get_enabled_servers

    enabled_set = await get_enabled_servers()
    servers = []
    for cfg in _MCP_SERVERS_CONFIG:
        endpoint = getattr(settings, cfg["env_key"].lower(), None)
        if not endpoint:
            # 尝试从环境变量读取（settings 中可能未定义该字段）
            endpoint = os.environ.get(cfg["env_key"], f"http://localhost:{cfg['default_port']}")

        servers.append({
            "name": cfg["name"],
            "endpoint": endpoint,
            "type": cfg["type"],
            "description": cfg["description"],
            "tools": cfg["tools"],
            "tool_count": len(cfg["tools"]),
            "enabled": cfg["name"] in enabled_set,
        })

    return {
        "data": servers,
        "total": len(servers),
    }


@router.get("/servers/health")
async def check_mcp_servers_health():
    """检查所有 MCP Server 的健康状态（路由入口）

    注意：此路由必须在 /servers/{server_name} 之前注册，
    否则会被 {server_name} 参数捕获。
    """
    return await _check_mcp_servers_health_impl()


@router.get("/servers/{server_name}")
async def get_mcp_server_detail(server_name: str):
    """获取单个 MCP Server 详情

    Args:
        server_name: Server 名称

    Returns:
        Server 详细信息（含工具清单）
    """
    # 健康检查特殊路由（避免被 {server_name} 捕获）
    if server_name == "health":
        return await _check_mcp_servers_health_impl()
    cfg = next((c for c in _MCP_SERVERS_CONFIG if c["name"] == server_name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")

    endpoint = os.environ.get(cfg["env_key"], f"http://localhost:{cfg['default_port']}")

    return {
        "name": cfg["name"],
        "endpoint": endpoint,
        "type": cfg["type"],
        "description": cfg["description"],
        "tools": [
            {"name": tool_name, "server": cfg["name"]}
            for tool_name in cfg["tools"]
        ],
        "tool_count": len(cfg["tools"]),
    }


@router.get("/tools")
async def list_all_mcp_tools():
    """列出所有已启用 MCP Server 提供的工具

    Returns:
        所有可用工具的扁平列表（含所属 Server，仅含已启用 Server 的工具）
    """
    from src.mcp import get_enabled_servers

    enabled_set = await get_enabled_servers()
    tools = []
    for cfg in _MCP_SERVERS_CONFIG:
        if cfg["name"] not in enabled_set:
            continue
        for tool_name in cfg["tools"]:
            tools.append({
                "name": tool_name,
                "server": cfg["name"],
                "server_type": cfg["type"],
            })

    return {
        "data": tools,
        "total": len(tools),
    }


async def _check_mcp_servers_health_impl():
    """检查所有 MCP Server 的健康状态（实现）

    对每个配置的 MCP Server 发起 HTTP 连接检测，
    返回在线/离线状态及响应延迟。

    Returns:
        各 Server 的健康状态列表
    """
    import httpx

    async def check_one(cfg: dict) -> dict:
        endpoint = os.environ.get(cfg["env_key"], f"http://localhost:{cfg['default_port']}")
        start = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # 尝试连接 SSE 端点（FastMCP 默认 SSE 路径 /sse）
                resp = await client.get(f"{endpoint}/sse", follow_redirects=False)
                latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
                return {
                    "name": cfg["name"],
                    "endpoint": endpoint,
                    "status": "online",
                    "latency_ms": latency_ms,
                    "http_status": resp.status_code,
                }
        except Exception:
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            return {
                "name": cfg["name"],
                "endpoint": endpoint,
                "status": "offline",
                "latency_ms": latency_ms,
                "http_status": None,
            }

    results = await asyncio.gather(*[check_one(cfg) for cfg in _MCP_SERVERS_CONFIG])
    return {
        "data": results,
        "total": len(results),
        "online": sum(1 for r in results if r["status"] == "online"),
        "offline": sum(1 for r in results if r["status"] == "offline"),
    }


@router.put("/servers/{server_name}/enabled")
async def toggle_mcp_server(
    server_name: str,
    payload: dict = Body(...),
    user=Depends(require_role("admin")),
):
    """启用/禁用单个 MCP Server（前端控制开关）

    状态持久化到 Redis hash `mcp:enabled`，Character Tick 决策时
    会读取该状态过滤可用工具列表。

    Args:
        server_name: MCP Server 名称
        payload: {"enabled": true|false}

    Returns:
        更新后的启用状态
    """
    from src.mcp.client import MCP_ENABLED_KEY

    cfg = next((c for c in _MCP_SERVERS_CONFIG if c["name"] == server_name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")

    enabled = bool(payload.get("enabled", True))
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")

    # 写入 Redis hash（值为字符串 "true" / "false"）
    await redis.hset(MCP_ENABLED_KEY, server_name, "true" if enabled else "false")

    logger.info(
        "mcp_server_toggled",
        server=server_name,
        enabled=enabled,
    )

    return {
        "success": True,
        "server": server_name,
        "enabled": enabled,
    }


@router.post("/tools/{tool_name}/invoke")
async def invoke_mcp_tool(
    tool_name: str,
    server_name: str,
    args: dict = Body(...),
):
    """调用 MCP Server 的工具（测试用）

    Args:
        tool_name: 工具名称
        server_name: 服务器名称
        args: 工具参数（JSON body）

    Returns:
        工具执行结果
    """
    import httpx

    cfg = next((c for c in _MCP_SERVERS_CONFIG if c["name"] == server_name), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP Server '{server_name}' not found")

    if tool_name not in cfg["tools"]:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found in server '{server_name}'")

    endpoint = os.environ.get(cfg["env_key"], f"http://localhost:{cfg['default_port']}")

    try:
        # 通过 MCP SSE 协议调用工具
        # FastMCP 2.0+ SSE 端点：POST /messages/ 调用工具
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 先连接 SSE 获取 session
            sse_resp = await client.get(f"{endpoint}/sse", follow_redirects=False)
            if sse_resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"MCP Server offline or SSE endpoint not available (HTTP {sse_resp.status_code})",
                    "endpoint": endpoint,
                }

            # 调用工具
            invoke_resp = await client.post(
                f"{endpoint}/messages/",
                json={
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": args,
                    },
                },
                timeout=30.0,
            )
            return {
                "success": True,
                "status_code": invoke_resp.status_code,
                "result": invoke_resp.json() if invoke_resp.headers.get("content-type", "").startswith("application/json") else invoke_resp.text,
                "endpoint": endpoint,
            }
    except httpx.ConnectError:
        return {
            "success": False,
            "error": f"Cannot connect to MCP Server at {endpoint}. Is it running?",
            "endpoint": endpoint,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "endpoint": endpoint,
        }
