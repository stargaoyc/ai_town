"""round-5 review L8：跨角色全局检索必须在调用点显式声明范围扩张

allow_cross_character 为 keyword-only 必填参数（无默认值），
漏传即在参数绑定期抛 TypeError——把「仅限管理面」从文档约定
变成仓库层签名强制，非管理调用方无法无意识越过角色边界。
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.memory_repo import MemoryRepository


def _unit_vec(dim: int = 2048, index: int = 0) -> list[float]:
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


async def test_global_search_without_flag_raises_type_error() -> None:
    # session 以占位对象注入即可：TypeError 发生在参数绑定，函数体永不执行
    repo = MemoryRepository(cast(AsyncSession, object()))
    # cast 到 Any 绕过静态检查——「缺参调用本身」就是被测契约
    raw_call = cast(Any, repo.search_hybrid_global)

    with pytest.raises(TypeError):
        await raw_call(_unit_vec(index=7))
