"""Action 系统基础数据结构

定义 Action 的核心数据模型、分类枚举与执行/决策结果。

设计要点：
- Action 是角色的原子行为单元，按场景组织（scene + activity）。
- precondition 由代码过滤候选，LLM 只能在候选中选择，不能绕过 precondition。
- executor 计算状态变更，真正的状态写入由执行层（事务化）完成。

资源字段符号约定（统一为"带符号增量"）：
- energy_cost / satiety_cost / social_cost / phone_battery_cost：
  正数 = 恢复（对应状态值增加），负数 = 消耗（对应状态值减少）。
- money_cost：正数 = 花费金额（从 money 中扣除）。
- money_gain：正数 = 获得金额（加到 money）。
"""

from collections.abc import Callable
from enum import Enum

from pydantic import BaseModel, Field


class ActionCategory(str, Enum):
    """Action 分类"""

    MOVE = "move"
    LIFE = "life"
    WORK = "work"
    SOCIAL = "social"
    SPECIAL = "special"


class Action(BaseModel):
    """Action 定义

    一个 Action 描述了角色可以执行的某类原子行为，包括其所属分类、
    所需场景、资源消耗/恢复、前置条件与执行器。

    precondition / executor 约定：
    - precondition: (state: dict) -> bool，state 为角色当前状态字典，
      包含 location / stamina / satiety / mood / money / phone_battery /
      social_energy / current_action 等字段。
    - executor: (state: dict, params: dict) -> dict，返回需要变更的状态字段
      及其新值（绝对值，非增量），由执行层合并写入。
      若为 None，则执行层仅应用 cost 字段带来的状态变化（见 apply_cost_fields）。
    """

    id: str  # 唯一标识
    name: str  # 显示名
    description: str | None = None  # 行为描述（可选）
    category: ActionCategory
    scene: str | None = None  # 所需场景（None 表示任意场景）
    activity: str | None = None  # 场景活动类型
    duration_minutes: int = 10  # 基础耗时（虚拟分钟）
    allow_dynamic_duration: bool = False  # 允许 LLM 动态调整耗时
    energy_cost: int = 0  # 体力变化（正=恢复，负=消耗）
    satiety_cost: int = 0  # 饱腹度变化（正=恢复，负=消耗）
    social_cost: int = 0  # 社交能量变化（正=恢复，负=消耗）
    money_cost: int = 0  # 金钱消耗（正数=花费金额）
    money_gain: int = 0  # 金钱收益（正数=获得金额）
    phone_battery_cost: int = 0  # 手机电量变化（正=恢复，负=消耗）
    precondition: Callable | None = None  # 前置条件检查函数
    executor: Callable | None = None  # 执行器函数（可选，默认只更新状态）
    params_schema: dict | None = None  # 参数 JSON Schema（用于 LLM 决策）


class ActionResult(BaseModel):
    """Action 执行结果"""

    success: bool
    new_state: dict  # 状态变更
    message: str | None = None


class DecisionResult(BaseModel):
    """LLM 决策结果（结构化）

    LLM 仅在候选 Action 中选择并给出理由，真正的状态变更由执行层完成。
    """

    action: str  # 选中的 Action ID
    reason: str  # 决策理由
    params: dict = Field(default_factory=dict)  # 执行参数
    duration: int | None = None  # 动态耗时（仅 allow_dynamic_duration=True 时有效）
    plan_changes: list[dict] = Field(default_factory=list)  # 计划变更
    proactive_share_intent: bool = False  # 是否想主动分享


# 资源型状态字段的取值范围（与 character_states 表注释一致）
_RESOURCE_MIN = 0
_RESOURCE_MAX = 100


def clamp_resource(value: int, lo: int = _RESOURCE_MIN, hi: int = _RESOURCE_MAX) -> int:
    """将资源值约束到 [lo, hi] 区间（体力/饱腹度/手机电量/社交能量等）"""
    return max(lo, min(hi, value))


def apply_cost_fields(state: dict, action: Action) -> dict:
    """根据 Action 的资源字段计算状态变更（带符号增量，资源自动 clamp 到 [0,100]）

    当 action.executor 为 None 时，执行层使用本函数应用默认状态更新；
    当 action.executor 不为 None 时，执行层应将本函数返回值与 executor 返回值合并
    （executor 返回值优先覆盖），再写入角色状态。

    Returns:
        发生变化的字段及其新值（绝对值）。
    """
    changes: dict = {}
    if action.energy_cost:
        changes["stamina"] = clamp_resource(state.get("stamina", 0) + action.energy_cost)
    if action.satiety_cost:
        changes["satiety"] = clamp_resource(state.get("satiety", 0) + action.satiety_cost)
    if action.social_cost:
        changes["social_energy"] = clamp_resource(state.get("social_energy", 0) + action.social_cost)
    if action.phone_battery_cost:
        changes["phone_battery"] = clamp_resource(state.get("phone_battery", 0) + action.phone_battery_cost)
    if action.money_cost or action.money_gain:
        changes["money"] = state.get("money", 0) - action.money_cost + action.money_gain
    return changes
