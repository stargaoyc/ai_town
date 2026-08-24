"""移动类 Action

move: 移动到指定场景（参数 target_scene）
- 实际耗时由 MovementSystem 按 configs/world-map.yaml 连通矩阵计算
  （此前此处曾有从 Redis world:state:matrix 读取耗时的实现，但该键全仓无写入方，
  属幻影数据源，已随 compute_move_duration 一并删除 —— 审查 P0-4）
- 移动消耗体力（energy_cost = -5）
"""

from typing import Any

from src.actions.base import Action, ActionCategory

# 无法计算矩阵耗时时的默认移动耗时（虚拟分钟）
DEFAULT_MOVE_DURATION = 10


def _move_executor(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """移动执行器：仅更新位置；体力消耗由 energy_cost 字段统一应用"""
    target = params.get("target_scene")
    if not target:
        raise ValueError("move Action 缺少参数 target_scene")
    return {"location": target}


def build_move_action() -> Action:
    """构造移动 Action"""
    return Action(
        id="move",
        name="移动",
        category=ActionCategory.MOVE,
        scene=None,  # 任意场景均可发起移动
        activity=None,
        duration_minutes=DEFAULT_MOVE_DURATION,  # 基础耗时，实际由移动矩阵决定
        allow_dynamic_duration=False,
        energy_cost=-5,  # 移动消耗 5 点体力
        precondition=None,  # 移动作为常驻候选，目标场景合法性在执行时校验
        executor=_move_executor,
        params_schema={
            "type": "object",
            "properties": {
                "target_scene": {
                    "type": "string",
                    "description": "目标场景 ID",
                }
            },
            "required": ["target_scene"],
        },
    )
