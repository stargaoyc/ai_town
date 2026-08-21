"""移动类 Action

move: 移动到指定场景（参数 target_scene）
- 实际耗时由"移动矩阵"决定，从 Redis world:state:matrix 读取
- 移动消耗体力（energy_cost = -5）

移动矩阵格式（与 configs/world-map.yaml 的 adjacency 一致）：
    { "home": {"school": 5, "cafe": 8, ...}, "school": {...}, ... }
存储在 Redis 的 world:state:matrix 键中（JSON 字符串）。
"""

import json
from typing import Any

from redis.asyncio import Redis
from structlog import get_logger

from src.actions.base import Action, ActionCategory

logger = get_logger()

# Redis 中存储移动矩阵的键
MOVE_MATRIX_REDIS_KEY = "world:state:matrix"
# 无法从矩阵读取时的默认移动耗时（虚拟分钟）
DEFAULT_MOVE_DURATION = 10


def _move_executor(state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """移动执行器：仅更新位置；体力消耗由 energy_cost 字段统一应用"""
    target = params.get("target_scene")
    if not target:
        raise ValueError("move Action 缺少参数 target_scene")
    return {"location": target}


async def compute_move_duration(redis: Redis, from_scene: str, to_scene: str) -> int:
    """从 Redis 移动矩阵查询两点间的移动耗时（虚拟分钟）

    Args:
        redis: redis.asyncio.Redis 客户端
        from_scene: 起点场景 ID
        to_scene: 终点场景 ID

    Returns:
        移动耗时；起点 == 终点返回 0；矩阵缺失或无路径时返回 DEFAULT_MOVE_DURATION。
    """
    if from_scene == to_scene:
        return 0
    try:
        raw = await redis.get(MOVE_MATRIX_REDIS_KEY)
        if raw is None:
            return DEFAULT_MOVE_DURATION
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        matrix = json.loads(text)
        minutes = matrix.get(from_scene, {}).get(to_scene)
        if minutes is not None:
            return int(minutes)
    except Exception as e:  # 矩阵读取失败时降级为默认耗时，避免阻塞世界 Tick
        logger.warning(
            "move_matrix_read_failed",
            error=str(e),
            from_scene=from_scene,
            to_scene=to_scene,
        )
    return DEFAULT_MOVE_DURATION


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
