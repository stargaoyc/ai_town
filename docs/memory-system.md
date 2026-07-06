# 记忆系统设计

> 记忆系统让角色拥有"过去"，是长期一致性的基础。本文档定义三层记忆架构、pgvector 检索流程、反思与规划机制。

---

## 一、设计目标

| 目标 | 说明 |
|------|------|
| 长期一致性 | 角色行为基于历史记忆，不会"失忆" |
| 可演化 | 通过反思形成高层认知，影响未来决策 |
| 高效检索 | Top-K 向量检索 p95 < 30ms（10M 级记忆） |
| 事务一致 | 记忆写入与行为记录同一事务，杜绝半写 |

---

## 二、三层记忆架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                    1. 原始记忆 (Memory Stream)                   │
│    所有行为记录的原始日志，存入 PG memory_episodes（含向量）     │
│    [昨天去了咖啡店] [周一早上迟到了] [认识了新朋友]              │
└─────────────────────────┬───────────────────────────────────────┘
                          │ 定期总结 (每 N 条触发)
┌─────────────────────────▼───────────────────────────────────────┐
│                    2. 反思总结 (Reflection)                      │
│    对大量原始记忆的归纳提炼，形成高层次自我认知                  │
│    ["我习惯早睡早起", "我对社交有点焦虑", "我喜欢雨天"]          │
└─────────────────────────┬───────────────────────────────────────┘
                          │ 影响
┌─────────────────────────▼───────────────────────────────────────┐
│                    3. 长期规划 (Planning)                        │
│    基于性格和反思生成的长期目标与每日计划                        │
│    [三个月内学会咖啡拉花] [明天 8:00 去学校]                     │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 原始记忆（memory_episodes）

每次 Action 执行后生成一条记忆，包含自然语言描述与向量。

| 字段 | 说明 |
|------|------|
| `content` | 自然语言描述（"小明在咖啡店工作了 2 小时"） |
| `embedding` | 1536 维向量（OpenAI text-embedding-3-small） |
| `importance` | 1–10 重要程度，影响检索权重 |
| `source_type` | `action` / `conversation` / `reflection` / `event` |
| `related_characters` | 涉及的其他角色 ID |
| `location` | 发生地点 |
| `is_reflected` | 是否已被反思吸收 |

### 2.2 反思总结（reflections）

定期对原始记忆归纳，形成高层认知。反思本身也可向量化，支持高层语义检索。

| 字段 | 说明 |
|------|------|
| `summary` | 一句话总结（"我习惯早睡早起"） |
| `detail` | 详细论证 |
| `source_memory_ids` | 由哪些记忆归纳而来 |
| `embedding` | 反思向量（可选） |

### 2.3 长期规划（plans）

基于性格和反思生成的目标与计划。

| 字段 | 说明 |
|------|------|
| `title` | 目标描述（"三个月内学会咖啡拉花"） |
| `horizon` | `daily` / `weekly` / `monthly` / `quarterly` / `yearly` |
| `steps` | JSONB 步骤数组 |
| `status` | `active` / `completed` / `abandoned` / `paused` |
| `due_at` | 截止时间 |

详细 DDL 见 [数据模型设计](data-model.md)。

---

## 三、记忆检索流程

### 3.1 流程

```text
用户提问 / 决策触发
        ↓
   生成查询向量（embed query）
        ↓
   pgvector 检索 Top-K
   SELECT * FROM memory_episodes
   WHERE character_id = :cid
   ORDER BY embedding <=> :q_vec
   LIMIT :top_k
        ↓
   按时间衰减 + 重要度重排序
   final_score = sim_score * w1 + recency * w2 + importance * w3
        ↓
   注入 LLM 上下文
```

### 3.2 检索 SQL

```sql
-- 角色记忆 Top-K 检索 + 时间衰减重排序
SELECT id, content, importance, timestamp,
       1 - (embedding <=> :q_vec) AS sim_score
FROM memory_episodes
WHERE character_id = :cid
ORDER BY embedding <=> :q_vec
LIMIT :top_k;
```

### 3.3 混合检索（推荐生产用）

```sql
-- 向量召回 + 时间/重要度加权
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

### 3.4 反思层检索

```sql
-- 反思层语义检索 (高层认知)
SELECT id, summary
FROM reflections
WHERE character_id = :cid
ORDER BY embedding <=> :q_vec
LIMIT 5;
```

应用层将"原始记忆"与"反思"合并注入 LLM 上下文，让角色既能引用具体事件，又能体现稳定认知。

---

## 四、反思触发机制

### 4.1 触发条件

| 触发条件 | 说明 |
|----------|------|
| 数量阈值 | 每新增 20 条未反思记忆触发一次反思 |
| 时间阈值 | 每日固定时间（如晚上 22:00） |
| 事件触发 | 关键事件后（关系变化、重大决策、突发事件） |

### 4.2 反思生成流程

```text
1. 拉取最近 N 条未反思记忆 (is_reflected = FALSE)
2. 构造反思 Prompt:
   输入: 角色性格 + N 条记忆
   输出: 3 条高层总结
3. 对每条总结调用 embed() 生成向量
4. 写入 reflections 表 (含 embedding)
5. 更新对应 memory_episodes.is_reflected = TRUE (同一事务)
```

### 4.3 反思 Prompt 示例

```text
[角色]
姓名: {name}
性格: {personality}

[近期记忆]
1. {memory_1}
2. {memory_2}
...

[任务]
请基于以上记忆，归纳出 3 条关于该角色的高层认知。
每条以 JSON 输出: { "summary": "...", "detail": "..." }
```

---

## 五、规划机制

### 5.1 计划生成触发

| 触发 | 说明 |
|------|------|
| 每日规划 | 每天 6:00 生成当日计划 |
| 反思驱动 | 反思产生新认知后，调整长期计划 |
| 事件驱动 | 突发事件（如失业、新关系）触发计划重排 |

### 5.2 计划与 Action 的关系

```text
长期计划 (plans)
    ↓ 分解
每日计划 (steps JSONB)
    ↓ 影响
候选 Action 过滤 (优先选择符合计划的 Action)
    ↓
LLM 决策时注入"当前计划"作为上下文
```

LLM 决策 Prompt 中包含 `[当前计划]` 段，引导角色选择符合长期目标的 Action。

---

## 六、Repository 接口

```python
# db/repositories/memory_repo.py
class MemoryRepository:
    async def add(self, ep: MemoryEpisode) -> MemoryEpisode: ...
    async def search_similar(
        self, character_id: UUID, query_vec: list[float], top_k: int = 10
    ) -> list[MemoryEpisode]: ...
    async def search_hybrid(
        self, character_id: UUID, query_vec: list[float], top_k: int = 10
    ) -> list[MemoryEpisode]: ...
    async def recent(self, character_id: UUID, limit: int = 50) -> list[MemoryEpisode]: ...
    async def unreflected(self, character_id: UUID, limit: int = 20) -> list[MemoryEpisode]: ...
    async def mark_reflected(self, memory_ids: list[UUID]) -> None: ...


class ReflectionRepository:
    async def add(self, r: Reflection) -> Reflection: ...
    async def search_similar(
        self, character_id: UUID, query_vec: list[float], top_k: int = 5
    ) -> list[Reflection]: ...
    async def by_character(self, character_id: UUID) -> list[Reflection]: ...


class PlanRepository:
    async def add(self, p: Plan) -> Plan: ...
    async def active(self, character_id: UUID) -> list[Plan]: ...
    async def update_status(self, plan_id: UUID, status: str) -> None: ...
```

---

## 七、性能与扩展

### 7.1 索引策略

| 索引 | 用途 |
|------|------|
| `hnsw (embedding vector_cosine_ops)` | 向量近似最近邻 |
| `(character_id, timestamp DESC)` | 角色内时间范围扫描 |
| `(character_id, importance DESC)` | 重要度排序 |
| `gin (related_characters)` | 关联角色反查 |

### 7.2 性能指标

| 场景 | 数据量 | p95 延迟 |
|------|--------|----------|
| 单角色 Top-10 检索 | 5 万条 | < 20ms |
| 单角色 Top-10 检索 | 100 万条 | < 30ms |
| 全局 Top-10 检索 | 1000 万条 | < 50ms |

### 7.3 扩展到独立向量库的判定

满足任一条件时，建议把 `memory_episodes` 迁回独立向量库（如 Milvus），PG 仅存元数据：

- 单角色记忆数 > 500 万，或总记忆数 > 1 亿；
- HNSW 索引构建内存占用超过 PG `shared_buffers` 50%；
- 检索 p95 > 200ms 且调参无效。

`MemoryRepository` 已抽象，切换成本仅限实现类。详见 [迁移指南](migration-guide.md#向量库切换)。

---

## 八、可观测埋点

| Span | 关键属性 |
|------|----------|
| `memory.retrieve` | `character_id`, `query`, `top_k`, `latency_ms` |
| `memory.write` | `character_id`, `importance`, `source_type` |
| `memory.reflect` | `character_id`, `input_count`, `output_count` |
| `plan.generate` | `character_id`, `horizon`, `steps_count` |

---

## 九、相关文档

| 主题 | 文档 |
|------|------|
| 数据模型 DDL | [data-model.md](data-model.md) |
| Action 系统 | [action-system.md](action-system.md) |
| 世界引擎 | [world-engine.md](world-engine.md) |
