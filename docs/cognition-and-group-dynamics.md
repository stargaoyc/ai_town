# 认知深化与群体动力学（2026-08-24 实现纪要）

> 本文记录 2026-08-24 两批实现的设计语义：群体动力学（传闻/共同经历/群活动）与
> 认知深化四项（主题反思/两层 PersonMemory/记忆压缩归档/计划层级）。
> 相关评审：[第二轮复审](project-review-20260824-round2.md)。

---

## 一、群体动力学

### 1.1 共同经历标记

Character Tick 记忆沉淀时，同场景在场角色写入 `memory_episodes.related_characters`
（UUID[]）。该字段是「共同经历查询」与「传闻溯源」的原语：

```sql
-- A 与 B 的共同经历
SELECT * FROM memory_episodes
WHERE character_id = :a AND related_characters @> ARRAY[:b::uuid];
```

### 1.2 传闻传播（GossipService）

好友的高重要性经历以第二手记忆扩散到听者：

```
源：好友 A 的 action 记忆，importance >= GOSSIP_IMPORTANCE_THRESHOLD(7)，24h 窗口内
边：A↔B 关系强度 >= GOSSIP_RELATION_MIN(20)
果：B 新增记忆「听{A名}说：<源内容前120字>」
    importance = max(2, 源importance // 2)   # 保真度递减
    source_type='gossip'，related_characters=[A]
```

三条设计约束：
1. **内容零编造**——模板拼接源记忆原文，LLM 不参与转述；
2. **每好友每窗口 ≤1 条**——去重复用 `source_type + related_characters` 列（UUID[] 包含查询），不加新表；
3. **不二次传播**——候选查询排除 `source_type='gossip'`，八卦不会被当亲身经历继续扩散。

配置：`GOSSIP_ENABLED / GOSSIP_IMPORTANCE_THRESHOLD / GOSSIP_WINDOW_HOURS / GOSSIP_MAX_PER_TICK / GOSSIP_RELATION_MIN`。
接入点：Tick 步骤 5.5（`_propagate_gossip`），失败仅告警不阻断主流程。

### 1.3 群活动（group_activity Action）

同场景 ≥3 人（自己 + ≥2 名其他角色）时可触发临时小聚：

- **人数门槛在 Tick 侧候选过滤执行**——Action.precondition 只见 state 字典拿不到在场名单；
- 单次 LLM 调用生成集体叙事（`configs/prompts/group_activity.yaml`，与 chat_with 同哲学：
  一次往返保证连贯）；LLM 失败退化为模板叙事，聚会照常发生；
- 为**每个参与者**写共同经历记忆（related_characters 互指，importance=6）；
- 两两关系 +2（上限 100；陌生角色因共同活动结识，默认 20 起步）；
- LLM 失败/解析失败的降级路径与 chat_with 一致：回退 wait。

### 1.4 传闻的行为化表达

最近 24h 的传闻注入决策 Prompt「[听说的消息]」段——角色可在 chat_with 中自然提起
听来的消息，而非只沉默存档。查询入口 `MemoryRepository.fetch_recent_gossip()`。

---

## 二、认知深化

### 2.1 反思分层（reflections.tier）

| tier | 名称 | 触发 | 产出 |
|---|---|---|---|
| 1 | 批次主题反思 | 未反思记忆 ≥20 条 | 编号记忆池（≤30 条）→ LLM 归纳 2-4 个主题，**每主题一条 Reflection**，来源只挂载支撑该主题的记忆 |
| 2 | 跨期元反思 | 累计反思 ≥6 且 7 天冷却期满 | 最近 10 条 tier-1 → 归纳「[长期倾向]」（跨期主题归纳） |

决策注入排序：tier 降序 → created_at 降序（元反思优先）。
LLM 未给出可用主题映射时退化为单条汇总（来源挂全池），不丢 grounding。

### 2.2 Person Memory 两层结构

```
条目层 person_memory_entries（append-only）
  - 每次交互由 LLM 抽取「新事实」逐条追加，只写不改
  - 解析失败回退：「用户提到：<消息前120字>」，不丢交互痕迹
主档层 person_memories.content
  - 后台每 6h 把未压缩条目 >= PERSON_MEMORY_COMPACT_THRESHOLD(20) 的
    (角色,用户) 对合并进主档，条目标记 compacted=TRUE（软归档可追溯）
对话上下文 = 主档 + 最近 8 条未压缩条目
```

从结构上根除旧版「单槽全文重写」的 telephone game 漂移。
Prompt：`person_memory.yaml`（抽取语义）/ `person_memory_compact.yaml`（合并语义）。

### 2.3 记忆压缩归档（retention 两阶段）

```
阶段一 压缩：到期低价值记忆按（角色 × 月份）分组，
       组内 >= MEMORY_COMPRESSION_MIN_BATCH(5) 条则 LLM 压缩成
       [归档] 行（source_type='archive'，importance=3，materialized=False 进向量队列）
阶段二 删除：分级删除（≤3 级 90 天 / 4-6 级 180 天），归档行豁免
```

**不变量：压缩失败的组整组跳过留待下周期——绝不未压缩先删除。**
低于最小批的小组无需摘要，直接删除（摘要收益低于成本）。

### 2.4 改写式记忆去重（is_duplicate）

EmbeddingWorker 向量化时与同角色近 `MEMORY_DEDUP_WINDOW_HOURS`(24h) 已向量化记忆
余弦比对，相似度 ≥ `MEMORY_DEDUP_SIMILARITY_THRESHOLD`(0.95) 判定改写式重复：

- 重复行置 `is_duplicate=TRUE, materialized=TRUE, embedding=NULL`
  （防 worker 无限重拉；不落向量节省空间）；
- `search_hybrid` 与 `fetch_unreflected` 均排除重复行；
- **pg_trgm 方案已实测证伪**（中文真实改写对相似度仅 0.31–0.40），向量比对是唯一可靠信号。

### 2.5 计划层级体系

| 类型 | 语义 | 生命周期 |
|---|---|---|
| long_term | 长期目标（数周-数月） | 手工完成/放弃 |
| short_term | 短期计划（数天-数周） | planChanges 或手工 |
| daily | 当日计划 | 创建超 `DAILY_PLAN_TTL_HOURS`(24h) 自动置 expired |

- LLM 双通道：`planChanges`（变更既有，归属校验）+ `createPlanChanges`（新建，
  服务端绑定 character_id，类型白名单、优先级钳制 1-5、单次最多 3 条有效条目）；
- 决策 Prompt [当前计划] 注入类型/优先级/截止日全量信息；
- 作息桥接：ScheduleSystem 档位经 `{schedule}` 占位符注入（含睡眠约束提示）；
- 计划通过 Prompt 软引导影响行为，**不做 precondition 硬过滤**（保留自主行为空间）。

---

## 三、运维配套

| 能力 | 用法 |
|---|---|
| 数据库定时备份 | `docker compose --profile backup up -d`；pg_dump\|gzip 每 `BACKUP_INTERVAL_HOURS`(6h) 写入 `./data/backups`，保留 `BACKUP_RETENTION_DAYS`(14) 天；.part 原子改名防半成品 |
| 容器日志轮转 | compose 全服务 json-file 10MB×3（锚点 `x-default-logging`） |
| 冷启动恢复演练 | `cd packages/backend && uv run python scripts/cold_start_drill.py`（`--world-only` 仅世界层）；清空 Redis 世界/角色键 → `rehydrate_states()` → 校验快照回灌与角色镜像恢复 |

---

## 四、测试地图

| 机制 | 测试 |
|---|---|
| 传闻传播 | `tests/integration/test_gossip_it.py`（传播/幂等/门槛/不二次传播/过期） |
| 群活动持久化 | `tests/integration/test_group_activity_it.py`（全员互指/关系加固含钳制/幂等） |
| 主题反思解析 | `tests/test_reflection_themes.py` |
| retention 压缩 | `tests/integration/test_retention_compression_it.py` |
| PM 两层 | `tests/integration/test_person_memory_layers_it.py` |
| 改写式去重 | `tests/integration/test_memory_dedup_it.py` + worker 分支 `test_worker_dedup_it.py` |
| 世界事件差分 | `tests/test_world_events_diff.py` |
| 热度衰减 | `tests/integration/test_heat_decay_it.py` |
| 计划层级 | `tests/test_plan_hierarchy.py` + `tests/integration/test_plan_hierarchy_it.py` |
