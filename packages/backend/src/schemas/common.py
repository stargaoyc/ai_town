"""API 响应模型 - 公共信封

类型收敛专项（复审 #19）：全部端点以命名 Pydantic 模型声明响应，
OpenAPI 导出后经 pnpm gen:api 生成前端契约类型，替代手写 interface。
"""

from __future__ import annotations

from pydantic import BaseModel


class SuccessOut(BaseModel):
    """通用操作成功响应"""

    success: bool


class SuccessIdOut(BaseModel):
    """操作成功 + 目标 ID"""

    success: bool
    id: str


class SuccessUpdatedOut(BaseModel):
    """操作成功 + 影响条数"""

    success: bool
    updated: int
