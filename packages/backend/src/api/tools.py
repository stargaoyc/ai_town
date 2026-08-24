"""工具管理 API 路由

本地工具（进程内直接调用）管理。
工具按命名空间（namespace）分组：shop / knowledge / social / world / self_info。
启用/禁用状态持久化到 Redis hash `tools:enabled`，按工具全名存储。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from structlog import get_logger

from src.auth.rbac import require_role
from src.runtime import get_redis
from src.tools import TOOL_REGISTRY, get_enabled_tools
from src.tools.registry import TOOLS_ENABLED_KEY, invalidate_enabled_cache

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])
logger = get_logger(__name__)

# 依赖类型别名（规避 B008：不在函数默认参数中调用 Depends/Body）
AdminUser = Annotated[dict[str, Any], Depends(require_role("admin"))]
BodyDict = Annotated[dict[str, Any], Body(...)]


# 命名空间元数据（用于 /servers 端点展示）
# category: self-developed（自研业务工具） / read-only（只读查询工具）
_NAMESPACES: list[dict[str, Any]] = [
    {
        "name": "shop",
        "type": "self-developed",
        "description": "商店模拟（购买/出售/查询商品，24 件默认商品）",
    },
    {
        "name": "knowledge",
        "type": "self-developed",
        "description": "小镇设定库查询（世界规则/角色/场景/行动/记忆系统）",
    },
    {
        "name": "social",
        "type": "self-developed",
        "description": "角色社交（送礼/约会/冲突解决）",
    },
    {
        "name": "world",
        "type": "read-only",
        "description": "世界状态查询（虚拟时间/天气/场景/角色查找）",
    },
    {
        "name": "self_info",
        "type": "read-only",
        "description": "角色自省（关系查询/记忆搜索）",
    },
]


def _namespace_tools(namespace: str) -> list[str]:
    """获取命名空间下的所有工具全名"""
    return [name for name in TOOL_REGISTRY if name.startswith(f"{namespace}.")]


async def _is_namespace_enabled(namespace: str) -> bool:
    """检查命名空间是否启用（命名空间下所有工具均启用才算启用）"""
    enabled = await get_enabled_tools()
    tools = _namespace_tools(namespace)
    return all(t in enabled for t in tools) if tools else False


@router.get("/servers")
async def list_tool_servers() -> dict[str, Any]:
    """列出所有工具命名空间

    Returns:
        命名空间列表（含描述、工具清单、启用状态）
    """
    enabled = await get_enabled_tools()
    servers = []
    for ns in _NAMESPACES:
        ns_tools = _namespace_tools(ns["name"])
        ns_enabled = all(t in enabled for t in ns_tools) if ns_tools else False
        servers.append(
            {
                "name": ns["name"],
                "type": ns["type"],
                "description": ns["description"],
                "tools": ns_tools,
                "tool_count": len(ns_tools),
                "enabled": ns_enabled,
            }
        )

    return {
        "data": servers,
        "total": len(servers),
    }


@router.get("/servers/health")
async def check_tool_servers_health() -> dict[str, Any]:
    """检查所有工具命名空间的健康状态

    本地工具为进程内调用，始终在线。
    """
    results = []
    for ns in _NAMESPACES:
        results.append(
            {
                "name": ns["name"],
                "endpoint": "in-process",
                "status": "online",
                "latency_ms": 0,
                "http_status": None,
            }
        )
    return {
        "data": results,
        "total": len(results),
        "online": len(results),
        "offline": 0,
    }


@router.get("/servers/{server_name}")
async def get_tool_server_detail(server_name: str) -> dict[str, Any]:
    """获取单个命名空间详情

    Args:
        server_name: 命名空间名称（如 "shop"）
    """
    if server_name == "health":
        return await check_tool_servers_health()

    ns = next((n for n in _NAMESPACES if n["name"] == server_name), None)
    if ns is None:
        raise HTTPException(status_code=404, detail=f"Tool namespace '{server_name}' not found")

    ns_tools = _namespace_tools(server_name)
    return {
        "name": ns["name"],
        "endpoint": "in-process",
        "type": ns["type"],
        "description": ns["description"],
        "tools": [{"name": t, "server": ns["name"], "description": TOOL_REGISTRY[t]["description"]} for t in ns_tools],
        "tool_count": len(ns_tools),
    }


@router.get("/tools")
async def list_all_tools() -> dict[str, Any]:
    """列出所有已启用工具

    Returns:
        所有可用工具的扁平列表（仅含已启用工具）
    """
    enabled = await get_enabled_tools()
    tools = []
    for full_name, meta in TOOL_REGISTRY.items():
        if full_name not in enabled:
            continue
        namespace = full_name.split(".", 1)[0]
        ns_meta = next((n for n in _NAMESPACES if n["name"] == namespace), None)
        tools.append(
            {
                "name": full_name,
                "server": namespace,
                "server_type": ns_meta["type"] if ns_meta else "unknown",
                "description": meta["description"],
                "state_mutating": meta["state_mutating"],
            }
        )

    return {
        "data": tools,
        "total": len(tools),
    }


@router.put("/servers/{server_name}/enabled")
async def toggle_tool_server(
    server_name: str,
    payload: BodyDict,
    user: AdminUser,
) -> dict[str, Any]:
    """启用/禁用整个命名空间的所有工具

    状态持久化到 Redis hash `tools:enabled`，Character Tick 决策时
    会读取该状态过滤可用工具列表。

    Args:
        server_name: 命名空间名称
        payload: {"enabled": true|false}

    Returns:
        更新后的启用状态
    """
    ns = next((n for n in _NAMESPACES if n["name"] == server_name), None)
    if ns is None:
        raise HTTPException(status_code=404, detail=f"Tool namespace '{server_name}' not found")

    enabled = bool(payload.get("enabled", True))
    redis = get_redis()
    if redis is None:
        raise HTTPException(500, "Redis not available")

    ns_tools = _namespace_tools(server_name)
    mapping = {t: "true" if enabled else "false" for t in ns_tools}
    if mapping:
        await redis.hset(TOOLS_ENABLED_KEY, mapping=mapping)  # type: ignore
    # 立即失效进程内缓存，避免决策链路最长 5s 内仍读到旧开关状态（审查二轮 N8）
    invalidate_enabled_cache()

    logger.info(
        "tool_namespace_toggled",
        namespace=server_name,
        enabled=enabled,
        tool_count=len(ns_tools),
    )

    return {
        "success": True,
        "server": server_name,
        "enabled": enabled,
    }


@router.post("/tools/{tool_name}/invoke")
async def invoke_tool(
    tool_name: str,
    user: AdminUser,
    server_name: str | None = None,
    args: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """调用本地工具（测试用）

    Args:
        tool_name: 工具全名（如 "shop.buy_item"）或简短名（如 "buy_item"）
        server_name: 可选命名空间（用于消歧）
        args: 工具参数（JSON body）

    Returns:
        工具执行结果
    """
    # 解析工具全名
    full_name = tool_name
    if "." not in tool_name and server_name:
        full_name = f"{server_name}.{tool_name}"

    if full_name not in TOOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Tool '{full_name}' not found")

    meta = TOOL_REGISTRY[full_name]

    # 测试调用：只传 LLM 可填参数，不注入角色状态
    # 状态变更类工具会因缺少 current_money 等参数而返回错误，这是预期行为
    try:
        result = await meta["func"](**(args or {}))
        return {
            "success": True,
            "tool": full_name,
            "result": result,
            "state_mutating": meta["state_mutating"],
        }
    except TypeError as e:
        return {
            "success": False,
            "tool": full_name,
            "error": f"参数错误: {e}",
            "hint": "状态变更类工具需要 current_money/current_inventory 等参数，"
            "请在角色 Tick 中通过 LLM 决策调用，或补全必需参数。",
        }
    except Exception as e:
        return {
            "success": False,
            "tool": full_name,
            "error": str(e),
        }
