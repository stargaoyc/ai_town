"""记忆扩展 API 路由

包含：
- 角色日记：基于 memory_episodes 生成的叙事性归档
- 角色对用户的记忆：Person Memory
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from structlog import get_logger

from src.auth import decode_token, get_current_user
from src.auth.rbac import require_role
from src.db.repositories import MemoryRepository
from src.db.session import db
from src.memory.diary_service import DiaryService
from src.memory.person_memory_service import PersonMemoryService
from src.schemas.api_out import (
    DiariesListOut,
    DiaryGeneratedOut,
    MemoriesListOut,
    PersonMemoriesListOut,
    PersonMemoryGetOut,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["memory-extension"])

AdminOrOperator = Annotated[dict[str, Any], Depends(require_role("admin", "operator"))]

# 聚合类端点的跨用户读权限角色（round-6 review H2）
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


def _get_diary_service() -> DiaryService:
    return DiaryService(session_factory=db.session)


def _get_person_memory_service() -> PersonMemoryService:
    return PersonMemoryService(session_factory=db.session)


# === 日记接口 ===


@router.get("/characters/{character_id}/diaries", response_model=DiariesListOut)
async def list_diaries(
    character_id: str,
    period: Literal["day", "week", "month", "year"] | None = None,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """获取角色日记列表

    Args:
        character_id: 角色 UUID
        period: 周期过滤（day/week/month/year）
        limit: 返回数量上限
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    service = _get_diary_service()
    diaries = await service.get_diaries(cid, period=period, limit=limit)
    return {"data": diaries, "total": len(diaries)}


@router.post("/characters/{character_id}/diaries/generate", response_model=DiaryGeneratedOut)
async def generate_diary(
    character_id: str,
    _user: AdminOrOperator,
    period: Literal["day", "week", "month", "year"] = "day",
    character_name: str = "",
) -> dict[str, Any]:
    """为角色生成指定周期的日记

    需 admin/operator 权限。
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    if not character_name:
        # 从数据库查询角色名
        async with db.session() as session:
            result = await session.execute(
                text("SELECT name FROM characters WHERE id = :cid"),
                {"cid": str(cid)},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Character not found") from None
            character_name = row[0]

    service = _get_diary_service()
    diary = await service.generate_diary(
        character_id=cid,
        character_name=character_name,
        period=period,
    )
    if diary is None:
        raise HTTPException(
            status_code=422,
            detail="Diary generation failed: insufficient memories or LLM unavailable",
        ) from None
    return {"data": diary}


# === 角色对用户的记忆接口 ===


@router.get("/characters/{character_id}/person-memory", response_model=PersonMemoryGetOut)
async def get_person_memory(
    character_id: str,
    user: PrincipalWithRole,
    user_id: str = Query(..., description="用户标识"),
) -> dict[str, Any]:
    """获取角色对某用户的记忆

    归属校验（round-6 review H2）：仅本人或 admin/operator 可查询。
    """
    if user_id != user["user_id"] and user["role"] not in _PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed to access other user's person-memory")

    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    service = _get_person_memory_service()
    memory = await service.get_memory(cid, user_id)
    if memory is None:
        return {"data": None, "exists": False}
    return {"data": memory, "exists": True}


@router.get("/characters/{character_id}/person-memory/list", response_model=PersonMemoriesListOut)
async def list_person_memories(
    character_id: str,
    _user: AdminOrOperator,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """获取角色对所有用户的记忆列表（按热度倒序）

    跨用户记忆汇总仅限 admin/operator（round-6 review H2）。
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        result = await session.execute(
            text("""
                SELECT id, character_id, user_id, platform, content,
                       heat, last_interaction_at, created_at, updated_at
                FROM person_memories
                WHERE character_id = :cid
                ORDER BY heat DESC, last_interaction_at DESC
                LIMIT :limit
            """),
            {"cid": str(cid), "limit": limit},
        )
        rows = [dict(r._mapping) for r in result]

    # 序列化
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif isinstance(v, UUID):
                r[k] = str(v)
    return {"data": rows, "total": len(rows)}


# === 角色记忆接口 ===


@router.get("/memories/{character_id}", response_model=MemoriesListOut)
async def get_memories(character_id: str, limit: int = 20) -> dict[str, Any]:
    """获取角色记忆

    Args:
        character_id: 角色 UUID
        limit: 返回数量限制（默认 20）

    Returns:
        角色最近的记忆片段
    """
    try:
        cid = UUID(character_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from None

    async with db.session() as session:
        repo = MemoryRepository(session)
        episodes = await repo.recent(cid, limit)

    return {
        "data": [
            {
                "id": str(e.id),
                "content": e.content,
                "timestamp": e.timestamp.isoformat(),
                "importance": e.importance,
                "is_reflected": e.is_reflected,
            }
            for e in episodes
        ],
        "total": len(episodes),
    }
