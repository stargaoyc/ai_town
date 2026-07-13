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
| `importance` | 1–10 重要程度，影响检索权重。默认固定为 5；启用 `MEMORY_LLM_SCORING_ENABLED=true` 后由 LLM 按情感强度/关系影响/稀缺性/后续影响评分 |
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

### 7.3 切换到独立向量库的判定

满足任一条件时，建议把 `memory_episodes` 切换到独立向量库（如 Milvus），PG 仅存元数据：

- 单角色记忆数 > 500 万，或总记忆数 > 1 亿；
- HNSW 索引构建内存占用超过 PG `shared_buffers` 50%；
- 检索 p95 > 200ms 且调参无效。

`MemoryRepository` 已抽象，切换成本仅限实现类。详见 [架构设计 - 向量检索](architecture.md#53-向量检索pgvector--hnsw)。

---

## 八、可观测埋点

| Span | 关键属性 |
|------|----------|
| `memory.retrieve` | `character_id`, `query`, `top_k`, `latency_ms` |
| `memory.write` | `character_id`, `importance`, `source_type` |
| `memory.reflect` | `character_id`, `input_count`, `output_count` |
| `plan.generate` | `character_id`, `horizon`, `steps_count` |
| `memory.llm_score` | `character_id`, `event_content`, `score`, `model` |

---

## 九、LLM 记忆重要程度评分

### 9.1 设计动机

默认情况下，所有记忆的 `importance` 字段固定为 5，无法区分"日常喝水"与"与好友吵架"的重要程度。启用 LLM 评分后，每条事件由 LLM 按 1-10 分评分，让记忆检索更精准。

### 9.2 启用方式

通过环境变量 `MEMORY_LLM_SCORING_ENABLED=true` 启用（默认 `false`）：

```bash
# .env
MEMORY_LLM_SCORING_ENABLED=true
```

### 9.3 评分维度

LLM 基于以下四个维度综合评分（1-10）：

| 维度 | 说明 | 示例高分场景 |
|------|------|-------------|
| **情感强度** | 事件引发的情感波动程度 | 角色大哭、暴怒、狂喜 |
| **关系影响** | 对角色关系的改变程度 | 初次见面、关系破裂、表白 |
| **稀缺性** | 事件发生的罕见程度 | 罕见节日、意外相遇、突发事件 |
| **后续影响** | 对未来决策的影响程度 | 获得新工作、受伤、搬家 |

### 9.4 实现位置

| 文件 | 方法 | 说明 |
|------|------|------|
| `src/memory/episode_service.py` | `score_importance_with_llm(content, character_context)` | 调用 LLM 进行评分 |
| `src/memory/episode_service.py` | `_memorize()` | 根据 `MEMORY_LLM_SCORING_ENABLED` 选择 LLM 评分或默认值 5 |

### 9.5 成本考量

- 每条事件额外消耗约 200-400 Token（轻量 prompt + 结构化输出）；
- 50 角色 × 30s/Tick × 24h = 14.4 万次/天，启用后日增成本约 $0.5-$2（gpt-4o-mini）；
- 建议在低角色数（<10）或调试期启用，生产环境 50 角色时保持 `false`。

---

## 十、日记服务（DiaryService）

### 10.1 设计目标

`memory_episodes` 是事件级真相源，但角色视角的「今天发生了什么」需要叙事性归档。DiaryService 基于一段时间内的记忆，调用 LLM 生成第一人称日记，作为角色情感与经历的浓缩存档。

| 特性 | 说明 |
|------|------|
| **真相源不变** | 日记不替代 `memory_episodes`，仅作为叙事归档 |
| **时间真相源** | 使用真实 UTC 时间过滤记忆（非虚拟世界时间） |
| **四种周期** | day / week / month / year，按 `PERIOD_DAYS` 映射天数 |
| **最低记忆阈值** | 时间段内记忆少于 3 条时返回失败（422），避免空日记 |
| **LLM 输出结构化** | 强制返回 `{title, content, mood}` JSON |

### 10.2 数据模型

表 `character_diaries`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID v7 | 主键 |
| `character_id` | UUID | 角色 ID（外键） |
| `period` | text | `day` / `week` / `month` / `year` |
| `diary_date` | TIMESTAMPTZ | 日记日期（真实 UTC） |
| `diary_end_date` | TIMESTAMPTZ | 周期结束日期（仅 `period != "day"`） |
| `title` | text | 日记标题 |
| `content` | text | 日记正文（200-500 字） |
| `mood` | text | 情绪标签 |

### 10.3 实现位置

| 文件 | 类/方法 | 说明 |
|------|---------|------|
| `src/memory/diary_service.py` | `DiaryService.generate_diary()` | 生成日记主流程 |
| `src/memory/diary_service.py` | `DiaryService._get_target_time()` | 获取目标时间（真实 UTC） |
| `src/memory/diary_service.py` | `DiaryService._save_diary()` | 保存到 `character_diaries` 表 |
| `src/memory/diary_service.py` | `DiaryService.get_diaries()` | 查询日记列表 |
| `src/db/models/diary.py` | `CharacterDiary` | SQLAlchemy 模型 |
| `src/api/memory.py` | `generate_diary` / `list_diaries` | API 端点 |

### 10.4 生成流程

```
1. 参数校验（period 合法性、LLM 可用性）
2. 计算时间窗口：target = datetime.now(UTC)，start = target - timedelta(days=PERIOD_DAYS[period])
3. 从 memory_episodes 查询 [start, target] 内的记忆（按时间正序）
4. 记忆少于 3 条 → 返回 None（API 返回 422）
5. 构造 Prompt（最多 20 条记忆，避免 prompt 过长）
6. 调用 LLM structured_output，强制返回 {title, content, mood}
7. 保存到 character_diaries 表
8. 返回日记数据
```

### 10.5 Prompt 要点

```
你是角色「{character_name}」，请根据以下记忆记录，写一篇{period_cn}的日记。

要求：
1. 以第一人称写，体现角色的性格和情感
2. 不要罗列事实，而是叙事性地总结
3. 包含角色的感受和思考
4. 字数 200-500 字
5. 不要暴露你是 AI

请输出 JSON: {"title": "...", "content": "...", "mood": "..."}
```

---

## 十一、Person Memory（角色对用户的记忆）

### 11.1 设计目标

角色需要记住不同用户的偏好、互动历史与情感连接，以便在后续对话中体现「我记得你」。Person Memory 是角色视角对单个用户的记忆归档。

### 11.2 数据模型

表 `person_memories`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID v7 | 主键 |
| `character_id` | UUID | 角色 ID |
| `user_id` | text | 用户标识 |
| `platform` | text | 平台（web / qq / discord） |
| `content` | text | 记忆内容（自然语言） |
| `heat` | int | 热度（互动越多越高） |
| `last_interaction_at` | TIMESTAMPTZ | 最后互动时间 |
| `created_at` / `updated_at` | TIMESTAMPTZ | 时间戳 |

### 11.3 实现位置

| 文件 | 类/方法 | 说明 |
|------|---------|------|
| `src/memory/person_memory_service.py` | `PersonMemoryService` | 服务实现 |
| `src/db/models/person_memory.py` | `PersonMemory` | SQLAlchemy 模型 |
| `src/api/memory.py` | `get_person_memory` / `list_person_memories` | API 端点 |

### 11.4 热度排序

`list_person_memories` 按 `heat DESC, last_interaction_at DESC` 排序，让角色最熟悉的用户排在前列。

---

## 十二、相关文档

| 主题 | 文档 |
|------|------|
| 数据模型 DDL | [data-model.md](data-model.md) |
| Action 系统 | [action-system.md](action-system.md) |
| 世界引擎 | [world-engine.md](world-engine.md) |
