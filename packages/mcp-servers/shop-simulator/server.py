"""MCP Shop Simulator Server - 角色商店购买模拟

为 AI Town 角色提供"逛商店 → 购买/出售"的工具能力。
角色通过 LLM 决策调用此 MCP Server 完成虚拟经济交互。

设计：
- 无状态：每次调用由 caller 传入角色当前 money + inventory
- 商店目录内置（可由配置文件覆盖）
- 校验事务可行性，返回 deltas 由 caller 应用到 character_states
- 价格可配置随机浮动（模拟市场波动）

返回结构：
    {
        "success": bool,
        "action": "buy" | "sell",
        "item_id": str,
        "quantity": int,
        "unit_price": int,
        "total_price": int,
        "money_delta": int,           # 负为支出，正为收入
        "inventory_delta": dict,       # {item_id: quantity_change}
        "error": str | None,
    }

caller（Action executor）拿到结果后：
    new_money = current_money + money_delta
    new_inventory = merge(current_inventory, inventory_delta)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = structlog.get_logger()

mcp = FastMCP("shop-simulator")


# ============================================================
# 商店目录数据模型
# ============================================================

class ShopItem(BaseModel):
    """商店商品定义"""
    item_id: str = Field(description="商品唯一标识")
    name: str = Field(description="商品显示名")
    category: str = Field(description="分类：food/drink/book/toy/medicine/clothing/other")
    base_price: int = Field(ge=0, description="基准价格（金币）")
    description: str = Field(default="", description="商品描述")
    sellable: bool = Field(default=True, description="是否允许角色反向出售给商店")
    stackable: bool = Field(default=True, description="是否可堆叠")
    # 出售给商店的折价率（角色卖出价为买入价的百分比）
    sell_back_ratio: float = Field(default=0.5, ge=0.0, le=1.0)


# 默认商店目录（24 件商品覆盖主要品类）
# 生产环境可通过 YAML 配置文件覆盖
DEFAULT_CATALOG: list[ShopItem] = [
    # 食物
    ShopItem(item_id="bread", name="面包", category="food", base_price=15,
             description="新鲜出炉的面包，恢复 20 饱腹度"),
    ShopItem(item_id="rice_ball", name="饭团", category="food", base_price=20,
             description="海苔饭团，恢复 25 饱腹度"),
    ShopItem(item_id="cake", name="蛋糕", category="food", base_price=50,
             description="草莓蛋糕，恢复 40 饱腹度并提升情绪"),
    ShopItem(item_id="bento", name="便当", category="food", base_price=35,
             description="精致便当，恢复 35 饱腹度"),
    # 饮料
    ShopItem(item_id="water", name="矿泉水", category="drink", base_price=10,
             description="解渴，无特殊效果"),
    ShopItem(item_id="coffee", name="咖啡", category="drink", base_price=25,
             description="恢复 15 精力"),
    ShopItem(item_id="juice", name="果汁", category="drink", base_price=18,
             description="恢复 10 精力并小幅提升情绪"),
    ShopItem(item_id="tea", name="茶", category="drink", base_price=15,
             description="温和恢复 8 精力"),
    # 书籍
    ShopItem(item_id="novel", name="小说", category="book", base_price=45,
             description="阅读可提升情绪与社交能量"),
    ShopItem(item_id="textbook", name="教科书", category="book", base_price=80,
             description="学习用，长时间阅读恢复较多社交能量"),
    ShopItem(item_id="comic", name="漫画", category="book", base_price=30,
             description="轻松阅读，大幅提升情绪"),
    # 玩具
    ShopItem(item_id="puzzle", name="拼图", category="toy", base_price=40,
             description="休闲益智，小幅恢复精力"),
    ShopItem(item_id="plush_toy", name="毛绒玩具", category="toy", base_price=60,
             description="提升情绪，可送礼"),
    # 药品
    ShopItem(item_id="medicine", name="感冒药", category="medicine", base_price=70,
             description="治疗疾病状态"),
    ShopItem(item_id="vitamin", name="维生素", category="medicine", base_price=35,
             description="提升体力上限 5（持续 1 天）"),
    ShopItem(item_id="bandage", name="创可贴", category="medicine", base_price=12,
             description="处理小伤口"),
    # 服装
    ShopItem(item_id="tshirt", name="T恤", category="clothing", base_price=55,
             description="基础服装，无特殊效果"),
    ShopItem(item_id="dress", name="连衣裙", category="clothing", base_price=120,
             description="提升情绪与自信"),
    ShopItem(item_id="hat", name="帽子", category="clothing", base_price=38,
             description="装饰用，小幅提升情绪"),
    # 其他
    ShopItem(item_id="flower", name="花束", category="other", base_price=45,
             description="可送礼，大幅提升对方好感"),
    ShopItem(item_id="gift_box", name="礼物盒", category="other", base_price=100,
             description="通用礼物，提升对方好感"),
    ShopItem(item_id="phone_charger", name="手机充电器", category="other", base_price=28,
             description="恢复手机电量 30"),
    ShopItem(item_id="umbrella", name="雨伞", category="other", base_price=42,
             description="下雨天必备"),
    ShopItem(item_id="notebook", name="笔记本", category="other", base_price=22,
             description="记录用，无特殊效果"),
    ShopItem(item_id="pen", name="钢笔", category="other", base_price=35,
             description="书写工具"),
]


# 价格浮动范围（基于 base_price 的 ±10% 随机浮动）
PRICE_FLOAT_RATIO = 0.10


def _get_catalog_dict() -> dict[str, ShopItem]:
    """获取商品 ID → ShopItem 映射"""
    return {item.item_id: item for item in DEFAULT_CATALOG}


def _compute_current_price(item: ShopItem) -> int:
    """计算当前售价（基于 base_price 与时间种子的轻微浮动）

    使用日期作为种子，同一天价格稳定，跨天有变化，模拟市场波动。
    """
    today_seed = datetime.now(timezone.utc).timetuple().tm_yday
    # 简单确定性浮动：基于 item_id 哈希与日期
    item_hash = sum(ord(c) for c in item.item_id)
    float_factor = 1.0 + (
        ((today_seed + item_hash) % 100) / 100.0 - 0.5
    ) * 2 * PRICE_FLOAT_RATIO
    return max(1, int(item.base_price * float_factor))


# ============================================================
# MCP Tools
# ============================================================

@mcp.tool()
async def list_items(category: str | None = None) -> dict:
    """列出商店所有商品（可按分类过滤）

    Args:
        category: 可选分类过滤（food/drink/book/toy/medicine/clothing/other）

    Returns:
        {
            "items": [
                {
                    "item_id": str,
                    "name": str,
                    "category": str,
                    "current_price": int,
                    "base_price": int,
                    "description": str,
                    "sellable": bool,
                }
            ],
            "total": int,
        }
    """
    catalog = _get_catalog_dict()
    items = []
    for item in DEFAULT_CATALOG:
        if category and item.category != category:
            continue
        current_price = _compute_current_price(item)
        items.append({
            "item_id": item.item_id,
            "name": item.name,
            "category": item.category,
            "current_price": current_price,
            "base_price": item.base_price,
            "description": item.description,
            "sellable": item.sellable,
        })

    logger.info(
        "list_items_called",
        category=category,
        returned=len(items),
    )

    return {
        "items": items,
        "total": len(items),
    }


@mcp.tool()
async def get_item_details(item_id: str) -> dict:
    """查询单个商品详情

    Args:
        item_id: 商品 ID

    Returns:
        商品完整信息，不存在返回 error
    """
    catalog = _get_catalog_dict()
    item = catalog.get(item_id)
    if item is None:
        return {
            "success": False,
            "error": f"Item not found: {item_id}",
            "item": None,
        }

    current_price = _compute_current_price(item)
    sell_price = int(current_price * item.sell_back_ratio) if item.sellable else 0

    return {
        "success": True,
        "error": None,
        "item": {
            "item_id": item.item_id,
            "name": item.name,
            "category": item.category,
            "current_price": current_price,
            "base_price": item.base_price,
            "sell_price": sell_price,
            "description": item.description,
            "sellable": item.sellable,
            "stackable": item.stackable,
            "sell_back_ratio": item.sell_back_ratio,
        },
    }


@mcp.tool()
async def buy_item(
    item_id: str,
    quantity: int,
    current_money: int,
    current_inventory: dict[str, int] | None = None,
) -> dict:
    """模拟购买商品事务

    校验：商品存在、数量合法、金钱充足
    返回 deltas 由 caller 应用到 character_states

    Args:
        item_id: 商品 ID
        quantity: 购买数量（>=1）
        current_money: 角色当前金钱（caller 从 character_states.money 传入）
        current_inventory: 角色当前库存（caller 从 character_states.inventory 传入）

    Returns:
        {
            "success": bool,
            "action": "buy",
            "item_id": str,
            "item_name": str,
            "quantity": int,
            "unit_price": int,
            "total_price": int,
            "money_delta": int,           # 负数（支出）
            "inventory_delta": dict,       # {item_id: +quantity}
            "new_money": int,              # 预期新金钱（供 caller 校验）
            "error": str | None,
        }
    """
    current_inventory = current_inventory or {}

    # 校验数量
    if quantity < 1:
        return _error_result("buy", item_id, quantity, "Quantity must be >= 1")

    if quantity > 99:
        return _error_result("buy", item_id, quantity, "Quantity exceeds limit (99)")

    # 校验商品存在
    catalog = _get_catalog_dict()
    item = catalog.get(item_id)
    if item is None:
        return _error_result("buy", item_id, quantity, f"Item not found: {item_id}")

    # 计算总价
    unit_price = _compute_current_price(item)
    total_price = unit_price * quantity

    # 校验金钱
    if current_money < total_price:
        return _error_result(
            "buy", item_id, quantity,
            f"Insufficient money: have {current_money}, need {total_price}",
            unit_price=unit_price,
        )

    # 计算 deltas
    money_delta = -total_price
    inventory_delta = {item_id: quantity}

    logger.info(
        "buy_item_validated",
        item_id=item_id,
        quantity=quantity,
        unit_price=unit_price,
        total_price=total_price,
        money_delta=money_delta,
    )

    return {
        "success": True,
        "action": "buy",
        "item_id": item_id,
        "item_name": item.name,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_price": total_price,
        "money_delta": money_delta,
        "inventory_delta": inventory_delta,
        "new_money": current_money + money_delta,
        "error": None,
    }


@mcp.tool()
async def sell_item(
    item_id: str,
    quantity: int,
    current_money: int,
    current_inventory: dict[str, int] | None = None,
) -> dict:
    """模拟出售商品事务

    校验：商品存在且 sellable=True、角色库存足够
    出售价 = 当前售价 × sell_back_ratio（默认 50%）

    Args:
        item_id: 商品 ID
        quantity: 出售数量（>=1）
        current_money: 角色当前金钱
        current_inventory: 角色当前库存

    Returns:
        同 buy_item 结构，money_delta 为正数（收入）
    """
    current_inventory = current_inventory or {}

    # 校验数量
    if quantity < 1:
        return _error_result("sell", item_id, quantity, "Quantity must be >= 1")

    # 校验商品存在
    catalog = _get_catalog_dict()
    item = catalog.get(item_id)
    if item is None:
        return _error_result("sell", item_id, quantity, f"Item not found: {item_id}")

    # 校验可出售
    if not item.sellable:
        return _error_result("sell", item_id, quantity, f"Item {item_id} is not sellable")

    # 校验库存
    have_quantity = current_inventory.get(item_id, 0)
    if have_quantity < quantity:
        return _error_result(
            "sell", item_id, quantity,
            f"Insufficient inventory: have {have_quantity}, need {quantity}",
        )

    # 计算总收入
    current_buy_price = _compute_current_price(item)
    unit_sell_price = int(current_buy_price * item.sell_back_ratio)
    total_income = unit_sell_price * quantity

    money_delta = total_income
    inventory_delta = {item_id: -quantity}

    logger.info(
        "sell_item_validated",
        item_id=item_id,
        quantity=quantity,
        unit_sell_price=unit_sell_price,
        total_income=total_income,
        money_delta=money_delta,
    )

    return {
        "success": True,
        "action": "sell",
        "item_id": item_id,
        "item_name": item.name,
        "quantity": quantity,
        "unit_price": unit_sell_price,
        "total_price": total_income,
        "money_delta": money_delta,
        "inventory_delta": inventory_delta,
        "new_money": current_money + money_delta,
        "error": None,
    }


@mcp.tool()
async def get_shop_categories() -> dict:
    """列出商店所有商品分类（供 LLM 角色浏览目录）

    Returns:
        {
            "categories": [
                {"name": "food", "item_count": 4, "price_range": [15, 50]},
                ...
            ]
        }
    """
    catalog_by_cat: dict[str, list[ShopItem]] = {}
    for item in DEFAULT_CATALOG:
        catalog_by_cat.setdefault(item.category, []).append(item)

    categories = []
    for cat, items in catalog_by_cat.items():
        prices = [_compute_current_price(i) for i in items]
        categories.append({
            "name": cat,
            "item_count": len(items),
            "price_range": [min(prices), max(prices)],
        })

    return {
        "categories": categories,
        "total_categories": len(categories),
    }


# ============================================================
# 辅助函数
# ============================================================

def _error_result(
    action: str,
    item_id: str,
    quantity: int,
    error: str,
    unit_price: int = 0,
) -> dict[str, Any]:
    """构造错误返回结构"""
    return {
        "success": False,
        "action": action,
        "item_id": item_id,
        "item_name": None,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_price": 0,
        "money_delta": 0,
        "inventory_delta": {},
        "new_money": None,
        "error": error,
    }


if __name__ == "__main__":
    mcp.run()
