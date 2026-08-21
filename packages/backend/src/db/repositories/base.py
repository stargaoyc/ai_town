"""基础 Repository - 提供通用 CRUD 能力

所有具体 Repository 继承此类，复用 get_by_id / add / list_all 等通用方法。
使用泛型 ModelT 约束每个子类操作的 ORM 模型类型。
"""

from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 泛型类型变量，表示 Repository 操作的 ORM 模型类型
ModelT = TypeVar("ModelT")


class BaseRepository[ModelT]:
    """基础 Repository - 提供通用 CRUD"""

    def __init__(self, session: AsyncSession, model: type[ModelT]):
        self.session = session
        self.model = model

    async def get_by_id(self, id: UUID) -> ModelT | None:
        """按主键查询单条记录，不存在返回 None"""
        return await self.session.get(self.model, id)

    async def add(self, obj: ModelT) -> ModelT:
        """新增一条记录（flush 不 commit，由调用方控制事务）"""
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def list_all(self, limit: int = 100) -> list[ModelT]:
        """查询全表记录（默认限制 100 条，防止全表扫描"""
        stmt = select(self.model).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars())
