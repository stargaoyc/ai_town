"""小镇场景相关 API 路由

包含：
- 场景列表查询
- 场景详情查询（含实时状态：拥挤度、在场角色）
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from src.runtime import get_scene_loader
from src.schemas.api_out import SceneOut, ScenesListOut

router = APIRouter(prefix="/api/v1/town", tags=["town"])


@router.get("/scenes", response_model=ScenesListOut)
async def list_scenes() -> dict[str, Any]:
    """获取所有场景列表"""
    scene_loader = get_scene_loader()
    if not scene_loader:
        raise HTTPException(status_code=503, detail="Scene loader not initialized")

    scenes = scene_loader.get_all_scenes()
    return {
        "data": [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type.value,
                "open_hours": s.open_hours,
                "capacity": s.capacity,
                "activities": s.activities,
                "workday_only": s.workday_only,
            }
            for s in scenes.values()
        ],
        "total": len(scenes),
    }


@router.get("/scenes/{scene_id}", response_model=SceneOut)
async def get_scene_detail(scene_id: str) -> dict[str, Any]:
    """获取场景详情（含实时状态）"""
    scene_loader = get_scene_loader()
    if not scene_loader:
        raise HTTPException(status_code=503, detail="Scene loader not initialized")

    scene = scene_loader.get_scene(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    crowdedness = await scene_loader.get_crowdedness(scene_id)
    present_chars = await scene_loader.get_present_characters(scene_id)

    return {
        "scene": {
            "id": scene.id,
            "name": scene.name,
            "type": scene.type.value,
            "open_hours": scene.open_hours,
            "capacity": scene.capacity,
            "activities": scene.activities,
            "workday_only": scene.workday_only,
        },
        "runtime": {
            "crowdedness": crowdedness,
            "present_characters": present_chars,
            "present_count": len(present_chars),
        },
    }
