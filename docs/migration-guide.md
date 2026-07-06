# 迁移指南：从 MongoDB 到 PostgreSQL

> 本文档描述如何将 AI Town 的持久化层从 MongoDB（+ 独立向量库）迁移到 PostgreSQL 17 + pgvector。适用于已有 Mongo 历史数据的项目；新项目可直接按 [数据模型设计](data-model.md) 建库。

---

## 一、迁移目标与范围

### 1.1 目标

| 项 | 迁移前 | 迁移后 |
|----|--------|--------|
| 主数据库 | MongoDB | PostgreSQL 17 + pgvector |
| 向量存储 | Chroma / Milvus（独立） | pgvector（统一在 PG） |
| 异步驱动 | motor | asyncpg + SQLAlchemy 2.0 |
| Schema 演进 | 无强约束 | alembic 版本化 |
| 事务 | 跨库无事务 | 单 PG 事务 |

### 1.2 涉及集合 → 表

| MongoDB 集合 | PostgreSQL 表 | 备注 |
|--------------|---------------|------|
| `Character` | `characters` | `traits` 用 JSONB |
| `ActionRecord` | `action_records` | 按月分区 |
| `Reflection` | `reflections` | — |
| `Plan` | `plans` | `steps` 用 JSONB |
| `Message` | `messages` | 按月分区 |
| `Relation` | `relations` | 复合主键 |
| `MemoryEpisode`（原 Chroma/Milvus） | `memory_episodes` | `embedding vector(1536)` |
| `ModuleConfig`（原 PG） | `module_configs` | 已是 PG，无需迁移 |

---

## 二、迁移策略

### 2.1 总体策略

采用**双写期 → 校验 → 切流 → 下线**四阶段：

```text
① 双写期 (1-2 周)
   - 应用同时写 Mongo 和 PG
   - 历史数据批量迁移
   - 读仍走 Mongo
        ↓
② 校验期 (3-5 天)
   - 对比 Mongo 与 PG 行数、关键 ID 抽样比对
   - 修复差异
        ↓
③ 切流 (灰度 → 全量)
   - 读切到 PG (灰度 10% → 50% → 100%)
   - 写保留双写作为兜底
        ↓
④ 下线 Mongo
   - 停止 Mongo 写入
   - 观察 1 周无回滚后下线 Mongo
```

### 2.2 风险与对策

| 风险 | 对策 |
|------|------|
| 向量需重新生成 | Mongo 中无原始向量时调 embedding API 重算 |
| Schema 漂移导致字段缺失 | 迁移前用脚本扫描 Mongo 字段分布，DDL 兜底默认值 |
| 双写性能下降 | 双写期可临时降低 Tick 频率 |
| 切流出错 | 保留 Mongo 7 天只读副本，支持快速回滚 |

---

## 三、迁移前准备

### 3.1 PG 初始化

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
```

### 3.2 执行表结构迁移

```bash
cd packages/backend
alembic upgrade head
```

DDL 详见 [数据模型设计](data-model.md)。

### 3.3 预创建分区

`action_records` 与 `messages` 按月分区，需覆盖历史数据所在月份：

```sql
-- 假设历史数据从 2025-01 开始
CREATE TABLE action_records_2025_01 PARTITION OF action_records
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
-- ... 依次创建到当前月
```

可编写脚本批量生成。

---

## 四、迁移脚本

### 4.1 脚本结构

```text
scripts/migrate_mongo_to_pg/
├── __init__.py
├── main.py                  # 入口, 按依赖顺序调度
├── migrate_characters.py
├── migrate_relations.py
├── migrate_action_records.py
├── migrate_memory_episodes.py   # 含向量重算
├── migrate_reflections.py
├── migrate_plans.py
├── migrate_messages.py
└── verify.py                # 行数与抽样校验
```

### 4.2 迁移顺序

按外键依赖顺序：

```text
characters → relations → action_records → memory_episodes
                                    ↓
                              reflections → plans
messages (独立) → module_configs (已是 PG, 跳过)
```

### 4.3 主入口

```python
# scripts/migrate_mongo_to_pg/main.py
import asyncio
from migrate_characters import migrate_characters
from migrate_relations import migrate_relations
from migrate_action_records import migrate_action_records
from migrate_memory_episodes import migrate_memory_episodes
from migrate_reflections import migrate_reflections
from migrate_plans import migrate_plans
from migrate_messages import migrate_messages
from verify import verify_all

async def main():
    # 1. 结构化数据 (无向量)
    await migrate_characters()           # 必须先迁移
    await migrate_relations()
    await migrate_action_records()       # 依赖 characters
    await migrate_plans()
    await migrate_messages()

    # 2. 向量数据 (可能需重算 embedding)
    await migrate_memory_episodes()      # 依赖 characters
    await migrate_reflections()          # 依赖 characters

    # 3. 校验
    await verify_all()

if __name__ == "__main__":
    asyncio.run(main())
```

### 4.4 Character 迁移示例

```python
# scripts/migrate_mongo_to_pg/migrate_characters.py
from motor.motor_asyncio import AsyncIOMotorClient
from db.session import DB
from db.models.character import Character
import uuid

async def migrate_characters():
    mongo = AsyncIOMotorClient(MONGO_URL).ai_town.characters
    pg = DB(DATABASE_URL)

    cursor = mongo.find({})
    batch = []
    async for doc in cursor:
        ch = Character(
            id=uuid.UUID(str(doc["_id"])) if is_valid_uuid(doc["_id"]) else uuid.uuid4(),
            name=doc["name"],
            age=doc.get("age", 0),
            occupation=doc.get("occupation", ""),
            personality=doc.get("personality", []),
            traits=doc.get("traits", {}),     # JSONB
            backstory=doc.get("backstory", ""),
            avatar_url=doc.get("avatar_url"),
            status=doc.get("status", "active"),
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at"),
        )
        batch.append(ch)
        if len(batch) >= 1000:
            await flush(pg, batch)
            batch = []
    if batch:
        await flush(pg, batch)

async def flush(pg, batch):
    async with pg.session() as s:
        s.add_all(batch)
        # COPY 模式更快: await s.execute(insert(Character), [b.__dict__ for b in batch])
```

### 4.5 MemoryEpisode 迁移（含向量重算）

```python
# scripts/migrate_mongo_to_pg/migrate_memory_episodes.py
from openai import AsyncOpenAI
from db.session import DB
from db.models.memory_episode import MemoryEpisode

client = AsyncOpenAI()

async def embed(text: str) -> list[float]:
    resp = await client.embeddings.create(
        model="text-embedding-3-small", input=text
    )
    return resp.data[0].embedding

async def migrate_memory_episodes():
    # 若原数据在 Chroma/Milvus, 用其 SDK 拉取
    # 若原数据在 Mongo 且无向量, 按 content 重算
    pg = DB(DATABASE_URL)
    async for doc in mongo_or_vector_db.iter():
        if doc.get("embedding"):
            vec = doc["embedding"]
        else:
            vec = await embed(doc["content"])   # 重算

        ep = MemoryEpisode(
            character_id=map_id(doc["character_id"]),
            content=doc["content"],
            embedding=vec,
            importance=doc.get("importance", 5),
            timestamp=doc["timestamp"],
            action_id=doc.get("action_id"),
            location=doc.get("location"),
            related_characters=[map_id(x) for x in doc.get("related_characters", [])],
            source_type=doc.get("source_type", "action"),
        )
        # 批量写入...
```

### 4.6 校验脚本

```python
# scripts/migrate_mongo_to_pg/verify.py
async def verify_all():
    await verify_count("characters")
    await verify_count("action_records")
    await verify_count("memory_episodes")
    await verify_count("messages")
    await verify_sample("characters", sample_size=100)
    await verify_sample("memory_episodes", sample_size=50, check_embedding=True)

async def verify_count(table):
    mongo_count = await mongo_count_for(table)
    pg_count = await pg_count_for(table)
    if mongo_count != pg_count:
        log.error(f"{table}: Mongo={mongo_count} PG={pg_count} MISMATCH")
    else:
        log.info(f"{table}: count={pg_count} OK")
```

---

## 五、应用层切换

### 5.1 双写期

```python
# 双写包装: 同时写 Mongo 和 PG
class DualWriteCharacterRepo:
    def __init__(self, mongo_repo, pg_repo):
        self.mongo = mongo_repo
        self.pg = pg_repo

    async def create(self, ch):
        await self.mongo.create(ch)      # 旧路径
        try:
            await self.pg.create(ch)     # 新路径
        except Exception as e:
            log.error(f"PG write failed: {e}")   # 不阻塞主流程
```

### 5.2 切流（读）

通过配置开关切换读源：

```yaml
# config.yaml
migration:
  read_source: pg        # mongo | pg | dual_compare
  dual_write: true
```

`dual_compare` 模式同时读两边并对比，用于发现差异。

### 5.3 切流（写）

确认读无问题后，关闭 Mongo 写入：

```yaml
migration:
  read_source: pg
  dual_write: false      # 停止 Mongo 写入
```

观察 1 周无回滚后下线 Mongo 连接配置。

---

## 六、向量库切换

### 6.1 从 Chroma/Milvus 迁到 pgvector

若原向量库为 Chroma：

```python
import chromadb
client = chromadb.PersistentClient(path="./chroma")

for collection_name in ["memory_episodes", "reflections"]:
    collection = client.get_collection(collection_name)
    data = collection.get(include=["embeddings", "documents", "metadatas"])
    # 写入 PG memory_episodes / reflections
```

若原向量库为 Milvus：

```python
from pymilvus import Collection
coll = Collection("memory_episodes")
coll.load()
for batch in coll.query(iterator=True, batch_size=1000, output_fields=["*"]):
    # 写入 PG
```

### 6.2 抽象层保证可回切

`MemoryRepository` 已抽象，若未来需切回独立向量库，仅需替换实现类：

```python
class MemoryRepository(ABC):
    async def add(self, ep): ...
    async def search_similar(self, cid, vec, top_k): ...

class PGMemoryRepository(MemoryRepository): ...   # 当前默认
class MilvusMemoryRepository(MemoryRepository): ...  # 未来可选
```

### 6.3 何时该切回独立向量库

满足任一条件时建议切回：

- 单角色记忆数 > 500 万，或总记忆数 > 1 亿；
- HNSW 索引构建内存占用超过 PG `shared_buffers` 50%；
- 检索 p95 > 200ms 且调参无效。

---

## 七、回滚方案

### 7.1 双写期回滚

仅需将读源切回 `mongo`：

```yaml
migration:
  read_source: mongo
  dual_write: true
```

PG 数据保留，不影响 Mongo。

### 7.2 切流后回滚

若已停止 Mongo 写入（`dual_write: false`）后需回滚：

1. 重新开启 `dual_write: true`；
2. 用 PG 中切流后新增的数据反向同步回 Mongo（脚本）；
3. 读源切回 `mongo`。

### 7.3 下线后回滚

Mongo 已下线则无法回滚。**下线前必须确保观察期无问题**。

---

## 八、迁移后验证清单

- [ ] PG 各表行数与 Mongo 对齐
- [ ] 抽样 100 条 Character 字段内容一致
- [ ] 抽样 50 条 MemoryEpisode 向量维度 = 1536
- [ ] HNSW 索引已创建，检索延迟 < 50ms
- [ ] 角色决策链路端到端通过（手动触发 Tick）
- [ ] 消息收发正常（QQ/飞书/Web）
- [ ] 可观测性 Trace 链路完整
- [ ] 备份策略覆盖 PG（每日全量 + WAL 归档）
- [ ] 双写期至少运行 7 天无差异

---

## 九、相关文档

| 主题 | 文档 |
|------|------|
| 数据模型 DDL | [data-model.md](data-model.md) |
| 去 Mongo 架构决策 | [architecture.md](architecture.md#五关键架构决策去除-mongodb) |
| 开发指南 | [development-guide.md](development-guide.md) |
| 部署 | [deployment.md](deployment.md) |
