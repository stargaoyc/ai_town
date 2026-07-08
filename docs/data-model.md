# 数据模型设计

> 本文档定义 AI Town 的全部数据库表结构、ER 关系、索引策略。所有持久化数据统一存储于 PostgreSQL 17 + pgvector，主键使用 UUID v7（时间有序），时间字段统一 TIMESTAMPTZ。

---

## 一、扩展与命名约定

### 1.1 启用扩展

```sql
CREATE EXTENSION IF NOT EXISTS pg_uuidv7;   -- 时间有序 UUID v7 生成
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector 向量检索
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 文本模糊检索
-- 注意: 不再使用 uuid-ossp, 改用 pg_uuidv7 提供 uuidv7()
```

### 1.2 命名约定

| 项 | 约定 |
|----|------|
| 主键 | `id UUID DEFAULT uuidv7()`（UUID v7，时间有序） |
| 时间戳 | `created_at` / `updated_at TIMESTAMPTZ DEFAULT now()` |
| 业务时间 | `timestamp` / `due_at` / `captured_at` 等统一为 `TIMESTAMPTZ` |
| 软删除 | `deleted_at TIMESTAMPTZ`（按需） |
| 灵活字段 | 用 `JSONB`，不用 `JSON` |
| 数组字段 | 用 `TEXT[]` / `UUID[]` |
| 表名 | 复数蛇形（`characters`、`action_records`） |

### 1.3 主键选型说明

使用 **UUID v7**（RFC 9562）而非 UUID v4：
- UUID v4 完全随机 → B-tree 页分裂严重、缓存局部性差、写入性能衰减；
- UUID v7 前 48 位为毫秒时间戳 → 时间单调递增 → 顺序追加 → 索引友好；
- 后 80 位随机 → 防枚举、分布式可独立生成。

详见 [架构设计 - 主键选型](architecture.md#51-主键选型uuid-v7时间有序-uuid)。

---

## 二、ER 关系图

```text
┌──────────────────────┐         ┌──────────────────────┐
│  characters          │◀────────│  relations           │
│  (角色定义)          │  from/to│  (角色关系)          │
└──────────┬───────────┘         └──────────────────────┘
           │ 1:N
           ▼
┌──────────────────────┐         ┌──────────────────────┐
│  character_states    │         │  action_records      │
│  (实时态, Redis 镜像)│         │  (行为历史, 分区)    │
└──────────────────────┘         └──────────┬───────────┘
                                            │ 1:N
           ┌──────────────────────┐         ▼
           │  memory_episodes     │◀────────┘
           │  (向量记忆)          │
           │  embedding vector    │
           └──────────┬───────────┘
                      │ 1:N (source)
                      ▼
           ┌──────────────────────┐         ┌──────────────────────┐
           │  reflections         │         │  plans               │
           │  (反思总结)          │         │  (长期规划)          │
           └──────────────────────┘         └──────────────────────┘

┌──────────────────────┐         ┌──────────────────────┐
│  messages            │         │  module_configs      │
│  (对话历史, 分区)    │         │  (模块配置)          │
└──────────────────────┘         └──────────────────────┘

┌──────────────────────┐         ┌──────────────────────┐
│  world_events        │         │  world_snapshots     │
│  (世界变更事件, 差分) │         │  (世界快照, 冷启动)  │
└──────────────────────┘         └──────────────────────┘
```

---

## 三、表结构 DDL

### 3.1 characters（角色定义）

```sql
CREATE TABLE characters (
    id           UUID PRIMARY KEY DEFAULT uuidv7(),
    name         TEXT NOT NULL,
    age          INT  NOT NULL CHECK (age >= 0 AND age <= 200),
    occupation   TEXT NOT NULL,
    traits       JSONB NOT NULL DEFAULT '{}'::jsonb,      -- 自定义属性（含 personality）
    backstory    TEXT NOT NULL DEFAULT '',
    avatar_url   TEXT,
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','archived','deleted')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_characters_name_trgm ON characters USING gin (name gin_trgm_ops);
CREATE INDEX idx_characters_traits    ON characters USING gin (traits jsonb_path_ops);
CREATE INDEX idx_characters_status    ON characters (status);
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID (v7) | 主键，时间有序 |
| `name` | TEXT | 角色名 |
| `age` | INT | 年龄 |
| `occupation` | TEXT | 职业 |
| `traits` | JSONB | 自定义属性（`personality`/`hobby`/`schedule`/`mbti` 等） |
| `backstory` | TEXT | 背景故事 |
| `avatar_url` | TEXT | 头像 URL |
| `status` | TEXT | `active`/`archived`/`deleted` |

> ⚠️ **0002_optimize 迁移**：`personality TEXT[]` 列已删除，性格标签统一存储在 `traits.personality`（`list[str]`）。角色卡导入时自动合并。

### 3.2 character_states（实时态持久镜像）

> 实时高频读写仍走 Redis，PG 仅作持久镜像与冷启动恢复。

```sql
CREATE TABLE character_states (
    character_id      UUID PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    location          TEXT NOT NULL,
    current_action    TEXT,
    action_started_at TIMESTAMPTZ,                       -- 统一 TIMESTAMPTZ
    energy            INT  NOT NULL DEFAULT 100 CHECK (energy BETWEEN 0 AND 100),
    hunger            INT  NOT NULL DEFAULT 0   CHECK (hunger BETWEEN 0 AND 100),
    mood              TEXT,
    version           INT  NOT NULL DEFAULT 1,           -- 乐观锁版本号（0002_optimize）
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()  -- 自动更新触发器（0002_optimize）
);

-- 启动时: SELECT * FROM character_states; → 灌入 Redis
-- 更新时: ... SET version = version + 1 WHERE version = :old_version
-- updated_at 由 trg_character_states_updated_at 触发器自动更新
```

### 3.3 action_records（行为历史，按月分区）

```sql
CREATE TABLE action_records (
    id                 UUID NOT NULL DEFAULT uuidv7(),
    character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    action_id          TEXT NOT NULL,
    action_name        TEXT NOT NULL,
    params             JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason             TEXT NOT NULL DEFAULT '',
    result             TEXT NOT NULL DEFAULT '',
    duration_minutes   INT  NOT NULL DEFAULT 0,
    location           TEXT,
    related_characters UUID[] NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 分区表主键必须包含分区键
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- 每月一个分区 (示例: 2026-07)
CREATE TABLE action_records_2026_07 PARTITION OF action_records
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

-- ⚠️ 0002_optimize: 无 DEFAULT 分区，PG 原生 "no partition found" 报错足够清晰
-- 分区预创建由 pre_create_partitions() 函数自动处理（应用启动时调用）

CREATE INDEX idx_ar_char_time   ON action_records (character_id, created_at DESC);
CREATE INDEX idx_ar_action      ON action_records (action_id);
CREATE INDEX idx_ar_related     ON action_records USING gin (related_characters);
CREATE INDEX idx_ar_params      ON action_records USING gin (params jsonb_path_ops);
```

### 3.4 memory_episodes（向量记忆，pgvector）

> ⚠️ **性能优化（0002_optimize 迁移）**：表已改为按 `character_id` **HASH 分区**（16 分区，HASH 分区数固定，扩容需全表重分布）。HNSW 索引在**父表**创建，PostgreSQL 自动传播到所有子分区（含未来新增）。查询 `WHERE character_id = :cid` 触发分区裁剪，HNSW 只搜索该角色的数据（< 10ms），避免全局 HNSW + WHERE 过滤导致的召回率崩塌。
>
> ⚠️ **外键修复（v4）**：`character_id` 已建立外键 `REFERENCES characters(id) ON DELETE CASCADE`。PostgreSQL 11+ 支持分区表引用非分区表，角色删除时记忆自动级联清理。

```sql
CREATE TABLE memory_episodes (
    id                 UUID NOT NULL DEFAULT uuidv7(),
    character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
                                                                      -- 分区键（外键引用 characters.id）
    content            TEXT NOT NULL,
    embedding          vector(1536),                        -- nullable: materialized=false 时为 NULL
    importance         INT  NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    timestamp          TIMESTAMPTZ NOT NULL DEFAULT now(),
    action_id          TEXT,
    location           TEXT,
    related_characters UUID[] NOT NULL DEFAULT '{}',
    is_reflected       BOOLEAN NOT NULL DEFAULT FALSE,
    materialized       BOOLEAN NOT NULL DEFAULT FALSE,      -- embedding 是否已生成（异步 worker）
    source_type        TEXT NOT NULL DEFAULT 'action'
                       CHECK (source_type IN ('action','conversation','reflection','event')),
    PRIMARY KEY (id, character_id)                          -- 分区表主键必须含分区键
) PARTITION BY HASH (character_id);

-- 16 个 HASH 分区（MODULUS 16，HASH 分区数固定，扩容到 32 需全表重分布）
CREATE TABLE memory_episodes_p00 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 0);
CREATE TABLE memory_episodes_p01 PARTITION OF memory_episodes FOR VALUES WITH (MODULUS 16, REMAINDER 1);
-- ... p02 ~ p15 同理

-- ⚠️ HNSW 索引在父表创建，PostgreSQL 自动传播到所有子分区（含未来新增）
--    无需手动为每个子分区建索引，避免运维噩梦
--    ef_construction=128 提升图构建精度（原 64 偏低）
CREATE INDEX idx_mem_embedding_hnsw ON memory_episodes
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- 辅助索引（同样在父表创建，自动传播）
CREATE INDEX idx_mem_char_time ON memory_episodes (character_id, timestamp DESC);
CREATE INDEX idx_mem_char_imp  ON memory_episodes (character_id, importance DESC);
CREATE INDEX idx_mem_related   ON memory_episodes USING gin (related_characters);
CREATE INDEX idx_mem_unreflected ON memory_episodes (character_id) WHERE is_reflected = FALSE;
CREATE INDEX idx_mem_unmaterialized ON memory_episodes (timestamp) WHERE materialized = FALSE;  -- embedding worker

-- 查询时调优（分区后可适当提高，单分区数据量小）
-- SET hnsw.ef_search = 100;
```

| 字段 | 变更 | 说明 |
|------|------|------|
| `character_id` | 移除 FK | 分区表不支持 FK 引用，由应用层 ORM 保证 |
| `embedding` | 改为 nullable | `materialized=false` 时为 NULL |
| `materialized` | **新增** | embedding 是否已生成（异步 worker 处理） |
| `PRIMARY KEY` | 改为复合 | `(id, character_id)` 分区表要求 |

> **异步 embedding**：记忆写入时 `materialized=false, embedding=NULL`，不阻塞 Tick 循环。后台 [EmbeddingWorker](../packages/backend/src/memory/embedding_worker.py) 批量拉取并生成向量。

### 3.5 reflections（反思总结）

```sql
CREATE TABLE reflections (
    id                 UUID PRIMARY KEY DEFAULT uuidv7(),
    character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    summary            TEXT NOT NULL,                    -- "我习惯早睡早起"
    detail             TEXT NOT NULL DEFAULT '',
    source_memory_ids  UUID[] NOT NULL DEFAULT '{}',     -- DEPRECATED: 使用 reflection_sources 中间表
    importance         INT  NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    embedding          vector(1536),                     -- 反思向量(可选, 高层语义检索)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_refl_char_time ON reflections (character_id, created_at DESC);
CREATE INDEX idx_refl_embedding_hnsw
    ON reflections USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);                -- ef_construction 同步提升
```

#### 3.5.1 reflection_sources（反思来源中间表）

> ⚠️ **改进（0002_optimize v3 迁移）**：替代 `reflections.source_memory_ids UUID[]`，解决数组无法建立外键约束的问题。
> v3 修复：增加 `memory_character_id` 字段，与 `memory_id` 组成**复合外键**引用 `memory_episodes(id, character_id) ON DELETE CASCADE`，真正保证参照完整性。

```sql
CREATE TABLE reflection_sources (
    reflection_id        UUID NOT NULL REFERENCES reflections(id) ON DELETE CASCADE,
    memory_id            UUID NOT NULL,                    -- memory_episodes.id
    memory_character_id  UUID NOT NULL,                    -- memory_episodes.character_id（分区键）
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (reflection_id, memory_id, memory_character_id),
    -- 复合外键：引用分区表 memory_episodes 的完整主键
    FOREIGN KEY (memory_id, memory_character_id)
        REFERENCES memory_episodes(id, character_id) ON DELETE CASCADE
);

CREATE INDEX idx_refl_sources_memory ON reflection_sources (memory_id, memory_character_id);
```

### 3.6 plans（长期规划）

```sql
CREATE TABLE plans (
    id            UUID PRIMARY KEY DEFAULT uuidv7(),
    character_id  UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,                         -- "三个月内学会咖啡拉花"
    horizon       TEXT NOT NULL CHECK (horizon IN ('daily','weekly','monthly','quarterly','yearly')),
    steps         JSONB NOT NULL DEFAULT '[]'::jsonb,    -- [{...},{...}]
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','completed','abandoned','paused')),
    priority      INT  NOT NULL DEFAULT 5,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    due_at        TIMESTAMPTZ
);

CREATE INDEX idx_plans_char_status ON plans (character_id, status);
CREATE INDEX idx_plans_due         ON plans (due_at) WHERE status = 'active';
```

### 3.7 relations（角色关系）

```sql
CREATE TABLE relations (
    from_id    UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    to_id      UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    strength   INT  NOT NULL DEFAULT 0 CHECK (strength BETWEEN -100 AND 100),
    tags       TEXT[] NOT NULL DEFAULT '{}',             -- ["朋友","同学"]
    metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_id, to_id),
    CHECK (from_id <> to_id)
);

CREATE INDEX idx_rel_to       ON relations (to_id, strength DESC);
CREATE INDEX idx_rel_tags     ON relations USING gin (tags);
CREATE INDEX idx_rel_metadata ON relations USING gin (metadata jsonb_path_ops);
```

### 3.8 messages（对话历史，按月分区）

```sql
CREATE TABLE messages (
    id              UUID NOT NULL DEFAULT uuidv7(),
    conversation_id UUID NOT NULL,                       -- 会话 ID (无外键, 会话为逻辑概念)
    character_id    UUID REFERENCES characters(id) ON DELETE SET NULL,
    user_id         TEXT,                                -- 平台用户标识
    platform        TEXT NOT NULL CHECK (platform IN ('web','qq','lark','api')),
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 平台特定字段
    tokens          INT,
    cost            NUMERIC(10,6),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE messages_2026_07 PARTITION OF messages
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

-- ⚠️ 0002_optimize: 无 DEFAULT 分区，PG 原生报错足够清晰，由 pre_create_partitions() 自动预创建

CREATE INDEX idx_msg_conv_time ON messages (conversation_id, created_at);
CREATE INDEX idx_msg_char_time ON messages (character_id, created_at DESC);
CREATE INDEX idx_msg_user_time ON messages (user_id, created_at DESC);

-- ⚠️ 覆盖索引（0002_optimize）：仅包含轻量字段，content 走主键回表
--    原 INCLUDE(content) 会导致索引膨胀（content 可能 2000 字）
CREATE INDEX idx_msg_user_time_cover
    ON messages (user_id, created_at DESC) INCLUDE (role, platform);
CREATE INDEX idx_msg_conv_time_cover
    ON messages (conversation_id, created_at) INCLUDE (role, character_id, platform);

CREATE INDEX idx_msg_created_brin ON messages USING brin (created_at);
```

### 3.9 module_configs（模块配置）

```sql
CREATE TABLE module_configs (
    id                   UUID PRIMARY KEY DEFAULT uuidv7(),
    name                 TEXT NOT NULL UNIQUE,
    type                 TEXT NOT NULL CHECK (type IN ('mcp','local','skill')),
    enabled              BOOLEAN NOT NULL DEFAULT FALSE,
    config               JSONB NOT NULL DEFAULT '{}'::jsonb,
    dependencies         TEXT[] NOT NULL DEFAULT '{}',
    mcp_server_url       TEXT,
    health_check_status  TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (health_check_status IN ('healthy','unhealthy','unknown')),
    last_check_at        TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_module_enabled ON module_configs (enabled) WHERE enabled = TRUE;
```

### 3.10 world_events（世界变更事件，差分记录）

> **事件溯源 + 定期快照架构（v3 修复）**：
> - `world_events`：差分事件（高频，仅状态变化时写入），每 N Tick 记录各维度变更
> - `world_snapshots`：完整状态快照（低频，每 1000 Tick 存一次），冷启动恢复用
> - 冷启动恢复：加载最新快照 → 回放之后的增量事件 → 恢复状态（启动时间恒定）
>
> ⚠️ **幂等性保证（v4 修复）**：`UNIQUE(tick_id, event_type)` 约束保证单 Tick 单类型事件唯一。
> `add_batch` 使用 `INSERT ... ON CONFLICT DO NOTHING`，服务重启 / Tick 重试时自动跳过已存在事件。

```sql
CREATE TABLE world_events (
    id          UUID PRIMARY KEY DEFAULT uuidv7(),
    tick_id     BIGINT NOT NULL,
    event_type  TEXT NOT NULL,                           -- time/weather/scene/resource/event
    payload     JSONB NOT NULL,                          -- 变更内容（仅差分）
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tick_id, event_type)                         -- 幂等约束：单 Tick 单类型事件唯一
);

CREATE INDEX idx_world_events_tick      ON world_events (tick_id);
CREATE INDEX idx_world_events_type_time ON world_events (event_type, created_at DESC);
```

### 3.11 world_snapshots（世界快照，冷启动恢复）

> 每 1000 Tick 存一次完整世界状态快照。冷启动时从最新快照开始，仅回放之后的增量事件。

```sql
-- 表结构在 0001_init 中创建，0002_optimize v3 保留（v2 曾误删）
CREATE TABLE world_snapshots (
    id             UUID PRIMARY KEY DEFAULT uuidv7(),
    tick_id        BIGINT NOT NULL,                      -- 快照对应的 Tick 序号
    world_time     TIMESTAMPTZ,                          -- 虚拟世界时间
    weather        TEXT,                                 -- 天气状态
    locations      JSONB,                                -- 所有场景状态
    resources      JSONB,                                -- 资源状态
    active_events  JSONB,                                -- 活跃事件列表
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_world_tick ON world_snapshots (tick_id);
```

| event_type | payload 示例 |
|------------|-------------|
| `time` | `{"virtual_time": "2026-07-06T10:30:00", "tick_id": 42}` |
| `weather` | `{"from": "sunny", "to": "rainy"}` |
| `scene` | `{"scene_id": "cafe", "crowdedness": 0.8}` |
| `resource` | `{"resource": "coffee_beans", "delta": -5}` |
| `event` | `{"event_id": "sakura_festival", "action": "start"}` |

---

## 四、索引策略汇总

### 4.1 索引清单

| 检索场景 | 索引 | 说明 |
|----------|------|------|
| 角色名模糊搜索 | `gin (name gin_trgm_ops)` | pg_trgm 支持相似度 |
| 角色按 traits 筛选 | `gin (traits jsonb_path_ops)` | JSONB 路径查询 |
| 角色记忆向量召回 | `hnsw (embedding vector_cosine_ops)` | HNSW，生产首选 |
| 角色内记忆按时间 | `(character_id, timestamp DESC)` | 范围扫描 |
| 未反思记忆查找 | `(character_id) WHERE is_reflected=FALSE` | 部分索引，反思专用 |
| 角色行为历史 | `(character_id, created_at DESC)` | 分区裁剪 + 索引 |
| 关联角色反查 | `gin (related_characters)` | 数组成员查询 |
| 关系图遍历 | `(from_id)` PK + `(to_id)` 索引 | 双向查询 |
| 消息会话拉取 | `(conversation_id, created_at)` | 多轮对话上下文 |
| 消息时间扫描 | `btree (created_at)` | 按月分区裁剪后 B-tree 足够（不使用 BRIN） |

### 4.2 索引设计原则

1. **主键索引**：UUID v7 时间有序，B-tree 顺序追加，无碎片化；
2. **复合索引字段顺序**：等值查询字段在前，范围查询字段在后；
3. **部分索引**：高频过滤条件用 `WHERE` 缩小索引体积（如 `WHERE enabled=TRUE`、`WHERE is_reflected=FALSE`）；
4. **GIN 索引**：JSONB、数组、全文检索场景；
5. **不使用 BRIN 索引**：按月范围分区已通过分区裁剪限制扫描范围，BRIN 在单月千万级以内数据无收益，反而增加写入维护开销；
6. **HNSW 索引**：向量检索，优于 IVFFlat（无需训练、增量友好）。

---

## 五、向量检索 SQL 范式

### 5.1 基础 Top-K 检索

```sql
SELECT id, content, importance, timestamp,
       1 - (embedding <=> :q_vec) AS sim_score
FROM memory_episodes
WHERE character_id = :cid
ORDER BY embedding <=> :q_vec
LIMIT :top_k;
```

### 5.2 混合检索（向量 + 时间衰减 + 重要度）

```sql
WITH candidates AS (
    SELECT id, content, importance, timestamp,
           1 - (embedding <=> :q_vec) AS sim_score
    FROM memory_episodes
    WHERE character_id = :cid
    ORDER BY embedding <=> :q_vec
    LIMIT :top_k * 3
)
SELECT id, content,
       sim_score * 0.6
       + importance * 0.05
       + EXTRACT(EPOCH FROM (now() - timestamp)) / 86400.0 * (-0.05) AS final_score
FROM candidates
ORDER BY final_score DESC
LIMIT :top_k;
```

### 5.3 反思层检索

```sql
SELECT id, summary
FROM reflections
WHERE character_id = :cid
ORDER BY embedding <=> :q_vec
LIMIT 5;
```

### 5.4 联合检索（应用层合并）

```sql
-- 原始记忆 + 反思 UNION ALL, 应用层按 final_score 排序
SELECT 'memory' AS kind, id, content AS text, embedding <=> :q_vec AS dist
FROM memory_episodes WHERE character_id = :cid
UNION ALL
SELECT 'reflection', id, summary, embedding <=> :q_vec
FROM reflections WHERE character_id = :cid
ORDER BY dist LIMIT :top_k;
```

### 5.5 HNSW 查询时调优

```sql
-- 会话级调整搜索宽度 (越大召回越高, 延迟越大)
SET LOCAL hnsw.ef_search = 80;
SELECT ... FROM memory_episodes ORDER BY embedding <=> :q_vec LIMIT 10;
```

---

## 六、分区表维护

### 6.1 滚动新建分区

`action_records` 与 `messages` 按月分区，需预创建未来 12 个月分区：

```sql
-- 示例: 预创建 2026-08 分区
CREATE TABLE action_records_2026_08 PARTITION OF action_records
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE messages_2026_08 PARTITION OF messages
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
```

### 6.2 自动化方案

| 方案 | 说明 |
|------|------|
| `pg_cron` 扩展 | 每月 1 日定时执行 `CREATE TABLE ... PARTITION OF` |
| 应用层定时任务 | Python `apscheduler` 每月 25 日预创建下月分区 |
| ⚠️ 无 DEFAULT 分区 | 0002_optimize 已删除 DEFAULT 分区，PG 原生 "no partition found" 报错足够清晰。分区预创建由 `pre_create_partitions()` 函数自动处理 |

### 6.3 历史分区归档

- 超过 1 年的分区可 detach 后导出到对象存储（Parquet 格式）；
- PG 内仅保留近 1 年数据用于在线查询。

```sql
-- 归档旧分区
ALTER TABLE action_records DETACH PARTITION action_records_2025_07;
-- 导出后 DROP
DROP TABLE action_records_2025_07;
```

---

## 七、容量估算

| 表 | 单角色/月增量 | 50 角色年增量 | 说明 |
|----|---------------|---------------|------|
| `action_records` | ~3 万 | ~1800 万 | 按 30s/Tick, 每角色每 Tick 1 条 |
| `memory_episodes` | ~3 万 | ~1800 万 | 与 action_records 1:1 |
| `messages` | 视用户量 | — | 分区表承载 |
| `reflections` | ~50 | ~3 万 | 反思稀疏 |
| `relations` | 稳定 | ~2500 行 | 50 角色两两组合 |

PG 在 HNSW + 分区表下，单表千万级行检索 p95 < 50ms（HNSW 检索 + 过滤）。

---

## 八、ORM 模型示例

### 8.1 SQLAlchemy 2.0 风格

```python
# db/models/memory_episode.py
from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Integer, Boolean, Index, DateTime, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7                                  # 应用层兜底
from .base import Base

class MemoryEpisode(Base):
    __tablename__ = "memory_episodes"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7                  # 应用层生成 UUID v7
    )
    # character_id 外键引用 characters(id) ON DELETE CASCADE
    # PostgreSQL 11+ 支持分区表引用非分区表，角色删除时记忆自动级联清理
    character_id: Mapped[UUID] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(String)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))
    importance: Mapped[int] = mapped_column(Integer, default=5)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action_id: Mapped[str | None]
    location: Mapped[str | None]
    related_characters: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), default=list)
    is_reflected: Mapped[bool] = mapped_column(Boolean, default=False)
    materialized: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String, default="action")

    __table_args__ = (
        Index("idx_mem_char_time", "character_id", timestamp.desc()),
        Index("idx_mem_unreflected", "character_id")
            .where(is_reflected == False),               # 部分索引
        # HNSW 索引通过 alembic 迁移用 raw SQL 创建
    )
```

### 8.2 alembic 创建 HNSW 索引与扩展

HNSW 索引与 pg_uuidv7 扩展不能通过 ORM `Index` 自动生成，需在 alembic 升级脚本中用 `op.execute()` 写原生 SQL：

```python
# migrations/versions/xxxx_init.py
def upgrade():
    # 1. 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_uuidv7;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2. 建表 (使用 uuidv7() 默认值)
    op.create_table(
        "memory_episodes",
        sa.Column("id", sa.UUID, primary_key=True,
                  server_default=sa.text("uuidv7()")),
        # ... 其他字段 ...
    )

    # 3. HNSW 向量索引（父表创建，自动传播到所有子分区）
    op.execute(
        "CREATE INDEX idx_mem_embedding_hnsw ON memory_episodes "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 128);"
    )

    # 4. 部分索引
    op.execute(
        "CREATE INDEX idx_mem_unreflected ON memory_episodes (character_id) "
        "WHERE is_reflected = FALSE;"
    )

def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_mem_embedding_hnsw;")
    op.execute("DROP INDEX IF EXISTS idx_mem_unreflected;")
    op.drop_table("memory_episodes")
```

---

## 九、相关文档

| 主题 | 文档 |
|------|------|
| 架构决策（UUID v7 / HNSW / 连接池） | [architecture.md](architecture.md) |
| 记忆系统 | [memory-system.md](memory-system.md) |
| 开发指南（Repository 模式） | [development-guide.md](development-guide.md) |
