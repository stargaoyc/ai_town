"""业务服务层（P2-3 示范落地）

AGENTS.md §4.4：新增业务逻辑优先沉淀为 Service，不在路由函数内堆积。
本包承接从 API 路由抽取的多 Repository 编排逻辑；路由层只做
参数校验、调用 Service、组装响应。
"""

from src.services.character_service import CharacterService

__all__ = ["CharacterService"]
