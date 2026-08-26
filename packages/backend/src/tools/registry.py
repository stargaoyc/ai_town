"""工具注册表 - 替代 MCPClient 的直接工具调用中枢

设计：
- 将原 MCP Server 的工具收编为进程内 async 函数调用，消除 HTTP/SSE 网络开销
- 工具按命名空间组织（shop/knowledge/social/world/self_info），全名格式 `namespace.tool`
- 状态变更类工具（buy_item/give_gift 等）需要角色当前状态参数（current_money 等），
  LLM 无法提供这些参数，由 registry 从调用方传入的 context 自动注入
- 工具启用/禁用状态存储在 Redis hash `tools:enabled`（替代原 `mcp:enabled`）
- 未配置时默认全部启用

接口与原 MCPClient 保持兼容：
- format_tools_for_prompt() -> str | None（无启用工具时 None，R5-M3）
- call_tool_by_full_name(full_name, args, context) -> dict
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from structlog import get_logger

from src.config import settings
from src.observability.metrics import TOOL_CALL_TOTAL
from src.tools import knowledge, media, self_info, shop, social, world

logger = get_logger(__name__)

# Redis hash key：存储各工具的启用状态（值为 "true" / "false"）
TOOLS_ENABLED_KEY = "tools:enabled"


# 工具调用类型：(args dict, context dict) -> result dict
ToolFunc = Callable[..., Awaitable[dict[str, Any]]]


# ============================================================
# 工具注册表
# ============================================================
# 每个工具定义：
#   func: 异步函数引用
#   description: LLM Prompt 中展示的功能描述
#   llm_params: LLM 可填写的参数（名称 -> 中文说明）
#   required_params: LLM 必填参数（调用前校验，缺失即返回失败观察而非抛异常——R4-H1）
#   injected_params: 需从角色状态自动注入的参数（工具参数名 -> 状态字段名）
#   state_mutating: 是否会产生状态 deltas（money_delta/inventory_delta/relation_strength_delta 等）

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    # ---------- 商店工具（shop）----------
    "shop.list_items": {
        "func": shop.list_items,
        "description": "查看商店商品列表（可按分类过滤）",
        "llm_params": {"category": "商品分类（可选：food/drink/book/toy/medicine/clothing/other）"},
        "required_params": [],
        "injected_params": {},
        "state_mutating": False,
    },
    "shop.get_item_details": {
        "func": shop.get_item_details,
        "description": "查询单个商品详情（价格/描述/是否可售）",
        "llm_params": {"item_id": "商品 ID"},
        "required_params": ["item_id"],
        "injected_params": {},
        "state_mutating": False,
    },
    "shop.buy_item": {
        "func": shop.buy_item,
        "description": "购买商品（扣金钱、加库存）",
        "llm_params": {"item_id": "商品 ID", "quantity": "购买数量（默认 1）"},
        "required_params": ["item_id"],
        "injected_params": {"current_money": "money", "current_inventory": "inventory"},
        "state_mutating": True,
    },
    "shop.sell_item": {
        "func": shop.sell_item,
        "description": "出售商品（加金钱、减库存）",
        "llm_params": {"item_id": "商品 ID", "quantity": "出售数量（默认 1）"},
        "required_params": ["item_id"],
        "injected_params": {"current_money": "money", "current_inventory": "inventory"},
        "state_mutating": True,
    },
    "shop.get_shop_categories": {
        "func": shop.get_shop_categories,
        "description": "列出商店所有商品分类及价格区间",
        "llm_params": {},
        "required_params": [],
        "injected_params": {},
        "state_mutating": False,
    },
    # ---------- 知识库工具（knowledge）----------
    "knowledge.query_kb": {
        "func": knowledge.query_kb,
        "description": "查询小镇设定库（世界规则/角色系统/场景系统/行动系统/记忆系统）",
        "llm_params": {"query": "查询关键词（空格分隔）", "category": "可选类别过滤", "limit": "返回数量（默认 5）"},
        "required_params": ["query"],
        "injected_params": {},
        "state_mutating": False,
    },
    "knowledge.list_categories": {
        "func": knowledge.list_categories,
        "description": "列出知识库所有类别",
        "llm_params": {},
        "required_params": [],
        "injected_params": {},
        "state_mutating": False,
    },
    # ---------- 社交工具（social）----------
    "social.give_gift": {
        "func": social.give_gift,
        "description": "给其他角色送礼（消耗库存、增加好感度）",
        "llm_params": {"target_id": "目标角色 ID", "item_id": "礼物 ID"},
        "required_params": ["target_id", "item_id"],
        "injected_params": {
            "current_relation_strength": "_relation_strength_with_target",
            "current_inventory": "inventory",
        },
        "state_mutating": True,
    },
    "social.invite_date": {
        "func": social.invite_date,
        "description": "邀请其他角色约会（需关系强度 >= 40）",
        "llm_params": {"target_id": "目标角色 ID", "scene_id": "约会场景 ID"},
        "required_params": ["target_id", "scene_id"],
        "injected_params": {
            "current_relation_strength": "_relation_strength_with_target",
            "current_mood": "mood",
        },
        "state_mutating": True,
    },
    "social.resolve_conflict": {
        "func": social.resolve_conflict,
        "description": "解决与另一角色的冲突（argument/misunderstanding/betrayal）",
        "llm_params": {"target_id": "目标角色 ID", "conflict_type": "冲突类型"},
        "required_params": ["target_id", "conflict_type"],
        "injected_params": {"current_relation_strength": "_relation_strength_with_target"},
        "state_mutating": True,
    },
    # ---------- 世界查询工具（world，只读）----------
    "world.get_world_info": {
        "func": world.get_world_info,
        "description": "查询当前世界状态（虚拟时间/天气/季节/Tick ID）",
        "llm_params": {},
        "required_params": [],
        "injected_params": {},
        "state_mutating": False,
    },
    "world.find_character_by_name": {
        "func": world.find_character_by_name,
        "description": "按名字查找角色（返回 ID/性格/背景，不暴露位置）",
        "llm_params": {"query_name": "角色名"},
        "required_params": ["query_name"],
        "injected_params": {},
        "state_mutating": False,
    },
    "world.get_scene_info": {
        "func": world.get_scene_info,
        "description": "查询场景详情（开放时间/容量/可做活动/邻接出口）",
        "llm_params": {"scene_id": "场景 ID"},
        "required_params": ["scene_id"],
        "injected_params": {},
        "state_mutating": False,
    },
    "world.list_scenes": {
        "func": world.list_scenes,
        "description": "列出全部场景摘要",
        "llm_params": {},
        "required_params": [],
        "injected_params": {},
        "state_mutating": False,
    },
    # ---------- 自省工具（self_info，只读）----------
    "self_info.get_relationships": {
        "func": self_info.get_relationships,
        "description": "查询自己与所有其他角色的关系（强度/类型/备注）",
        "llm_params": {},
        "required_params": [],
        "injected_params": {"character_id": "_character_id"},
        "state_mutating": False,
    },
    "self_info.search_memories": {
        "func": self_info.search_memories,
        "description": "按关键词搜索自己的记忆片段（文本匹配，非向量检索）",
        "llm_params": {"keyword": "搜索关键词", "limit": "返回数量，默认 5"},
        "required_params": ["keyword"],
        "injected_params": {"character_id": "_character_id"},
        "state_mutating": False,
    },
    # ---------- 创意生成工具（media）----------
    "media.draw_image": {
        "func": media.draw_image,
        "description": "生成一张图片（根据画面描述创作插画，成功后可在回复中分享给用户）",
        "llm_params": {
            "prompt": "画面描述（具体、含风格与氛围）",
            "ratio": "可选画面比例 1:1/3:4/4:3/16:9 等，默认 1:1",
        },
        "required_params": ["prompt"],
        "injected_params": {},
        "state_mutating": False,
    },
    "media.generate_video": {
        "func": media.generate_video_clip,
        "description": "提交一段短视频生成任务（异步受理，完成后自动分享成片；仅在用户明确要求视频时使用）",
        "llm_params": {
            "prompt": "视频内容描述（具体、含镜头与氛围）",
            "frames": "目标帧数（默认 25，约 1 秒；越大越长越慢）",
        },
        "required_params": ["prompt"],
        "injected_params": {"character_id": "_character_id"},
        "state_mutating": False,
    },
}


def _get_redis() -> Any:
    """延迟获取全局 Redis 客户端（避免循环导入）"""
    from src.runtime import get_redis

    return get_redis()


# 工具启用状态的进程内 TTL 缓存：单次 Tick 含多轮 ReAct 决策与工具调用，
# 每次都 hgetall 会产生 5-8 次 Redis 往返；TTL 内复用本地快照，
# Dashboard 切换开关最迟 5 秒生效
_ENABLED_CACHE_TTL_SECONDS = 5.0
_enabled_cache: tuple[float, frozenset[str]] | None = None


def invalidate_enabled_cache() -> None:
    """清空工具启用状态缓存

    管理端切换开关后调用，使 get_enabled_tools 下次读取绕过 5s TTL
    立即生效（审查二轮 N8）。
    """
    global _enabled_cache
    _enabled_cache = None


async def get_enabled_tools() -> set[str]:
    """从 Redis 读取已启用的工具全名集合（带 5 秒 TTL 缓存）

    Redis hash `tools:enabled` 存储 {tool_full_name: "true"|"false"}。
    未配置（hash 为空）时默认全部启用。

    Returns:
        已启用的工具全名集合；Redis 不可用时返回全部工具名。
    """
    global _enabled_cache

    import time

    now = time.monotonic()
    if _enabled_cache is not None and (now - _enabled_cache[0]) < _ENABLED_CACHE_TTL_SECONDS:
        return set(_enabled_cache[1])

    r = _get_redis()
    all_tools = set(TOOL_REGISTRY.keys())
    if r is None:
        return all_tools
    try:
        raw = await r.hgetall(TOOLS_ENABLED_KEY)
        if not raw:
            enabled = frozenset(all_tools)
        else:
            result: set[str] = set()
            for name, enabled_flag in raw.items():
                name_str = name.decode("utf-8") if isinstance(name, (bytes, bytearray)) else str(name)
                enabled_str = (
                    enabled_flag.decode("utf-8") if isinstance(enabled_flag, (bytes, bytearray)) else str(enabled_flag)
                )
                if enabled_str.lower() in ("true", "1", "yes"):
                    result.add(name_str)
            enabled = frozenset(result)
        _enabled_cache = (now, enabled)
        return set(enabled)
    except Exception:
        logger.warning("tools_enabled_read_failed", exc_info=True)
        return all_tools


async def is_tool_enabled(tool_full_name: str) -> bool:
    """检查单个工具是否启用"""
    enabled = await get_enabled_tools()
    return tool_full_name in enabled


class ToolRegistry:
    """工具注册表 - 进程内直接调用

    替代原 MCPClient，通过 async 函数引用直接调用工具，无网络开销。
    状态变更类工具的必需参数从 context 自动注入。
    """

    async def list_tools(self) -> list[dict[str, Any]]:
        """列出所有已启用工具的元数据（静态配置，不依赖外部服务在线）"""
        enabled = await get_enabled_tools()
        tools = []
        for full_name, meta in TOOL_REGISTRY.items():
            if full_name not in enabled:
                continue
            tools.append(
                {
                    "full_name": full_name,
                    "description": meta["description"],
                    "llm_params": dict(meta["llm_params"]),
                    "state_mutating": meta["state_mutating"],
                }
            )
        return tools

    async def format_tools_for_prompt(self) -> str | None:
        """格式化工具列表供 LLM Prompt 使用（仅含已启用工具）

        Returns:
            工具列表文本；无任何启用工具时返回 None，
            调用方据此整段跳过工具说明（R5-M3）
        """
        tools = await self.list_tools()
        if not tools:
            return None
        lines = []
        for t in tools:
            params_str = ", ".join(f"{k}: {v}" for k, v in t["llm_params"].items())
            tag = " [会改变状态]" if t["state_mutating"] else ""
            lines.append(f"- {t['full_name']}({params_str}): {t['description']}{tag}")
        return "\n".join(lines)

    async def call_tool_by_full_name(
        self,
        full_name: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过全名调用工具（如 "shop.buy_item"）

        Args:
            full_name: 工具全名（namespace.tool）
            args: LLM 提供的参数
            context: 调用方上下文，包含 character_id 和 state，
                     用于注入状态变更工具所需的 current_money 等参数

        Returns:
            {"success": bool, "result": ..., "error": ...}
        """
        args = args or {}
        meta = TOOL_REGISTRY.get(full_name)
        if meta is None:
            return {"success": False, "error": f"Unknown tool: {full_name}", "result": None}

        if not await is_tool_enabled(full_name):
            return {"success": False, "error": f"Tool '{full_name}' is disabled", "result": None}

        missing = _missing_required_params(meta, args)
        if missing:
            return {"success": False, "error": f"缺少必填参数: {', '.join(missing)}", "result": None}

        # 合并 LLM 参数与注入参数
        final_args = dict(args)
        injected = self._resolve_injected_params(meta["injected_params"], context)
        final_args.update(injected)

        # 补充默认值：quantity 默认 1
        if "quantity" in meta["llm_params"] and "quantity" not in final_args:
            final_args["quantity"] = 1

        return await self._call_tool(full_name, meta, final_args)

    def _resolve_injected_params(
        self,
        injected_spec: dict[str, str],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """从 context 解析需要注入的参数

        injected_spec 的 value 可能是：
        - 普通状态字段名（如 "money"、"inventory"、"mood"）→ 从 context["state"] 取值
        - 特殊键 "_character_id" → 从 context["character_id"] 取值
        - 特殊键 "_relation_strength_with_target" → 从 context["relations"] 按 args.target_id 查找
        """
        if not injected_spec or context is None:
            return {}

        state = context.get("state", {})
        character_id = context.get("character_id")

        resolved: dict[str, Any] = {}
        for param_name, source in injected_spec.items():
            if source == "_character_id":
                if character_id is not None:
                    resolved[param_name] = str(character_id)
            elif source == "_relation_strength_with_target":
                # 关系强度需从 args.target_id 查找，由 call_tool_with_context 处理
                pass
            else:
                value = state.get(source)
                if value is not None:
                    resolved[param_name] = value
        return resolved

    async def _call_tool(
        self,
        full_name: str,
        meta: dict[str, Any],
        final_args: dict[str, Any],
    ) -> dict[str, Any]:
        """执行工具函数本体，带可配置超时，返回统一结果字典

        挂死工具（如外部 API 无响应）若任其执行会占死角色 Tick 的信号量槽位与
        分布式锁（R6-L5）；用 asyncio.wait_for 在 tool_timeout_seconds 后取消
        内部协程并返回失败观察，绝不把 TimeoutError 抛给上层——ReAct 循环据此
        走失败观察继续决策，而不是中断整轮。

        已知取舍：wait_for 超时会取消工具协程，若工具持有 DB session 等上下文
        管理器，取消路径可能跳过 __aexit__；此处可接受——工具均为本地只读或
        状态快照，不持有跨调用事务，且失败观察不会携带被取消任务的结果。
        """
        timeout = settings.tool_timeout_seconds
        try:
            if timeout and timeout > 0:
                result = await asyncio.wait_for(meta["func"](**final_args), timeout=timeout)
            else:
                result = await meta["func"](**final_args)
        except TimeoutError:
            logger.warning("tool_call_timeout", tool=full_name, timeout_seconds=timeout)
            TOOL_CALL_TOTAL.labels(tool=full_name, outcome="timeout").inc()
            return {"success": False, "error": f"tool timeout after {timeout:g}s: {full_name}", "result": None}
        except Exception as e:
            logger.warning("tool_call_failed", tool=full_name, error=str(e), exc_info=True)
            TOOL_CALL_TOTAL.labels(tool=full_name, outcome="failed").inc()
            return {"success": False, "error": str(e), "result": None}
        TOOL_CALL_TOTAL.labels(tool=full_name, outcome="success").inc()
        return {"success": True, "result": result, "error": None, "state_mutating": meta["state_mutating"]}

    async def call_tool_with_context(
        self,
        full_name: str,
        args: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """带上下文调用工具（处理 _relation_strength_with_target 特殊注入）

        context 需包含：
            - character_id: str | UUID
            - state: dict（含 money/inventory/mood 等）
            - relations: dict[str, int]（target_id -> relation_strength，可为空）

        Args 中的 target_id 用于查找 relations 中的对应关系强度。
        """
        args = args or {}
        meta = TOOL_REGISTRY.get(full_name)
        if meta is None:
            return {"success": False, "error": f"Unknown tool: {full_name}", "result": None}

        if not await is_tool_enabled(full_name):
            return {"success": False, "error": f"Tool '{full_name}' is disabled", "result": None}

        missing = _missing_required_params(meta, args)
        if missing:
            return {"success": False, "error": f"缺少必填参数: {', '.join(missing)}", "result": None}

        # 合并 LLM 参数
        final_args = dict(args)

        # 补充默认值
        if "quantity" in meta["llm_params"] and "quantity" not in final_args:
            final_args["quantity"] = 1

        # 注入参数
        state = context.get("state", {})
        character_id = context.get("character_id")
        relations: dict[str, int] = context.get("relations", {}) or {}

        for param_name, source in meta["injected_params"].items():
            if source == "_character_id":
                if character_id is not None:
                    final_args[param_name] = str(character_id)
            elif source == "_relation_strength_with_target":
                target_id = final_args.get("target_id", "")
                final_args[param_name] = relations.get(target_id, 0)
            else:
                value = state.get(source)
                if value is not None:
                    final_args[param_name] = value

        return await self._call_tool(full_name, meta, final_args)


def _missing_required_params(meta: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """校验 LLM 提供的必填参数；返回缺失名单（空列表 = 通过）"""
    missing: list[str] = []
    for name in meta.get("required_params", []):
        value = args.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    return missing


def list_all_tool_names() -> list[str]:
    """返回所有注册的工具全名（不受启用状态过滤）"""
    return list(TOOL_REGISTRY.keys())


def get_tool_metadata(full_name: str) -> dict[str, Any] | None:
    """获取单个工具的元数据"""
    meta = TOOL_REGISTRY.get(full_name)
    if meta is None:
        return None
    return {
        "full_name": full_name,
        "description": meta["description"],
        "llm_params": dict(meta["llm_params"]),
        "state_mutating": meta["state_mutating"],
    }
