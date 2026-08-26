# 开发指南

> 本文档面向 AI Town 贡献者，介绍本地开发环境搭建、代码规范、数据访问层、测试与贡献流程。

---

## 一、环境要求

| 工具       | 版本  | 说明                                         |
| ---------- | ----- | -------------------------------------------- |
| Python     | 3.13+ | 后端                                         |
| uv         | 最新  | Python 包管理                 |
| Node.js    | 22+   | 前端                                         |
| pnpm       | 11+   | 前端包管理                                   |
| PostgreSQL | 18+   | 需启用 `vector` 扩展（PG 18 内建 `uuidv7()`，无需第三方扩展） |
| Redis      | 8.0+  | —                                            |
| Docker     | 24+   | 容器化部署                                   |

---

## 二、本地开发快速开始

### 2.1 启动依赖（PG / Redis）

```bash
docker compose up -d postgres redis
```

### 2.2 后端

```bash
cd packages/backend
uv sync                           # 安装依赖
cp ../../.env.example .env        # 填写密钥
alembic upgrade head              # 数据库迁移
uvicorn src.main:app --reload --port 8000
```

访问 `http://localhost:8000/docs` 查看 Swagger UI。

### 2.3 前端

```bash
cd packages/frontend
pnpm install
pnpm gen:api                      # 从后端 OpenAPI 生成类型
pnpm dev                          # 启动 Vite 开发服务器
```

访问 `http://localhost:5173`。

### 2.4 本地工具（无需单独启动）

工具已内联到后端进程（`src/tools/`），随后端启动自动加载。工具按命名空间组织（shop / knowledge / social / world / self_info），通过 Redis hash `tools:enabled` 控制启用状态。

---

## 三、后端代码结构

```text
packages/backend/src/
├── core/                  # 核心引擎
│   ├── world/             # World Tick（engine.py + evolutions/ 演化器）
│   ├── character/tick.py  # Character Tick 循环（CharacterTickEngine）
│   ├── locks.py           # 分布式锁（token + compare-and-delete）
│   ├── rehydration.py     # 启动时 PG→Redis 状态回灌
│   └── state_codec.py     # Redis 状态编解码（单一真相源）
├── actions/               # 内置 Action 定义（move/life/work/social）+ registry
├── adapters/              # 平台适配器（OneBot / Lark）
├── api/                   # FastAPI 路由（按资源拆分，由 main.py 聚合注册）
├── auth/                  # JWT / RBAC / API Key
├── cost_control/          # 日预算 + 熔断器
├── db/                    # models / repositories / session
├── llm/                   # LLMClient + PromptTemplates
├── memory/                # 记忆/反思/日记/Person Memory 服务
├── modules/               # 模块管理器（town/schedule/movement/character）
├── messaging/             # 消息服务 + WebSocket
├── observability/         # 日志/指标/追踪/Langfuse
├── scheduler/             # 分区预创建调度器
├── security/              # 限流/Prompt 注入防护
├── tools/                 # 本地工具（进程内 async 函数，ToolRegistry）
└── main.py                # FastAPI 入口（lifespan + 路由聚合）
```

---

## 四、数据访问层

> **混合策略**：ORM（SQLAlchemy 2.0）负责模型定义/迁移/简单 CRUD，原生 SQL（`text()`）负责向量检索/复杂查询/性能热点。详见 [架构设计 §5.7](architecture.md#57-数据访问策略orm-与原生-sql-混合)。

### 4.1 异步会话工厂

```python
# db/session.py
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from contextlib import asynccontextmanager

class DB:
    def __init__(self, url: str, pool_size: int = 20, max_overflow: int = 10):
        self.engine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            echo=False,
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncSession:
        async with self.session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
```

### 4.2 Repository 模式

```python
# db/repositories/memory_repo.py
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.memory_episode import MemoryEpisode
import uuid

class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, ep: MemoryEpisode) -> MemoryEpisode:
        self.session.add(ep)
        await self.session.flush()
        return ep

    async def search_similar(
        self, character_id: uuid.UUID, query_vec: list[float], top_k: int = 10
    ) -> list[MemoryEpisode]:
        stmt = (
            select(MemoryEpisode)
            .where(MemoryEpisode.character_id == character_id)
            .order_by(MemoryEpisode.embedding.cosine_distance(query_vec))
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def recent(self, character_id: uuid.UUID, limit: int = 50):
        stmt = (
            select(MemoryEpisode)
            .where(MemoryEpisode.character_id == character_id)
            .order_by(desc(MemoryEpisode.timestamp))
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())
```

### 4.3 ORM 模型示例

```python
# db/models/memory_episode.py
from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Integer, Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from uuid6 import uuid7                              # UUID v7, 时间有序
from .base import Base
import uuid

class MemoryEpisode(Base):
    __tablename__ = "memory_episodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE")
    )
    content: Mapped[str] = mapped_column(String)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    importance: Mapped[int] = mapped_column(Integer, default=5)
    timestamp: Mapped[datetime]
    action_id: Mapped[str | None]
    location: Mapped[str | None]
    related_characters: Mapped[list[uuid.UUID]] = mapped_column(default=list)
    is_reflected: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String, default="action")

    __table_args__ = (
        Index("idx_mem_char_time", "character_id", timestamp.desc()),
    )
```

### 4.4 事务化最佳实践

**Action 执行闭环**必须在同一事务中完成"写行为记录 + 写记忆向量 + 更新状态"，杜绝半写：

```python
async def execute_action(db: DB, redis: Redis, character_id, decision):
    embedding_vec = await embed(episode_text(character_id, decision))

    async with db.session() as session:           # 事务边界
        action_repo = ActionRepository(session)
        memory_repo = MemoryRepository(session)

        await action_repo.add(ActionRecord(...))
        await memory_repo.add(MemoryEpisode(..., embedding=embedding_vec))
        # 退出 contextmanager 时自动 commit; 任一失败整体回滚

    await redis.hset(f"char:{character_id}:state", mapping=new_state.to_dict())
```

---

## 五、数据库迁移（alembic）

### 5.1 创建迁移

```bash
cd packages/backend
alembic revision --autogenerate -m "add memory_episodes table"
```

### 5.2 扩展与 HNSW 索引需手写原生 SQL

HNSW 索引不能通过 ORM 自动生成，需在迁移脚本中用 `op.execute()`（PG 18 内建 `uuidv7()`，无需创建扩展）：

```python
# migrations/versions/xxxx_init.py
def upgrade():
    # 1. 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 2. 建表 (主键用 uuidv7() 默认值)
    op.create_table(
        "memory_episodes",
        sa.Column("id", sa.UUID, primary_key=True,
                  server_default=sa.text("uuidv7()")),
        # ... 其他字段 ...
    )

    # 3. HNSW 向量索引
    op.execute(
        "CREATE INDEX idx_mem_embedding_hnsw ON memory_episodes "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )

def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_mem_embedding_hnsw;")
    op.drop_table("memory_episodes")
```

### 5.3 执行迁移

```bash
alembic upgrade head          # 升到最新
alembic downgrade -1          # 回滚一版
alembic current               # 查看当前版本
```

---

## 六、添加新功能

### 6.1 新增 Action

```python
# src/actions/life.py（新增到既有分类文件，或新建文件）
from src.actions.base import Action, ActionCategory


def _my_action() -> Action:
    return Action(
        id="meditate",
        name="冥想",
        category=ActionCategory.LIFE,
        precondition=lambda s: s.get("stamina", 0) < 50,
        duration_minutes=10,
        energy_cost=20,  # 正数 = 恢复体力
    )
```

在 `src/actions/__init__.py` 的 `register_all()` 中加入 `registry.register(_my_action())`。完整字段说明见 `src/actions/base.py`。

### 6.2 新增本地工具

工具已内联到 `packages/backend/src/tools/`，无需创建独立 Server。新增工具步骤：

1. 在 `src/tools/` 下新建模块（如 `my_tool.py`），实现 async 函数；
2. 在 `src/tools/registry.py` 的 `TOOL_REGISTRY` 字典中注册工具，声明 `description`、`llm_params`、`injected_params`、`state_mutating`；
3. 状态变更类工具需在 `core/character/tick.py` 的 `_apply_tool_deltas()` 中处理返回的 delta 字段。

```python
# src/tools/my_tool.py
async def my_tool(query: str) -> dict:
    """工具描述"""
    return {"result": ...}
```

```python
# src/tools/registry.py
TOOL_REGISTRY["my_namespace.my_tool"] = {
    "func": my_tool,
    "description": "工具描述（LLM Prompt 中展示）",
    "llm_params": {"query": "查询关键词"},
    "injected_params": {},   # 需从角色状态注入的参数
    "state_mutating": False,
}
```

### 6.3 新增 API 端点

```python
# api/my_resource.py
from fastapi import APIRouter, Depends
from db.session import DB

router = APIRouter(prefix="/my-resource", tags=["my-resource"])

@router.get("/")
async def list_items(db: DB = Depends(get_db)):
    async with db.session() as s:
        ...
    return {"items": [...]}
```

在 `main.py` 中导入并注册 router（路由按资源拆分到 `src/api/` 下各模块，由 `main.py` 聚合）：

```python
# main.py
from src.api.my_resource import router as my_resource_router
app.include_router(my_resource_router)
```

> 全局异常处理器已在 `src/api/exceptions.py` 注册（`register_exception_handlers(app)`），统一错误响应格式并附带 `trace_id`，新端点无需重复处理。

---

## 七、代码规范

### 7.1 Python

| 工具            | 用途          |
| --------------- | ------------- |
| `ruff`          | lint + format |
| `mypy`          | 类型检查      |
| `black`（可选） | 备用格式化    |

```bash
uv run ruff check src/     # lint
uv run ruff format src/    # format
uv run mypy src/           # 类型检查
```

### 7.2 TypeScript

| 工具           | 用途                           |
| -------------- | ------------------------------ |
| `oxlint`       | lint（Rust 内核，替代 ESLint） |
| `prettier`     | format                         |
| `tsc --noEmit` | 类型检查                       |

### 7.3 提交规范

遵循 Conventional Commits：

```text
feat: 新增咖啡店 Action
fix: 修复记忆检索向量维度不匹配
docs: 补充迁移指南
refactor: 重构 Action 执行事务
test: 增加 MemoryRepository 单测
chore: 升级依赖
```

---

## 八、测试

### 8.1 后端测试

| 类型     | 工具                    | 说明                      |
| -------- | ----------------------- | ------------------------- |
| 单元测试 | pytest                  | Repository / Service 逻辑 |
| 集成测试 | pytest + testcontainers | 真实 PG + Redis           |
| API 测试 | httpx + pytest          | FastAPI 端点              |

```bash
cd packages/backend
pytest tests/unit/                # 单元测试
pytest tests/integration/         # 集成测试(需 Docker)
pytest -k "memory"                # 按关键字过滤
```

#### 集成测试示例（testcontainers）

```python
# tests/integration/test_memory_repo.py
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture
async def db():
    with PostgresContainer("pgvector/pgvector:pg17") as pg:
        # 初始化扩展、迁移、种子数据
        ...
        yield DB(pg.get_connection_url())

async def test_memory_search(db):
    async with db.session() as s:
        repo = MemoryRepository(s)
        await repo.add(MemoryEpisode(..., embedding=[...] * 1536))
        results = await repo.search_similar(cid, query_vec, top_k=5)
        assert len(results) == 1
```

### 8.2 前端测试

| 类型     | 工具            |
| -------- | --------------- |
| 单元测试 | Vitest          |
| 组件测试 | Testing Library |
| E2E      | Playwright      |

```bash
cd packages/frontend
pnpm test                 # 单元 + 组件测试
pnpm test:e2e             # E2E
```

### 8.3 覆盖率要求

- 后端核心模块（core / memory / db）覆盖率 ≥ 80%
- API 层覆盖率 ≥ 60%
- 前端关键组件覆盖率 ≥ 70%

---

## 九、调试技巧

### 9.1 基于 trace_id 调试

1. 在 Grafana/Jaeger 搜索 `trace_id`；
2. 查看 Span 链路定位耗时瓶颈；
3. 在 Loki 按 `trace_id` 过滤日志；
4. 在 Langfuse 查看 LLM Prompt/Completion。

### 9.2 世界回放

```bash
# 拉取某快照
curl http://localhost:8000/api/v1/world/snapshots | jq '.data[-1]'

# 用指定快照重置世界态（调试用）
curl -X POST http://localhost:8000/api/v1/admin/restore-snapshot \
  -H "Content-Type: application/json" \
  -d '{"snapshot_id": "..."}'
```

### 9.3 强制触发角色 Tick

```bash
curl -X POST http://localhost:8000/api/v1/admin/tick \
  -H "Content-Type: application/json" \
  -d '{"character_id": "...", "force": true}'
```

---

## 十、贡献流程

1. Fork 仓库并创建分支：`feat/my-feature`
2. 编写代码 + 测试，确保 `pytest` 与 `pnpm test` 通过
3. `ruff check` 与 `mypy` 无报错
4. 提交 PR，描述变更与动机
5. 通过 Code Review 后合并

---

## 十一、相关文档

| 主题         | 文档                                         |
| ------------ | -------------------------------------------- |
| 数据模型     | [data-model.md](data-model.md)               |
| 架构总览     | [architecture.md](architecture.md)           |
| 配置参考     | [config-reference.md](config-reference.md)   |
| 部署         | [deployment.md](deployment.md)               |
| Docker 部署  | [docker-deployment.md](docker-deployment.md) |
| 新手学习指南 | [getting-started.md](getting-started.md)     |
