"""角色相关 API 路由

包含：
- 角色列表与详情查询
- 角色反思 / 计划 / 行为历史
- 角色移动、作息、关系、互动
- 角色状态历史与消息历史
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from structlog import get_logger

from src.auth import decode_token, get_current_user
from src.db.models import Character, CharacterState, CharacterStateHistory
from src.db.repositories import (
    ActionRepository,
    CharacterRepository,
    ConversationRepository,
    MessageRepository,
    PlanRepository,
    ReflectionRepository,
)
from src.db.session import db
from src.modules import RelationGraph
from src.runtime import get_movement_system, get_redis, get_schedule_system
from src.schemas.characters import (
    ActionsOut,
    CharacterDetailOut,
    CharacterListOut,
    NearbyOut,
    PlansOut,
    ReflectionsOut,
    RelationsOut,
    ScheduleOut,
    StateHistoryOut,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["characters"])

# 聚合类端点的跨用户读权限角色（round-6 review H1）
_PRIVILEGED_ROLES = frozenset({"admin", "operator"})


async def principal_with_role(request: Request) -> dict[str, Any]:
    """鉴权并返回带角色的主体：JWT 取 token 的 role claim；API Key 无 RBAC 角色，按最小权限处理"""
    user = await get_current_user(request)
    role = "viewer"
    if user["auth_method"] == "jwt":
        payload = decode_token(request.headers.get("authorization", "")[7:])
        role = str(payload.get("role", "viewer"))
    return {"user_id": user["user_id"], "auth_method": user["auth_method"], "role": role}


# 依赖类型别名（规避 B008：不在函数默认参数中调用 Depends）
PrincipalWithRole = Annotated[dict[str, Any], Depends(principal_with_role)]

# WorldEngine 写入 world:state 的时间字段名是 "world_time"（非 "time"），
# 字段名不一致会导致 hour 恒回退默认值、开放时段判断失真（P0-2）
_DEFAULT_WORLD_HOUR = 8


def _world_hour_from_state(world_state: dict[bytes | str, bytes | str]) -> int:
    """从 world:state 哈希解析当前世界小时，格式 "HH:MM"，异常时回退默认值"""
    world_time = str(world_state.get("world_time", ""))
    try:
        return int(world_time.split(":")[0])
    except (ValueError, IndexError):
        return _DEFAULT_WORLD_HOUR


@router.get("/characters", response_model=CharacterListOut)
async def list_characters(limit: int = 20, active_only: bool = False) -> dict[str, Any]:
    """获取角色列表

    Args:
        limit: 返回数量限制（默认 20）
        active_only: 是否只返回活跃角色（默认 False）

    Returns:
        角色列表
    """
    async with db.session() as session:
        repo = CharacterRepository(session)
        if active_only:
            characters = await repo.get_active_characters()
        else:
            characters = await repo.list_all(limit)

    return {
        "data": [
            {
                "id": str(c.id),
                "name": c.name,
                "age": c.age,
                "occupation": c.occupation,
                "is_active": c.is_active,
            }
            for c in characters
        ],
        "total": len(characters),
    }


@router.get("/characters/{character_id}", response_model=CharacterDetailOut)
async def get_character(character_id: str) -> dict[str, Any]:
    """获取角色详情

    Args:
        character_id: 角色 UUID

    Returns:
        角色档案 + 实时状态
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    # P2-3：编排逻辑下沉 Service 层，路由只做校验与 404 语义
    from src.services import CharacterService

    async with db.session() as session:
        detail = await CharacterService(session).get_character_detail(cid)

    if detail is None:
        raise HTTPException(status_code=404, detail="Character not found")

    return detail


@router.get("/characters/{character_id}/reflections", response_model=ReflectionsOut)
async def get_reflections(character_id: str, limit: int = 10) -> dict[str, Any]:
    """获取角色反思记录

    Args:
        character_id: 角色 UUID
        limit: 返回数量限制（默认 10）

    Returns:
        角色最近的反思记录（按创建时间倒序）
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        repo = ReflectionRepository(session)
        reflections = await repo.get_by_character(cid, limit)

    return {
        "data": [
            {
                "id": str(r.id),
                "content": r.content,
                "created_at": r.created_at.isoformat(),
            }
            for r in reflections
        ],
        "total": len(reflections),
    }


@router.get("/characters/{character_id}/plans", response_model=PlansOut)
async def get_plans(character_id: str) -> dict[str, Any]:
    """获取角色进行中的计划

    Args:
        character_id: 角色 UUID

    Returns:
        角色所有 active 状态的计划（按优先级降序）
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        repo = PlanRepository(session)
        plans = await repo.get_active_plans(cid)

    return {
        "data": [
            {
                "id": str(p.id),
                "type": p.type,
                "title": p.title,
                "description": p.description,
                "status": p.status,
                "priority": p.priority,
                "progress": p.progress,
                "deadline": p.deadline.isoformat() if p.deadline else None,
                "created_at": p.created_at.isoformat(),
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in plans
        ],
        "total": len(plans),
    }


@router.get("/characters/{character_id}/actions", response_model=ActionsOut)
async def get_action_history(character_id: str, limit: int = 50) -> dict[str, Any]:
    """获取角色行为历史

    Args:
        character_id: 角色 UUID
        limit: 返回数量限制（默认 50）

    Returns:
        角色行为时间线（按时间倒序）
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        repo = ActionRepository(session)
        actions = await repo.get_by_character(cid, limit)

    return {
        "data": [
            {
                "id": str(a.id),
                "action_id": a.action_id,
                "action_name": a.action_name,
                "params": a.params,
                "reason": a.reason,
                "result": a.result,
                "duration_minutes": a.duration_minutes,
                "location": a.location,
                "related_characters": a.related_characters,
                "timestamp": a.timestamp.isoformat(),
            }
            for a in actions
        ],
        "total": len(actions),
    }


@router.post("/characters/{character_id}/move")
async def move_character(character_id: str, to_scene: str, hour: int | None = None) -> dict[str, Any]:
    """角色移动到指定场景

    Args:
        character_id: 角色 ID
        to_scene: 目标场景 ID
        hour: 当前小时（用于开放判断），默认从世界状态获取
    """
    movement_system = get_movement_system()
    redis = get_redis()
    if not movement_system or not redis:
        raise HTTPException(status_code=503, detail="Movement system not initialized")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    # 获取角色当前位置
    current_state = await redis.hgetall(f"char:{cid}:state")
    from_scene = str(current_state.get("location", "home"))

    # 获取当前小时（如果未提供）
    if hour is None:
        world_state = await redis.hgetall("world:state")
        hour = _world_hour_from_state(world_state)

    # 执行移动
    result = await movement_system.execute_move(str(cid), from_scene, to_scene, hour=hour)

    if not result.success:
        raise HTTPException(status_code=400, detail=result.reason)

    # 更新角色位置（A-1：双写——先 PG 镜像事务提交，再写 Redis 真相源）
    # 同事务写入 ActionRecord：此前 API 移动绕过审计（审查 P0-5/#5）
    from src.db.models import ActionRecord
    from src.db.repositories import ActionRepository, CharacterRepository
    from src.db.session import db

    async with db.session() as session:
        await ActionRepository(session).add(
            ActionRecord(
                character_id=cid,
                action_id="move",
                action_name="移动",
                params={"target_scene": to_scene, "source": "api"},
                reason=None,
                result=str(result.path),
                duration_minutes=result.total_minutes,
                location=to_scene,
                related_characters=[],
            )
        )
        cas_ok = await CharacterRepository(session).update_state_cas(cid, location=to_scene)
        if not cas_ok:
            # round-3 review M8：CAS 全部重试失败说明状态版本已被并发写推进，
            # 此时继续 hset 会用陈旧位置覆盖 Redis 真相源。在会话内抛出以连带
            # 回滚本次移动的 ActionRecord（半截审计记录比没有更糟），不写 Redis
            raise HTTPException(status_code=409, detail="角色状态已变化，请刷新后重试")
    await redis.hset(f"char:{cid}:state", "location", to_scene)

    return {
        "success": True,
        "from": from_scene,
        "to": to_scene,
        "duration_minutes": result.total_minutes,
        "path": result.path,
    }


@router.get("/characters/{character_id}/schedule", response_model=ScheduleOut)
async def get_character_schedule(character_id: str, hour: int | None = None) -> dict[str, Any]:
    """获取角色作息状态

    Args:
        character_id: 角色 ID
        hour: 查询的小时（默认当前小时）
    """
    schedule_system = get_schedule_system()
    if not schedule_system:
        raise HTTPException(status_code=503, detail="Schedule system not initialized")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    # 获取角色 traits
    async with db.session() as session:
        repo = CharacterRepository(session)
        char_data = await repo.get_by_id(cid)

    if not char_data:
        raise HTTPException(status_code=404, detail="Character not found")

    schedule_type = schedule_system.get_schedule_from_traits(char_data.traits or {})

    if hour is None:
        redis = get_redis()
        world_state = await redis.hgetall("world:state") if redis else {}
        hour = _world_hour_from_state(world_state)

    level = schedule_system.get_activity_level(schedule_type, hour)
    is_sleeping = schedule_system.is_sleeping(schedule_type, hour)
    regen_rate = schedule_system.get_stamina_regen_rate(schedule_type, hour)

    return {
        "character_id": character_id,
        "schedule_type": schedule_type,
        "hour": hour,
        "activity_level": level.value,
        "is_sleeping": is_sleeping,
        "stamina_regen_rate": regen_rate,
    }


@router.get("/characters/{character_id}/relations", response_model=RelationsOut)
async def get_character_relations(character_id: str) -> dict[str, Any]:
    """获取角色的所有关系"""
    redis = get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        graph = RelationGraph(session, redis)
        relations = await graph.get_all_relations(cid)
        # 批量解析目标角色名：前端关系图谱展示名称而非 UUID
        target_ids = [r.target_id for r in relations]
        names: dict[UUID, str] = {}
        if target_ids:
            rows = await session.execute(select(Character.id, Character.name).where(Character.id.in_(target_ids)))
            names = {row[0]: row[1] for row in rows.all()}

    return {
        "data": [
            {
                "target_id": str(r.target_id),
                "target_name": names.get(r.target_id),
                "relationship_type": r.relationship_type,
                "strength": r.strength,
                "last_interaction_at": r.last_interaction_at.isoformat() if r.last_interaction_at else None,
                "notes": r.notes,
            }
            for r in relations
        ],
        "total": len(relations),
    }


@router.get("/characters/{character_id}/nearby", response_model=NearbyOut)
async def get_character_nearby(character_id: str) -> dict[str, Any]:
    """获取与该角色同场景的其他角色（多智能体交互可见性）

    用于前端展示「当前场景中还有谁」，让用户感知到角色间的社交可能性。
    返回数据含角色档案、当前动作、与查询角色的关系强度。

    Args:
        character_id: 角色 UUID

    Returns:
        {
            "data": [
                {
                    "id": "...",
                    "name": "...",
                    "personality": "...",
                    "mood": "...",
                    "current_action_name": "...",
                    "relationship_type": "...",
                    "strength": 50,
                    "location": "cafe"
                }
            ],
            "total": N,
            "location": "cafe"
        }
    """
    redis = get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    # 读取当前位置（Redis 优先）
    redis_state = await redis.hgetall(f"char:{cid}:state")
    raw_location = redis_state.get("location")
    location: str | None = None
    if isinstance(raw_location, str):
        location = raw_location
    elif isinstance(raw_location, (bytes, bytearray)):
        location = raw_location.decode("utf-8")
    elif isinstance(raw_location, memoryview):
        location = raw_location.tobytes().decode("utf-8")
    if not location:
        # 降级到 PG
        async with db.session() as session:
            repo = CharacterRepository(session)
            char_data = await repo.get_character_with_state(cid)
        if char_data is None:
            raise HTTPException(status_code=404, detail="Character not found")
        _, state = char_data
        location = state.location

    if not location:
        return {"data": [], "total": 0, "location": None}

    # 查询同场景其他角色
    async with db.session() as session:
        repo = CharacterRepository(session)
        others = await repo.get_characters_by_location(location=location, exclude_id=cid)

        # 批量查关系（避免 N+1）
        graph = RelationGraph(session, redis)
        result_data = []
        for other_char, other_state in others:
            try:
                rel = await graph.get_relation(cid, other_char.id)
                rel_type = rel.relationship_type if rel else "stranger"
                strength = rel.strength if rel else 0
            except Exception:
                rel_type = "stranger"
                strength = 0

            personality = (other_char.traits or {}).get("personality", [])
            if isinstance(personality, list):
                personality_text = "、".join(personality)
            else:
                personality_text = str(personality)

            current_action_name = None
            if other_state.current_action:
                current_action_name = other_state.current_action.get("action_name")

            result_data.append(
                {
                    "id": str(other_char.id),
                    "name": other_char.name,
                    "personality": personality_text,
                    "mood": other_state.mood,
                    "current_action_name": current_action_name,
                    "relationship_type": rel_type,
                    "strength": strength,
                    "location": location,
                }
            )

    return {
        "data": result_data,
        "total": len(result_data),
        "location": location,
    }


@router.post("/characters/{character_id}/relations/{target_id}/interact")
async def record_interaction(
    character_id: str,
    target_id: str,
    strength_delta: int = 0,
    notes: str | None = None,
) -> dict[str, Any]:
    """记录角色间互动（更新关系）"""
    redis = get_redis()
    if not redis:
        raise HTTPException(status_code=503, detail="Redis not connected")

    try:
        cid = UUID(character_id)
        tid = UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        graph = RelationGraph(session, redis)
        try:
            snap_a, snap_b = await graph.update_on_interaction(cid, tid, strength_delta, notes)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "a_to_b": {
            "relationship_type": snap_a.relationship_type,
            "strength": snap_a.strength,
        },
        "b_to_a": {
            "relationship_type": snap_b.relationship_type,
            "strength": snap_b.strength,
        },
    }


@router.get("/characters/{character_id}/state-history", response_model=StateHistoryOut)
async def get_character_state_history(character_id: UUID, limit: int = 50) -> dict[str, Any]:
    """获取角色状态历史记录（用于状态图表）

    Args:
        character_id: 角色 ID
        limit: 返回记录数（默认 50）

    Returns:
        状态历史列表（按时间正序，便于前端绘制曲线）
    """
    async with db.session() as session:
        # 优先从 character_state_history 表查询（每次状态更新都会写入快照）
        stmt = (
            select(CharacterStateHistory)
            .where(CharacterStateHistory.character_id == character_id)
            .order_by(desc(CharacterStateHistory.recorded_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        history_records = list(result.scalars())

        if history_records:
            return {
                "data": [
                    {
                        "stamina": h.stamina,
                        "satiety": h.satiety,
                        "mood": h.mood,
                        "money": h.money,
                        "phone_battery": h.phone_battery,
                        "social_energy": h.social_energy,
                        "location": h.location,
                        "action_id": h.action_id,
                        "updated_at": h.recorded_at.isoformat() if h.recorded_at else None,
                    }
                    for h in reversed(history_records)
                ],
                "total": len(history_records),
                "source": "history",
            }

        # 回退：历史表暂无数据时返回当前状态（至少一个点）
        cur_stmt = select(CharacterState).where(CharacterState.character_id == character_id)
        cur_result = await session.execute(cur_stmt)
        state = cur_result.scalar_one_or_none()

    if state is None:
        return {"data": [], "total": 0, "source": "empty"}

    return {
        "data": [
            {
                "stamina": state.stamina,
                "satiety": state.satiety,
                "mood": state.mood,
                "money": state.money,
                "phone_battery": state.phone_battery,
                "social_energy": state.social_energy,
                "location": state.location,
                "action_id": None,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }
        ],
        "total": 1,
        "source": "current",
    }


@router.get("/characters/{character_id}/messages")
async def get_character_messages(
    character_id: UUID,
    user: PrincipalWithRole,
    limit: int = 50,
) -> dict[str, Any]:
    """获取角色的消息历史（跨会话）

    归属校验（round-6 review H1）：普通用户仅返回本人与该角色的会话消息，
    admin/operator 可跨用户聚合。

    Args:
        character_id: 角色 ID
        user: 鉴权主体（含 RBAC 角色）
        limit: 返回数量上限

    Returns:
        消息列表（按时间正序）
    """
    privileged = user["role"] in _PRIVILEGED_ROLES
    auth_user_id = user["user_id"]

    async with db.session() as session:
        conv_repo = ConversationRepository(session)
        msg_repo = MessageRepository(session)
        conversations = await conv_repo.list_by_character(character_id, limit=100)
        if not privileged:
            # 仅保留本人会话，防止聚合接口泄露他人私信
            conversations = [c for c in conversations if c.user_id == auth_user_id]
        if not conversations:
            return {"data": [], "total": 0}
        all_messages = []
        for conv in conversations:
            msgs = await msg_repo.list_by_conversation(
                conversation_id=conv.id,
                limit=limit,
                order_desc=True,
            )
            all_messages.extend(msgs)
        # 按时间倒序排序后截断
        all_messages.sort(
            key=lambda m: m.created_at or datetime.min,
            reverse=True,
        )
        all_messages = all_messages[:limit]
        # 返回正序（旧到新）
        all_messages.reverse()
    return {
        "data": [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "sender": m.sender,
                "content": m.content,
                "timestamp": m.created_at.isoformat() if m.created_at else None,
            }
            for m in all_messages
        ],
        "total": len(all_messages),
    }
