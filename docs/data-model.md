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
│  (角色定义)          │ char/tgt│  (角色关系, 有向图)  │
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
    id            UUID PRIMARY KEY DEFAULT uuidv7(),
    name          TEXT NOT NULL,
    age           INT,
    occupation    TEXT,
    traits        JSONB,                                    -- 自定义属性（含 personality，ORM 层 default={}）
    backstory     TEXT,
    avatar_url    TEXT,
    voice_preset  TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,            -- 是否参与世界运行
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()        -- 0002_optimize 新增，触发器自动维护
);

CREATE INDEX idx_characters_name_trgm ON characters USING gin (name gin_trgm_ops);
CREATE INDEX idx_characters_traits    ON characters USING gin (traits jsonb_path_ops);
CREATE INDEX idx_characters_active    ON characters (is_active) WHERE is_active = TRUE;
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID (v7) | 主键，时间有序 |
| `name` | TEXT | 角色名 |
| `age` | INT | 年龄（可空） |
| `occupation` | TEXT | 职业（可空） |
| `traits` | JSONB | 自定义属性（`personality`/`hobby`/`schedule`/`mbti` 等） |
| `backstory` | TEXT | 背景故事（可空） |
| `avatar_url` | TEXT | 头像 URL |
| `voice_preset` | TEXT | 语音预设 |
| `is_active` | BOOLEAN | 是否参与世界运行（true/false） |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `updated_at` | TIMESTAMPTZ | 更新时间（触发器自动维护） |

> ⚠️ **0002_optimize 迁移**：`personality` 列已删除，性格标签统一存储在 `traits.personality`（`list[str]`）。`updated_at` 字段+触发器新增。

### 3.2 character_states（实时态持久镜像）

> 实时高频读写仍走 Redis，PG 仅作持久镜像与冷启动恢复。

```sql
CREATE TABLE character_states (
    character_id      UUID PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    location          TEXT,                               -- 当前场景 ID
    stamina           INT  NOT NULL DEFAULT 80,           -- 体力 0-100
    satiety           INT  NOT NULL DEFAULT 60,           -- 饱腹度 0-100
    mood              TEXT,                               -- 情绪（happy/calm/sad/anxious 等）
    money             INT  NOT NULL DEFAULT 500,          -- 金钱
    inventory         JSONB,                              -- 物品栏（ORM 层 default={}）
    current_action    JSONB,                              -- 当前动作 {action_id, params, end_time}
    phone_battery     INT  NOT NULL DEFAULT 75,           -- 手机电量 0-100
    social_energy     INT  NOT NULL DEFAULT 60,           -- 社交能量 0-100
    version           INT  NOT NULL DEFAULT 1,            -- 乐观锁版本号（0002_optimize）
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()  -- 自动更新触发器（0002_optimize）
);

-- 启动时: SELECT * FROM character_states; → 灌入 Redis
-- 更新时: ... SET version = version + 1 WHERE version = :old_version
-- updated_at 由通用 update_updated_at() 触发器自动更新
-- fillfactor=85 + autovacuum 调优（0002_optimize v4）
```

### 3.3 action_records（行为历史，按月分区）

```sql
CREATE TABLE action_records (
    id                 UUID NOT NULL DEFAULT uuidv7(),
    character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    action_id          TEXT,
    action_name        TEXT,
    params             JSONB,
    reason             TEXT,
    result             TEXT,
    duration_minutes   INT,
    location           TEXT,
    related_characters JSONB,                              -- 相关角色 ID 列表（JSONB，非 UUID[]）
    timestamp          TIMESTAMPTZ NOT NULL DEFAULT now(), -- 执行时间（分区键）
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- 每月一个分区 (示例: 2026-07)
CREATE TABLE action_records_2026_07 PARTITION OF action_records
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

-- ⚠️ 0002_optimize: 无 DEFAULT 分区，PG 原生 "no partition found" 报错足够清晰
-- 分区预创建由 pre_create_partitions() 函数自动处理（应用启动时调用）

CREATE INDEX idx_ar_char_time   ON action_records (character_id, timestamp DESC);
CREATE INDEX idx_ar_action      ON action_records (action_id);
-- ⚠️ related_characters 为 JSONB 非 UUID[]，不能直接 GIN 索引
-- 如需数组查询，需改为 UUID[] 类型或使用 JSONB GIN 索引
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
    fail_count         INT  NOT NULL DEFAULT 0,             -- v3 新增：向量化失败次数，达 5 后熔断
    last_error         TEXT,                                -- v3 新增：最近失败错误信息（截断 1000 字）
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
-- v3: 排除 fail_count >= 5 的熔断记忆，避免 worker 反复拉取
CREATE INDEX idx_mem_unmaterialized ON memory_episodes (timestamp)
    WHERE materialized = FALSE AND fail_count < 5;

-- 查询时调优（分区后可适当提高，单分区数据量小）
-- SET hnsw.ef_search = 100;
```

| 字段 | 变更 | 说明 |
|------|------|------|
| `character_id` | 保留 FK | 外键 `REFERENCES characters(id) ON DELETE CASCADE`，PG 11+ 支持分区表引用非分区表（v4 修复 v3 误判） |
| `embedding` | 改为 nullable | `materialized=false` 时为 NULL |
| `materialized` | **新增** | embedding 是否已生成（异步 worker 处理） |
| `PRIMARY KEY` | 改为复合 | `(id, character_id)` 分区表要求 |

> **异步 embedding**：记忆写入时 `materialized=false, embedding=NULL`，不阻塞 Tick 循环。后台 [EmbeddingWorker](../packages/backend/src/memory/embedding_worker.py) 批量拉取并生成向量。

### 3.5 reflections（反思总结）

```sql
CREATE TABLE reflections (
    id                 UUID PRIMARY KEY DEFAULT uuidv7(),
    character_id       UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    content            TEXT NOT NULL,                    -- 反思内容
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ⚠️ related_episodes 字段已在 0002_optimize v5 迁移中删除
--    关联记忆通过 reflection_sources 中间表管理（复合外键 ON DELETE CASCADE）

CREATE INDEX idx_refl_char_time ON reflections (character_id, created_at DESC);
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
    type          TEXT,                                   -- 计划类型：long_term/short_term
    title         TEXT,                                   -- 计划标题
    description   TEXT,                                   -- 计划描述
    status        TEXT,                                   -- active/completed/abandoned（ORM 层 default='active'）
    priority      INT  NOT NULL DEFAULT 3,                -- 优先级 1-5
    deadline      TIMESTAMPTZ,                            -- 截止时间
    progress      INT  NOT NULL DEFAULT 0,                -- 进度 0-100
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()      -- 0002_optimize 新增，触发器自动维护
);

CREATE INDEX idx_plans_char_status ON plans (character_id, status);
```

### 3.7 relations（角色关系）

> 有向图：双向关系需两条记录（A→B 和 B→A）。`strength` 范围 0-100，配合 `relationship_type` 描述关系层级。

```sql
CREATE TABLE relations (
    character_id        UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    target_id           UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    strength            INT  NOT NULL DEFAULT 20,                 -- 关系强度 0-100
    relationship_type   TEXT NOT NULL DEFAULT 'stranger',         -- stranger/acquaintance/friend/close_friend/best_friend
    last_interaction_at TIMESTAMPTZ,                               -- 最后互动时间（衰减计算）
    notes               TEXT,                                      -- LLM 总结的对该角色的认知
    PRIMARY KEY (character_id, target_id)
);
```

### 3.8 messages（对话历史，非分区表）

> ⚠️ **Phase 3 设计变更（v3 迁移）**：原设计为按月分区表，实际实现改为**非分区表**（方案 B 对齐 ORM）。
> 原因：
> - messages 通过 `conversation_id` 外键引用 conversations，分区表 FK 受限
> - 单表 + (conversation_id, created_at) 复合索引足够支撑查询
> - 单表 3 个月内千万级数据 PostgreSQL B-tree 完全可承载
> - 长期归档由 Phase 4 冷热分离方案处理
>
> 与原设计差异：
> - 取消 `PARTITION BY RANGE (created_at)` 与月度分区
> - 取消 `idx_msg_user_time_cover` / `idx_msg_conv_time_cover` 覆盖索引（非分区表无收益）
> - `role` 字段改名为 `sender`（与 ORM 模型一致，覆盖 user/character/system 三种）
> - `metadata` 字段改名为 `extra_data`（与 ORM 模型一致）
> - 新增 `conversation_id` 外键引用 conversations(id) ON DELETE CASCADE（替代无外键的逻辑概念）
> - 取消 character_id / user_id / platform 字段（提升至 conversations 表）

```sql
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender          VARCHAR(20) NOT NULL,                 -- user/character/system
    content         TEXT NOT NULL,
    tokens          INT,                                  -- LLM token 消耗（成本追踪）
    cost            NUMERIC(10, 6),                       -- 调用费用 USD（Phase 3.5 熔断依赖）
    extra_data      JSONB,                                -- 附加信息（reply_to/attachments 等）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 会话时间线查询（核心索引）
CREATE INDEX idx_msg_conv_time ON messages (conversation_id, created_at);
-- 全局最近消息查询（管理面板、监控）
CREATE INDEX idx_msg_created   ON messages (created_at);
```

#### 3.8.1 conversations（会话主表，Phase 3 新增）

> 一个用户与一个角色的对话线程。`context` JSONB 存储压缩后的对话摘要，
> 避免每次拉取全量历史。`last_message_at` 维护会话活跃度。

```sql
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    character_id    UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    user_id         VARCHAR(100) NOT NULL,
    platform        VARCHAR(20) NOT NULL,                 -- web/qq/lark/internal
    context         JSONB,                                -- LLM 上下文压缩摘要
    last_message_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 同一用户对同一角色仅一个会话（ON CONFLICT 依赖此唯一约束）
CREATE UNIQUE INDEX idx_conv_user_char ON conversations (user_id, character_id);
-- 按最后消息时间排序活跃会话
CREATE INDEX idx_conv_last_msg         ON conversations (last_message_at DESC);
-- 按 character 查询所有相关会话（角色侧主动分享时使用）
CREATE INDEX idx_conv_char             ON conversations (character_id);
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
| 角色行为历史 | `(character_id, timestamp DESC)` | 分区裁剪 + 索引 |
| 关联角色反查 | `gin (related_characters)` | 数组成员查询 |
| 关系图遍历 | `(character_id)` PK + `(target_id)` 查询 | 双向查询（双向关系需两条记录） |
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

> ⚠️ `reflections` 表已移除 `embedding` 字段（v5 简化），反思检索改用 `content` 全文匹配或应用层合并。

```sql
-- 反思按时间倒序拉取（应用层再用 LLM 重排或与记忆联合检索）
SELECT id, content
FROM reflections
WHERE character_id = :cid
ORDER BY created_at DESC
LIMIT 20;
```

### 5.4 联合检索（应用层合并）

```sql
-- 原始记忆向量检索 + 反思按时间拉取，应用层按 final_score 合并排序
SELECT 'memory' AS kind, id, content AS text, embedding <=> :q_vec AS dist
FROM memory_episodes WHERE character_id = :cid
ORDER BY embedding <=> :q_vec LIMIT :top_k;
-- UNION ALL
-- SELECT 'reflection', id, content, 0 FROM reflections WHERE character_id = :cid
-- ORDER BY created_at DESC LIMIT 5;
-- ⚠️ reflections 无 embedding 字段，反思合并由应用层用 LLM 重排实现
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
from sqlalchemy import ForeignKey, String, Integer, Boolean, Index, DateTime, Uuid
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
    character_id: Mapped[UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )
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
