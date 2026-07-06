# 开发指南

> 本文档面向 AI Town 贡献者，介绍本地开发环境搭建、代码规范、数据访问层、测试与贡献流程。

---

## 一、环境要求

| 工具 | 版本 | 说明 |
|------|------|------|
| Python | 3.12+ | 后端 |
| uv / Poetry | 最新 | Python 包管理 |
| Node.js | 22+ | 前端 |
| pnpm | 11+ | 前端包管理 |
| PostgreSQL | 17+ | 需启用 `vector`、`uuid-ossp`、`pg_trgm` 扩展 |
| Redis | 7.4+ | — |
| Docker | 24+ | 容器化部署 |

---

## 二、本地开发快速开始

### 2.1 启动依赖（PG / Redis / MinIO）

```bash
docker compose up -d postgres redis minio
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

### 2.4 MCP Servers（按需）

```bash
cd packages/mcp-servers/code-executor
uv sync
uv run server.py
```

---

## 三、后端代码结构

```text
packages/backend/src/
├── core/                  # 核心引擎
│   ├── world_engine.py    # World Tick 循环
│   ├── character_tick.py  # Character Tick 循环
│   ├── action_system.py   # Action 注册与执行
│   └── actions/           # 内置 Action 定义
│       ├── move.py
│       ├── life.py
│       ├── work.py
│       └── social.py
├── agents/                # LangGraph 角色实现
├── memory/                # 记忆/反思/规划服务
├── modules/               # 模块管理器
├── tools/                 # MCP 集成
│   ├── base.py            # Tool 抽象
│   ├── registry.py        # ToolRegistry
│   └── mcp_client.py      # MCP 客户端
├── messaging/             # 消息服务
│   ├── adapters/          # 平台适配器
│   └── service.py
├── api/                   # FastAPI 路由
│   ├── characters.py
│   ├── world.py
│   ├── modules.py
│   ├── conversations.py
│   └── ...
├── db/                    # 数据访问层
│   ├── session.py         # 异步会话工厂
│   ├── base.py            # Declarative Base
│   ├── models/            # SQLAlchemy ORM 模型
│   ├── repositories/      # Repository 模式
│   └── migrations/        # alembic 迁移
├── observability/         # OTel/Langfuse 配置
├── config.py              # 配置加载
└── main.py                # FastAPI 入口
```

---

## 四、数据访问层

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
from .base import Base
import uuid

class MemoryEpisode(Base):
    __tablename__ = "memory_episodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
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

### 5.2 HNSW 索引需手写原生 SQL

HNSW 索引不能通过 ORM `Index` 自动生成，需在迁移脚本中用 `op.execute()`：

```python
# migrations/versions/xxxx_init.py
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    # ... 建表 ...
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
# core/actions/my_action.py
from core.action_system import Action, ActionCategory, ActionRegistry

def register(registry: ActionRegistry):
    registry.register(Action(
        id="my_action",
        name="我的行为",
        category=ActionCategory.LIFE,
        precondition=lambda s: s.energy < 50,
        executor=lambda s, p: s.replace(energy=s.energy + 20),
        duration_minutes=10,
        energy_cost=+20,
        social_impact=0,
    ))
```

在 `core/actions/__init__.py` 中调用 `register`。

### 6.2 新增 MCP Server

```bash
mkdir packages/mcp-servers/my-server
cd packages/mcp-servers/my-server
uv init
uv add mcp
```

```python
# server.py
from mcp.server import Server, tool

server = Server("my-server")

@server.tool()
async def my_tool(param: str) -> dict:
    """工具描述"""
    return {"result": ...}

if __name__ == "__main__":
    server.run()
```

在 `config.yaml` 与 `.env` 中注册模块。

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

# api/__init__.py 中注册 router
```

---

## 七、代码规范

### 7.1 Python

| 工具 | 用途 |
|------|------|
| `ruff` | lint + format |
| `mypy` | 类型检查 |
| `black`（可选） | 备用格式化 |

```bash
ruff check src/            # lint
ruff format src/           # format
mypy src/                  # 类型检查
```

### 7.2 TypeScript

| 工具 | 用途 |
|------|------|
| `eslint` | lint |
| `prettier` | format |
| `tsc --noEmit` | 类型检查 |

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

| 类型 | 工具 | 说明 |
|------|------|------|
| 单元测试 | pytest | Repository / Service 逻辑 |
| 集成测试 | pytest + testcontainers | 真实 PG + Redis |
| API 测试 | httpx + pytest | FastAPI 端点 |

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

| 类型 | 工具 |
|------|------|
| 单元测试 | Vitest |
| 组件测试 | Testing Library |
| E2E | Playwright |

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

| 主题 | 文档 |
|------|------|
| 数据模型 | [data-model.md](data-model.md) |
| 架构总览 | [architecture.md](architecture.md) |
| 配置参考 | [config-reference.md](config-reference.md) |
| 部署 | [deployment.md](deployment.md) |
