# 总体架构设计

> 本文档定义 AI Town（二次元 AI 小镇陪伴智能体）的分层架构、核心循环、数据流闭环、技术栈选型，以及关键架构决策。文档基于后端 `packages/backend` 最新代码状态撰写，覆盖 World Engine、Character Tick、消息服务、QQ 接入、主动分享、LLM 客户端、数据架构与可观测性等全部子系统。

---

## 一、项目背景与目标

### 1.1 背景

随着大语言模型（LLM）技术的快速发展，AI 陪伴产品正从简单的"对话机器人"向具备自主推理、工具调用和长期记忆能力的"智能体（Agent）"演进。传统陪伴型 Bot 存在以下痛点：

| 痛点 | 描述 |
|------|------|
| 状态易失 | 对话结束即"失忆"，无法形成长期一致的人设与关系 |
| 被动响应 | 仅在用户主动发起时才回复，缺乏"主动联系"的真实感 |
| 单一渠道 | 仅支持 Web 或单一 IM，难以覆盖用户日常触达场景 |
| 群聊缺位 | 在群聊场景下要么完全沉默、要么每条必回，缺乏"像真人一样选择性回复"的能力 |
| 行为不可追溯 | 角色行为黑盒，无法审计为什么做出某个决策 |
| 工程割裂 | LLM 调用、状态管理、记忆系统各自为政，难以闭环 |

本项目构建一个由 LLM 驱动的**多智能体虚拟小镇**，让二次元 AI 角色拥有独立的记忆、反思、规划与社交能力，在持续运行的虚拟世界中自主生活，并通过 Web、QQ（OneBot）、飞书等多渠道与用户建立长期陪伴关系。

### 1.2 目标

| 目标 | 说明 |
|------|------|
| 多角色共居 | 支持 10–50 个 AI 角色同时在小镇中生活、决策、交互 |
| 世界持续运行 | 世界状态推进不依赖用户消息，角色在用户离线时依然生活（World Tick + Character Tick 双循环） |
| 记忆与演化 | 角色拥有记忆流（MemoryEpisode）、反思能力（Reflection）和长期规划（Plan），行为长期一致且可演化 |
| 群聊智能回复 | QQ 群聊中通过"@机器人 / 关键词命中 / 启发式规则 / LLM 相关性判断"四层决策，实现像真人一样选择性回复，受 40% 概率上限约束避免刷屏 |
| 主动分享 | 角色在 Tick 中产生 `proactive_share_intent` 时，主动向有活跃会话的 QQ/Web 用户推送生活动态，形成"角色主动联系用户"的反向闭环 |
| 多段回复 | 长回复按段落拆分为多条消息依次发送（0.6s 间隔），模拟真人打字节奏，提升二次元陪伴感 |
| 可插拔能力 | 功能模块（代码执行、搜索、绘图等）通过 MCP 协议动态启用/禁用，热插拔 |
| 全链路可观测 | 每个决策周期可追踪、可审计、可调试（Prometheus 指标 + structlog 日志 + Langfuse 链路） |
| 多端触达 | 支持 Web Dashboard、QQ（OneBot v11/v12）、飞书（Lark）、开放 API 等多渠道交互 |

### 1.3 设计原则

| 原则 | 说明 |
|------|------|
| 状态驱动 | LLM 是决策和生成能力，不是状态真相源；所有状态变更由代码执行并落到 PG/Redis |
| 事实优先 | 所有可追溯事实必须落到 `action_records` / `memory_episodes` / `world_events` 等行为记录或明确的状态字段中 |
| 闭环演化 | 行为沉淀为记忆 → 记忆触发反思 → 反思影响未来决策 → 形成可追溯的生活轨迹 |
| 模块解耦 | 核心引擎（World Engine / Character Tick）与功能模块（MCP / 适配器）分离，模块可独立开关、独立升级 |
| 异步化解耦 | 慢操作（embedding、视频生成、主动分享推送）异步化，不阻塞 Tick 主循环 |
| 可观测性 | 埋点即契约，所有关键路径必须有 Trace 覆盖（Prometheus + Langfuse + structlog） |
| 成本可控 | LLM 调用必须有日预算（`llm_daily_budget_usd`）与熔断器（`llm_circuit_breaker_threshold`）兜底 |
| 安全前置 | 用户输入必须经 Prompt 注入检测 + 消毒（`PromptGuard`），LLM Prompt 中用户消息用分隔符包裹 |
| 幂等优先 | 事件写入、Tick 执行、消息处理均需支持幂等，避免重试导致脏数据 |

---

## 二、分层架构

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                        用户接入层 (Access Layer)                            │
│   Web Dashboard  │  QQ (OneBot v11/v12)  │  飞书 (Lark)  │  开放 API       │
│   /ws/chat/{cid} │  /ws/onebot/v12       │   (规划中)    │  /api/v1/*      │
│   WebSocketManager│ OneBotAdapter         │  LarkAdapter │  JWT + API Key  │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                       消息服务层 (Messaging Layer)                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐ │
│  │ 消息标准化  │ │ 会话上下文  │ │ 回复生成    │ │ 主动推送调度│ │ 群聊智能  │ │
│  │ PromptGuard│ │ ContextMgr │ │ LLM + 熔断 │ │ProactiveSha│ │ 回复决策  │ │
│  │ 消毒/截断   │ │ 历史压缩    │ │ 成本控制    │ │ ringService│ │ 三层过滤  │ │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └──────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                     世界引擎层 (World Engine Layer)                          │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐ │
│  │   World Tick         │  │  Character Tick      │  │   Evolution 链       │ │
│  │  Redis 分布式锁选主   │  │  五阶段闭环          │  │ 时间→天气→场景→资源  │ │
│  │  每 N 秒推进世界      │  │  感知→决策→执行→记忆  │  │  →事件               │ │
│  │  事件去重 + 快照      │  │  主动分享 + 反思     │  │  default_evolutions()│ │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                    Agent 能力层 (Agent Capability Layer)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ 记忆系统  │ │ 反思系统  │ │ 规划系统  │ │ 决策系统  │ │  社交系统         │ │
│  │EpisodeSvc│ │Reflection│ │ PlanRepo │ │DecisionR │ │  RelationGraph   │ │
│  │Retrieval │ │ Service  │ │ Schedule │ │ LLM 结构化│ │  MovementSystem  │ │
│  │EmbedWorker│ │ 反思来源 │ │ System   │ │ 输出      │ │  DurationCalc    │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    MCP 工具调用层 (标准化接口)                         │ │
│  │  代码执行 │ 网页搜索 │ 天气查询 │ 商店模拟 │ 知识库 │ 角色社交         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                    数据访问层 (Data Access Layer)                           │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  Repositories（CharacterRepository / MemoryRepository / ...）          │ │
│  ├────────────────────────────────────────────────────────────────────────┤ │
│  │  混合策略：SQLAlchemy 2.0 ORM（模型/迁移/简单CRUD）                    │ │
│  │           + 原生 SQL text()（向量检索/复杂CTE/分区DDL）                │ │
│  ├────────────────────────────────────────────────────────────────────────┤ │
│  │  pgvector halfvec + HNSW 索引  │  HASH 分区（memory_episodes 16 分区）│ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                    基础设施层 (Infrastructure Layer)                        │
│  ┌───────────────────┐  ┌─────────────┐  ┌──────────────┐ │
│  │  PostgreSQL 17    │  │  Redis 8.0  │  │  LLM 网关    │ │
│  │  + pgvector       │  │  缓存/锁/   │  │  OpenAI 兼容 │ │
│  │  + pg_uuidv7      │  │  实时状态    │  │  chat/image/ │ │
│  │  + JSONB          │  │  Leader 选举│  │  video/embed │ │
│  │  + 分区表          │  │             │  │              │ │
│  └───────────────────┘  └─────────────┘  └──────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │              可观测性 (OpenTelemetry + Langfuse + Prometheus)          │ │
│  │   链路追踪(Jaeger)  │  指标(Prometheus+Grafana)  │  日志(Loki+structlog)│ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 各层职责

| 层 | 职责 | 关键组件 | 代码位置 |
|----|------|----------|----------|
| 用户接入层 | 多平台消息收发与协议适配 | `WebSocketManager`、`OneBotAdapter`、`LarkAdapter` | `src/api/`（REST 路由）、`src/messaging/websocket.py`、`src/adapters/` |
| 消息服务层 | 消息标准化、会话上下文、回复生成、主动推送、群聊智能回复 | `MessageService`、`ProactiveSharingService`、`PromptGuard` | `src/messaging/` |
| 世界引擎层 | 全局状态推进、角色行为闭环、多智能体调度、演化器链 | `WorldEngine`、`CharacterTickEngine`、`default_evolutions()` | `src/core/` |
| Agent 能力层 | 记忆/反思/规划/决策/社交/MCP 工具 | `EpisodeService`、`ReflectionService`、`RetrievalService`、`ActionRegistry` | `src/memory/`、`src/actions/`、`src/modules/` |
| 数据访问层 | Repositories 抽象、ORM 与原生 SQL 混合、pgvector/HNSW/分区 | `MemoryRepository`、`CharacterRepository`、`db.session` | `src/db/` |
| 基础设施层 | 持久化、缓存、LLM 网关、可观测性 | PostgreSQL、Redis、`LLMClient`、OTel | `src/llm/`、`src/observability/` |

---

## 三、核心循环

AI Town 由三个相互独立但协同的核心循环驱动：World Tick（世界推进）、Character Tick（角色行为）、用户消息处理（对话响应）。

### 3.1 World Tick 循环

World Tick 是世界状态推进的主循环，由 `WorldEngine` 类实现（`src/core/world_engine.py`）。它确保虚拟世界的时间流逝、天气变化、场景状态、资源消耗和事件触发，**与用户是否在线无关**。

#### 执行流程

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    World Tick 主循环（_tick_loop）                   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 1. Redis 分布式锁选主（Leader Election）     │
        │    LOCK_KEY = "world:tick:leader"            │
        │    SET NX EX 30（TTL 30s，续租间隔 10s）      │
        │    仅 is_leader=True 的实例执行 Tick          │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 2. 读取世界状态（_load_world_state）          │
        │    从 Redis Hash 读取各演化器状态：           │
        │    - world:state:time       时间             │
        │    - world:state:weather    天气             │
        │    - world:state:scenes     场景             │
        │    - world:state:resources  资源             │
        │    - world:state:events     事件             │
        │    合并为完整 world_state 字典                │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 3. 执行演化器链（Evolution Chain）            │
        │    for evolution in default_evolutions():    │
        │        result = await evolution.evolve(      │
        │            redis, tick_id, world_state)      │
        │        world_state.update(result)            │
        │                                              │
        │    依赖顺序（前序写入供后续读取）：            │
        │    TimeEvolution → WeatherEvolution          │
        │    → SceneEvolution → ResourceEvolution      │
        │    → EventEvolution                          │
        │                                              │
        │    容错：单个演化器失败不中断整个 Tick        │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 4. 持久化到 Redis（_save_world_state）        │
        │    HSET world:state                          │
        │      tick_id / world_time / weather /        │
        │      temperature / updated_at                │
        │    各演化器详细状态写回各自 Hash              │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 5. 每 N Tick 持久化差分事件到 PG              │
        │    （world_snapshot_interval，默认 120）     │
        │    _save_world_events(world_state)           │
        │    事件去重：对比 _last_persisted_state，     │
        │    仅状态变化时写入 world_events 表           │
        │    UNIQUE(tick_id, event_type, event_key)    │
        │    保证幂等                                   │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 6. 每 1000 Tick 存完整快照                    │
        │    （world_full_snapshot_interval）          │
        │    _save_world_snapshot(world_state)         │
        │    写入 world_snapshots 表                   │
        │    冷启动恢复：最新快照 + 回放增量事件        │
        │    → 恢复时间恒定，不随运行时长线性增长       │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 7. 指标埋点                                   │
        │    WORLD_TICK_ID.set(tick_id)                │
        │    WORLD_TICK_DURATION.observe(elapsed)      │
        │    WORLD_TICK_TOTAL.inc()                    │
        │    失败时 WORLD_TICK_ERRORS.inc()             │
        └─────────────────────────────────────────────┘
```

#### Leader Election 细节

```python
# WorldEngine._leader_loop 核心逻辑
LOCK_KEY = "world:tick:leader"
LOCK_TTL = 30  # 秒
LOCK_RENEW_INTERVAL = 10  # 秒

# 1. 竞争获取锁（SET NX EX）
acquired = await redis.set(LOCK_KEY, f"leader:{now}", ex=LOCK_TTL, nx=True)

if acquired:
    self.is_leader = True
    # 2. 续租循环：每 10s 续租一次
    while not stop and self.is_leader:
        await asyncio.sleep(LOCK_RENEW_INTERVAL)
        renewed = await redis.expire(LOCK_KEY, LOCK_TTL)
        if not renewed:
            self.is_leader = False  # 锁丢失，退出续租
            break
else:
    # 3. 未获取锁：等待 TTL + 5s 缓冲后重试
    await asyncio.sleep(LOCK_TTL + 5)
```

**故障转移**：持锁实例宕机后，锁 TTL（30s）到期自动释放，其他实例在 `TTL + 5s` 后竞争接管，最长故障窗口约 35s。

#### 事件去重机制

`_save_world_events` 通过 `_last_persisted_state` 缓存上次持久化的状态摘要，仅在以下维度发生变化时才写入 `world_events`：

| 事件类型 | event_key | 去重维度 | 写入条件 |
|----------|-----------|----------|----------|
| `time` | `default` | `world_time` 字符串 | 虚拟时间变化 |
| `weather` | `default` | `weather` 字符串 | 天气变化 |
| `scene` | `default` | `scenes_state` JSON | 场景状态变化 |
| `resource` | `default` | `resources_state` JSON | 资源状态变化 |
| `event` | `default` | `events_state` 非空 | 有活跃事件时始终写入 |

**幂等保证**：`world_events` 表的 `UNIQUE(tick_id, event_type, event_key)` 约束确保即使 Tick 重试或服务重启，同一事件不会被重复写入。

### 3.2 Character Tick 循环

Character Tick 是角色行为决策与执行的闭环，由 `CharacterTickEngine` 实现（`src/core/character_tick.py`）。它定期对所有活跃角色执行"感知→决策→执行→记忆→分享→反思"六阶段闭环。

#### 执行流程

```text
┌─────────────────────────────────────────────────────────────────────┐
│              Character Tick 后台循环（_character_tick_loop）         │
│              每 character_tick_seconds（默认 30s）执行一批           │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 0. 获取所有活跃角色                          │
        │    CharacterRepository.get_active_characters│
        │    更新 ACTIVE_CHARACTERS Gauge              │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 对每个角色调用 tick_character(cid)：         │
        │ 1. Redis 分布式锁（char:tick:lock:{cid}）    │
        │    TTL 30s，避免同一角色并发 Tick            │
        │ 2. asyncio.Semaphore 并发控制                │
        │    （character_max_concurrent，默认 10）     │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ ① 感知环境（_perceive）                       │
        │    - 从 PG 读取角色档案 + 状态                │
        │    - 从 Redis 读取实时状态（缓存优先）        │
        │    - 从 Redis 读取世界状态                    │
        │    - RetrievalService.search 检索 Top-K 记忆  │
        │    - PlanRepository 读取进行中计划            │
        │    返回 context = {                          │
        │      character, state, world,                │
        │      memories, plans                         │
        │    }                                         │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ ② LLM 结构化决策（_decide）                   │
        │    - ActionRegistry.get_candidates(state)    │
        │      过滤候选 Action（precondition 检查）     │
        │    - PromptTemplates.render("decision", ...) │
        │      渲染决策 Prompt（角色档案+状态+记忆+     │
        │      候选列表+计划）                          │
        │    - LLMClient.structured_output(prompt,     │
        │        schema, model="chat")                 │
        │    - 返回 DecisionResult:                    │
        │      { action, reason, params, duration,     │
        │        plan_changes, proactive_share_intent }│
        │    - 防御性校验：action_id 必须在候选列表中   │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ ③ 执行 Action（_execute_action）             │
        │    - registry.get(action_id) 获取定义        │
        │    - apply_cost_fields 计算状态变更          │
        │    - 单一 PG 事务：                          │
        │      a. ActionRepository.add(ActionRecord)   │
        │      b. CharacterRepository.update_state     │
        │    - 更新 Redis 实时状态                     │
        │      HSET char:{cid}:state                   │
        │    - 指标：ACTION_EXECUTION_TOTAL/DURATION   │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ ④ 记忆沉淀（_memorize）                       │
        │    - 生成记忆内容（Action + 状态 + 理由）     │
        │    - EpisodeService.create_episode(          │
        │        character_id, content,                │
        │        action_id, location, importance=5)    │
        │    - 写入 memory_episodes 表                 │
        │      embedding=NULL, materialized=false      │
        │      （由 EmbeddingWorker 异步向量化）        │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ ⑤ 主动分享检查（_maybe_proactive_share）     │
        │    if decision.proactive_share_intent:       │
        │      ProactiveSharingService.evaluate_and_share│
        │      - 评估分享意图（Action 类型 + 情绪）     │
        │      - 检查冷却（1h）+ 日限额（5 次）         │
        │      - LLM 生成分享文案                      │
        │      - 写入 messages 表（share_type=proactive）│
        │      - WebSocket 推送给 Web 用户             │
        │      - _push_share_to_qq 推送给 QQ 用户      │
        │    失败不中断 Tick 主流程（try/except 兜底）  │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ ⑥ 反思检查（在 _memorize 中触发）             │
        │    ReflectionService.check_and_reflect(cid)  │
        │    - 统计未反思记忆数                        │
        │    - 若 >= REFLECTION_THRESHOLD（20）触发：   │
        │      a. 获取 20 条未反思记忆                 │
        │      b. LLM 归纳 3 条高层认知                │
        │      c. 写入 reflections 表                  │
        │      d. 写入 reflection_sources 中间表       │
        │         （复合外键引用 memory_episodes）     │
        │      e. 标记记忆 is_reflected=true           │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 指标埋点                                      │
        │   CHARACTER_TICK_DURATION.observe(elapsed)   │
        │   CHARACTER_TICK_TOTAL.labels(cid).inc()     │
        │   Langfuse trace_character_tick(...)         │
        └─────────────────────────────────────────────┘
```

#### LLM 限流退避

`_character_tick_loop` 检测到 LLM 429（RateLimitError）时，立即停止当前批次并指数退避：

```python
backoff_multiplier = 1
max_backoff = 10

if rate_limited:
    backoff_multiplier = min(backoff_multiplier * 2, max_backoff)
elif success_count > 0:
    backoff_multiplier = 1  # 恢复正常间隔

await asyncio.sleep(character_tick_seconds * backoff_multiplier)
```

这避免 Character Tick 抢占用户消息处理的 API 配额，保证对话响应优先级。

### 3.3 用户消息处理循环

用户消息处理由 `MessageService.handle_user_message` 实现（`src/messaging/service.py`），是用户与角色对话的核心入口，被 Web WebSocket、QQ OneBot、开放 API 共用。

#### 执行流程

```text
┌─────────────────────────────────────────────────────────────────────┐
│           MessageService.handle_user_message 流程                   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 0. Prompt 注入检测 + 消息消毒                │
        │    PromptGuard.check_injection(content)     │
        │    - 命中注入模式 → 拦截，返回安全提示        │
        │    PromptGuard.sanitize_user_input(content) │
        │    - 移除危险内容 + 控制字符 + 长度截断       │
        │    指标：MESSAGE_PROCESSED_TOTAL             │
        │      labels(platform, status="failed")      │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 1. 获取/创建会话                             │
        │    ConversationRepository.get_or_create(     │
        │      character_id, user_id, platform)       │
        │    幂等：同一 (character, user, platform)    │
        │    只创建一个会话                            │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 2. 写入用户消息                              │
        │    MessageRepository.add(                    │
        │      conversation_id, sender="user",         │
        │      content=content)                       │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 3. 加载角色档案 + 对话历史                   │
        │    CharacterRepository.get_character_with_state│
        │    MessageRepository.list_recent(            │
        │      conversation_id, limit=20)             │
        │    _build_context 渲染上下文：               │
        │      [角色档案] 姓名/性格/背景               │
        │      [当前状态] 位置/精力/情绪               │
        │      [对话摘要] 压缩后的 context.summary     │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 4. 检索相关记忆（可选）                      │
        │    RetrievalService.search(                  │
        │      character_id, query, top_k=10)         │
        │    pgvector HNSW 检索 + 混合排序             │
        │    （sim_score * 0.6 + importance * 0.05     │
        │      + time_decay）                          │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 5. 调用 LLM 生成回复（_generate_reply）      │
        │    a. 成本控制前置检查：                     │
        │       - CircuitBreaker.can_execute()         │
        │       - BudgetManager.check_budget()         │
        │    b. PromptGuard.wrap_user_message(user_msg)│
        │       用户消息用分隔符包裹，防角色覆盖       │
        │    c. LLMClient.chat(prompt, model="chat")   │
        │    d. 估算 token/cost（中文 ~1.5字/token）   │
        │    e. 成本控制后置记录：                     │
        │       - BudgetManager.record_usage           │
        │       - CircuitBreaker.record_success        │
        │    失败时：                                  │
        │       - CircuitBreaker.record_failure        │
        │       - 返回 DEFAULT_ERROR_REPLY             │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 6. 写入角色回复                              │
        │    MessageRepository.add(                    │
        │      conversation_id, sender="character",    │
        │      content=reply_text,                     │
        │      tokens=tokens, cost=cost,               │
        │      extra_data={"error": error})            │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 7. 按需压缩上下文（_maybe_compress_context） │
        │    if 会话消息数 > 50:                       │
        │      - 取最近 10 条之前的消息                │
        │      - LLM 压缩为 200 字摘要                 │
        │      - 写入 conversation.context.summary     │
        │      - 标记 compressed_at / compressed_count │
        │    else:                                    │
        │      - touch_last_message 更新最后消息时间   │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 8. 提交事务 + 指标埋点                       │
        │    session.commit()                         │
        │    MESSAGE_PROCESSED_TOTAL.labels(           │
        │      platform, status).inc()                │
        │    MESSAGE_PROCESSING_DURATION.observe()     │
        │    返回 {conversation_id, message_id,        │
        │           content, tokens, cost, error}      │
        └─────────────────────────────────────────────┘
```

#### 事务边界

用户消息与角色回复在**同一个 PG 事务**内提交（`session.commit()` 在第 8 步），保证：
- 用户消息写入成功 ⇔ 角色回复写入成功；
- 任一失败则整体回滚，不会出现"用户消息已存但无回复"的脏状态；
- 上下文压缩与 `last_message_at` 更新同事务，避免会话状态不一致。

---

## 四、数据流闭环

### 4.1 行为→记忆→反思闭环

这是角色"自主生活"的核心闭环，让角色行为长期一致且可演化。

```text
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   Character   │────▶│   Decision    │────▶│   Action      │
│   Tick 感知    │     │  LLM 在候选   │     │  Executor     │
│  状态+世界+记忆│     │  Action 中决策 │     │  事务化执行    │
└───────────────┘     └───────────────┘     └───────┬───────┘
                                                    │
                                                    ▼
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   未来决策     │◀────│  Reflection   │◀────│  MemoryEpisode│
│  检索时返回    │     │  高层认知      │     │  记忆沉淀      │
│  影响推理     │     │  每 20 条触发  │     │  embedding 异步│
└───────────────┘     └───────────────┘     └───────────────┘
        │                     │
        │                     │ reflection_sources
        │                     │ 中间表关联记忆
        │                     ▼
        └─────────►  reflections 表
                     （内容 + 来源记忆 ID 列表）
```

**闭环说明**：

1. **Action → MemoryEpisode**：`_execute_action` 后调用 `_memorize`，生成自然语言记忆内容（如"小明在咖啡馆执行了 buy_item。理由：想喝咖啡提神"），写入 `memory_episodes` 表，`embedding=NULL, materialized=false`。
2. **MemoryEpisode → Reflection**：每次记忆写入后调用 `ReflectionService.check_and_reflect`，统计未反思记忆数，达到阈值（20）时触发反思。
3. **Reflection → 未来决策**：反思结果存入 `reflections` 表，下次 `_perceive` 时可通过 `RetrievalService` 检索返回，作为 LLM 决策的上下文，影响角色行为。
4. **EmbeddingWorker 异步向量化**：`EmbeddingWorker` 后台批量处理 `materialized=false` 的记忆，调用 `LLMClient.embed()` 生成向量，更新 `embedding` 字段并标记 `materialized=true`。失败 5 次后熔断（`fail_count >= 5`），并通过 `next_retry_at` 指数退避。

### 4.2 用户对话→记忆闭环

用户与角色的对话同样会沉淀为记忆，让角色"记得和谁聊过什么"。

```text
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   用户消息     │────▶│ MessageService│────▶│  角色回复      │
│  Web/QQ/API   │     │ handle_user_  │     │  LLM 生成      │
│  platform 标记 │     │ message()     │     │  成本控制+熔断  │
└───────────────┘     └───────────────┘     └───────┬───────┘
                                                    │
                                                    ▼
                                            ┌───────────────┐
                                            │ MemoryEpisode │
                                            │ source_type=  │
                                            │ "conversation"│
                                            │ 写入 pgvector │
                                            │ embedding 异步│
                                            └───────────────┘
                                                    │
                                                    ▼
                                            ┌───────────────┐
                                            │  下次对话时   │
                                            │  RetrievalSvc │
                                            │  检索历史对话  │
                                            │  作为上下文   │
                                            └───────────────┘
```

**关键点**：
- `memory_episodes.source_type` 字段区分记忆来源（`action` / `conversation` / `reflection`），便于按来源过滤检索；
- 用户消息经 `PromptGuard` 消毒后再沉淀，避免注入内容污染记忆流；
- 对话记忆与行为记忆共用同一张 `memory_episodes` 表，统一参与反思与检索。

### 4.3 主动分享闭环

主动分享是"角色主动联系用户"的反向闭环，让陪伴感从"被动响应"升级为"主动关怀"。

```text
┌───────────────┐     ┌───────────────────┐     ┌───────────────┐
│ Character Tick│────▶│ proactive_share_  │────▶│ ProactiveSha- │
│ LLM 决策返回  │     │ intent = True     │     │ ringService   │
│ DecisionResult│     │ 在 DecisionResult │     │ evaluate_and_ │
└───────────────┘     └───────────────────┘     │ share()       │
                                                └───────┬───────┘
                                                        │
                        ┌───────────────────────────────┘
                        │
                        ▼
                ┌───────────────┐     ┌───────────────┐
                │  意图评估      │────▶│  频率限制      │
                │  Action 类型   │     │  冷却 1h       │
                │  + 情绪状态    │     │  日限额 5 次   │
                │  本地规则判断   │     └───────┬───────┘
                └───────────────┘             │
                                              ▼
                                      ┌───────────────┐
                                      │ LLM 生成文案   │
                                      │ 角色第一人称   │
                                      │ 50-100 字     │
                                      │ 不暴露"系统"  │
                                      └───────┬───────┘
                                              │
                        ┌─────────────────────┘
                        │
                        ▼
                ┌───────────────┐     ┌───────────────┐
                │  写入 messages │     │  推送给用户    │
                │  sender=       │     │  Web:          │
                │  "character"   │     │  WebSocketMgr │
                │  share_type=   │     │  .send_to_user│
                │  "proactive"   │     │               │
                └───────────────┘     │  QQ:          │
                                      │  OneBotAdapter│
                                      │  .push_share  │
                                      │  → send_      │
                                      │    private_msg│
                                      └───────────────┘
```

**触发条件**（`_evaluate_intent` 本地规则，不调用 LLM）：

| 规则 | 条件 | 说明 |
|------|------|------|
| Action 类型 | `action.action_id in SHAREABLE_ACTION_IDS` | 如 `buy_item`、`receive_gift`、`meet_friend`、`achieve_goal`、`finish_work`、`play_game`、`read_book`、`travel` |
| 情绪状态 | `state.mood in SHAREABLE_MOODS` | 如 `excited`、`happy`、`surprised`、`proud` |
| 日常分享 | `send_routine_share(routine_type)` | 如 `morning_greeting`、`evening_greeting`、`meal_time`、`weekend` |

**频率限制**：
- `SHARE_COOLDOWN_SECONDS = 3600`（1 小时）：同一角色对同一用户的最小分享间隔；
- `DAILY_SHARE_LIMIT = 5`：单角色每日最大主动分享次数，防刷屏。

**QQ 推送链路**：
1. `CharacterTickEngine._maybe_proactive_share` 调用 `ProactiveSharingService.evaluate_and_share`；
2. 成功后调用 `_push_share_to_qq(character_id, content)`；
3. 查询 `conversations` 表中 `platform=qq` 的活跃会话，提取 `user_id`（格式 `qq_{qq_number}`）；
4. 对每个 QQ 用户调用 `OneBotAdapter.push_share(user_id=qq_number, message=content)`；
5. `push_share` 获取第一个活跃 OneBot 连接，通过 `send_private_msg` action 推送。

---

## 五、QQ 接入架构

### 5.1 OneBot 反向 WebSocket

QQ 接入采用 **OneBot v11/v12 反向 WebSocket** 协议，由 `OneBotAdapter` 实现（`src/adapters/onebot.py`）。

```text
┌──────────────────┐  反向 WebSocket（主动连接）   ┌──────────────────────┐
│  OneBot 实现      │ ──────────────────────────▶ │  AI Town Backend     │
│  (NapCat /        │  ws://host:port/ws/onebot/v12│  OneBotAdapter       │
│   Lagrange /      │ ◀────────────────────────── │  _ws_endpoint()      │
│   go-cqhttp)     │  action 响应 / 事件推送       │                      │
└──────────────────┘                              └──────────┬───────────┘
                                                             │
                                                             ▼
                                                   ┌──────────────────┐
                                                   │  handle_event()  │
                                                   │  事件分发：       │
                                                   │  - message        │
                                                   │  - meta_event     │
                                                   │  - notice         │
                                                   │  - request        │
                                                   └──────────┬───────┘
                                                              │
                                          ┌───────────────────┼───────────────────┐
                                          ▼                   ▼                   ▼
                                   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
                                   │ 私聊消息      │   │ 群聊消息      │   │ 元事件        │
                                   │ _handle_     │   │ _handle_     │   │ _handle_     │
                                   │ message_event│   │ message_event│   │ meta_event   │
                                   │ (private)    │   │ (group)      │   │ (heartbeat/  │
                                   └──────┬───────┘   └──────┬───────┘   │ lifecycle)   │
                                          │                  │           └──────────────┘
                                          └────────┬─────────┘
                                                   ▼
                                          ┌──────────────────┐
                                          │ MessageService   │
                                          │ handle_user_     │
                                          │ message(         │
                                          │   character_id,  │
                                          │   user_id=       │
                                          │     "qq_{qq}",   │
                                          │   platform="qq", │
                                          │   content)       │
                                          └──────────────────┘
```

**协议要点**：

| 项 | 说明 |
|----|------|
| 端点 | `/ws/onebot/v12`（FastAPI WebSocket 路由） |
| 连接方向 | OneBot 实现（NapCat / Lagrange 等）作为客户端**主动连接**本服务端 |
| 事件格式 | OneBot 实现逐条推送事件 JSON（文本帧） |
| 兼容性 | 同时兼容 OneBot 11（`post_type`）和 OneBot v12（`type`） |
| 用户映射 | OneBot `user_id` → 内部 `(user_id="qq_{user_id}", platform="qq")` |
| 角色路由 | 群-角色映射（`onebot_group_character_map`）→ 默认角色（`ONEBOT_DEFAULT_CHARACTER_ID`） |
| 回推消息 | 优先用 OneBot 11 的 `send_private_msg` / `send_group_msg`（主流实现支持更完善） |

**事件分发逻辑**：

```python
async def handle_event(self, event: dict, onebot_ws: WebSocket) -> None:
    # 兼容 OneBot 11 (post_type) 和 OneBot v12 (type)
    event_type = event.get("type") or event.get("post_type")

    if event_type == "message":
        await self._handle_message_event(event, onebot_ws)
    elif event_type == "meta_event":
        await self._handle_meta_event(event)  # 心跳/生命周期，仅日志
    elif event_type == "notice":
        logger.debug("onebot_notice_event_ignored")  # 通知事件忽略
    elif event_type == "request":
        logger.debug("onebot_request_event_ignored")  # 请求事件忽略
    else:
        logger.debug("onebot_unknown_event")
```

### 5.2 群聊智能回复架构

群聊智能回复是 QQ 接入的核心特性，通过四层决策实现"像真人一样选择性回复"，避免每条必回的刷屏感。

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    群聊消息到达（_handle_message_event）              │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 第 1 层：@ 机器人检测                        │
        │ _is_mentioned_self(event, self_id)          │
        │ - event.to_me == true（OneBot 实现已判定）   │
        │ - message 段数组含 at 段且 qq == self_id     │
        │ - raw_message 含 [CQ:at,qq=<self_id>] 码    │
        │                                              │
        │ 命中 → 直接回复（移除 @ 前缀，保留实际内容）  │
        │ 未命中 → 进入第 2 层                          │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 第 2 层：关键词命中（角色名）                 │
        │ MessageService.should_reply_in_group()      │
        │ if character_name in text:                  │
        │     return True, "name_mentioned"            │
        │                                              │
        │ 命中 → 直接回复（reason=name_mentioned）      │
        │ 未命中 → 进入第 3 层                          │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 第 3 层：启发式规则（概率回复）               │
        │ ┌─ 3a. 疑问句（含 ?/？/吗/呢）               │
        │ │    40% 概率回复（GROUP_REPLY_PROBABILITY_  │
        │ │    CAP = 0.4）                             │
        │ │    reason="question_heuristic"             │
        │ │                                            │
        │ ├─ 3b. 情绪强烈（含 !/！/[CQ:face）          │
        │ │    20% 概率回复                            │
        │ │    reason="emotion_heuristic"              │
        │ │                                            │
        │ └─ 未命中启发式 → 进入第 4 层                 │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 第 4 层：LLM 相关性判断                      │
        │ LLMClient.structured_output(judge_prompt,   │
        │   schema={should_reply, reason},            │
        │   model="chat")                              │
        │                                              │
        │ 判断标准（满足任一即应回复）：                │
        │ - 消息与角色兴趣/背景相关                     │
        │ - 消息在讨论角色关心的话题                    │
        │ - 消息是通用问候且角色性格外向                │
        │ - 消息内容有趣，角色自然会想回应              │
        │                                              │
        │ 不回复标准：                                  │
        │ - 消息与角色完全无关                          │
        │ - 消息是他人之间的私密对话                    │
        │ - 消息是纯技术讨论且角色无相关背景            │
        │                                              │
        │ ⚠️ 概率上限约束：                             │
        │ 即使 LLM 返回 should_reply=true，            │
        │ 仍受 GROUP_REPLY_PROBABILITY_CAP（40%）约束  │
        │ if should and random.random() > 0.4:         │
        │     return False, "llm_yes_but_capped"       │
        │                                              │
        │ 失败时默认不回复（fail-safe）                 │
        └─────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │ 决策结果 → 调用 MessageService 生成回复       │
        │ 或跳过（should_reply=False）                 │
        └─────────────────────────────────────────────┘
```

**配置开关**：

```python
# config.py
onebot_group_at_only: bool = False
# True: 仅在被 @ 时回复（保守模式）
# False: 读取所有群消息并智能决策是否回复（智能模式，默认）
```

**成本控制**：
- 每条群消息最多调用 1 次 LLM（`structured_output`）；
- LLM 判断失败时默认不回复（`fail-safe`），避免异常导致刷屏；
- 40% 概率上限确保即使 LLM 总是说"回复"，实际回复率也受控。

### 5.3 多段回复

长回复按段落拆分为多条消息依次发送，模拟真人打字节奏。由 `_split_message` 函数实现。

```text
完整回复文本
    │
    ▼
┌─────────────────────────────────────────────┐
│ 1. 按双换行（段落）拆分                       │
│    re.split(r"\n\s*\n", text)               │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 2. 单段超过 MAX_SEGMENT_LENGTH（500）时      │
│    按单换行继续拆分                          │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 3. 仍超长则硬切分（每 500 字一段）            │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ 依次发送，段间间隔 SEGMENT_SEND_INTERVAL     │
│ （0.6 秒），模拟真人打字                     │
│                                              │
│ for idx, seg in enumerate(segments):        │
│     await _send_single(seg)                  │
│     if idx < len(segments) - 1:              │
│         await asyncio.sleep(0.6)             │
└─────────────────────────────────────────────┘
```

**拆分策略示例**：

```text
输入：
"今天天气真好呢！\n\n我去咖啡馆坐了一会儿，
点了一杯拿铁。\n\n对了，你最近怎么样？
有没有什么有趣的事情想分享给我听？"

拆分结果（3 段）：
1. "今天天气真好呢！"
2. "我去咖啡馆坐了一会儿，点了一杯拿铁。"
3. "对了，你最近怎么样？有没有什么有趣的事情想分享给我听？"

发送时序：
t=0s     发送第 1 段
t=0.6s   发送第 2 段
t=1.2s   发送第 3 段
```

### 5.4 主动分享推送

主动分享推送是角色主动向 QQ 用户发起私聊消息的链路，无需用户先发消息。

```text
┌─────────────────────────────────────────────┐
│ Character Tick                               │
│ decision.proactive_share_intent == True      │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ CharacterTickEngine._maybe_proactive_share  │
│ 调用 ProactiveSharingService.evaluate_and_  │
│ share(character_id, action, state)          │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ ProactiveSharingService 内部：               │
│ 1. _evaluate_intent 评估意图（Action+情绪）  │
│ 2. _check_cooldown 检查冷却（1h）            │
│ 3. _get_today_share_count 检查日限额（5 次） │
│ 4. _generate_share_content LLM 生成文案      │
│ 5. _deliver_share 写入 messages + WS 推送    │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ CharacterTickEngine._push_share_to_qq       │
│ 查询 conversations 表 platform=qq 的会话     │
│ 提取 user_id（格式 qq_{qq_number}）         │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ OneBotAdapter.push_share(                   │
│     user_id=qq_number,                      │
│     group_id=None,                          │
│     message=content)                        │
│                                             │
│ 获取第一个活跃 OneBot 连接                   │
│ 调用 send_message → _split_message 拆分     │
│ → send_private_msg action 推送              │
└─────────────────────────────────────────────┘
```

**关键设计**：
- 分享失败不中断 Tick 主流程（`try/except` 兜底）；
- `push_share` 使用第一个活跃连接（OneBot 实现通常只有 1 个连接）；
- 推送前检查 `WebSocketState.CONNECTED`，连接断开时跳过而非报错；
- 多段分享同样走 `_split_message`，保持与对话回复一致的打字节奏。

---

## 六、LLM 客户端架构

LLM 客户端由 `LLMClient` 类实现（`src/llm/client.py`），统一封装 OpenAI 兼容 API + LangChain，支持文本对话、多模态、结构化输出、向量嵌入、图像生成、视频生成六种能力。

### 6.1 模型分层

```text
┌─────────────────────────────────────────────────────────────────────┐
│                       LLMClient 模型分层                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  chat 模型（agnes-2.0-flash / gpt-4o-mini）                │   │
│  │  - 日常对话（MessageService）                                │   │
│  │  - 结构化决策（CharacterTickEngine._decide）                │   │
│  │  - 群聊智能回复判断（should_reply_in_group）                │   │
│  │  - 上下文压缩（_maybe_compress_context）                    │   │
│  │  - 反思归纳（ReflectionService._do_reflection）             │   │
│  │  - 分享文案生成（ProactiveSharingService）                  │   │
│  │  - 图像理解（multimodal_chat 原生支持 image_url）           │   │
│  │  端点：/v1/chat/completions（LangChain ChatOpenAI）         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  strong 模型（agnes-image-2.1-flash / gpt-4o）              │   │
│  │  - 图像生成（generate_image）                                │   │
│  │  端点：/v1/images/generations                                │   │
│  │  支持：1K/2K/3K/4K 尺寸，多种宽高比，图生图                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  flash 模型（agnes-video-v2.0 / gpt-3.5-turbo）            │   │
│  │  - 视频生成（generate_video，异步任务）                      │   │
│  │  端点：POST /v1/videos 创建任务                              │   │
│  │        GET /agnesapi?video_id=<ID> 轮询结果                  │   │
│  │  轮询：间隔 5s，最大 120 次（约 10 分钟超时）                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  embedding 模型（text-embedding-3-small）                   │   │
│  │  - 向量嵌入（EmbeddingWorker 批量生成）                      │   │
│  │  - 多模态嵌入（embed_multimodal，文本+图像）                 │   │
│  │  端点：/v1/embeddings                                        │   │
│  │  支持独立 API Key + URL（embedding_model_key/url）           │   │
│  │  兼容 OpenRouter 多模态格式                                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**配置项**（`config.py`）：

```python
openai_api_key: str
openai_base_url: str = "https://api.openai.com/v1"
model_chat: str = "gpt-4o-mini"           # 对话+结构化决策
model_strong: str = "gpt-4o"              # 图像生成
model_flash: str = "gpt-3.5-turbo"        # 视频生成
model_embedding: str = "text-embedding-3-small"
embedding_model_key: str | None = None    # Embedding 专用 API Key
embedding_model_url: str | None = None    # Embedding 专用 URL
llm_timeout: int = 30
llm_max_retries: int = 2
embedding_dim: int = 1536
```

### 6.2 调用方式

| 方法 | 用途 | 模型 | 返回 |
|------|------|------|------|
| `chat(prompt, model="chat")` | 文本对话 | chat | `str` |
| `multimodal_chat(content, model=None)` | 多模态对话（文本+图像理解） | chat | `str` |
| `structured_output(prompt, schema, model="chat")` | 结构化输出（JSON Schema） | chat | `dict` |
| `multimodal_structured_output(content, schema, model=None)` | 多模态结构化输出 | chat | `dict` |
| `embed(text)` | 文本向量嵌入 | embedding | `list[float]` |
| `embed_multimodal(text, image_url)` | 多模态向量嵌入 | embedding | `list[float]` |
| `generate_image(prompt, size, ratio, image)` | 图像生成 | strong | `str`（URL/Base64） |
| `generate_video(prompt, image, ...)` | 视频生成（异步轮询） | flash | `str`（视频 URL） |

**结构化输出实现**：

```python
async def structured_output(
    self, prompt: str, schema: dict[str, Any], model: str = "chat"
) -> dict[str, Any]:
    # 1. JSON Schema → Pydantic 模型（动态创建）
    pydantic_model = self._schema_to_pydantic(schema)
    # 2. LangChain with_structured_output 绑定
    structured_llm = self.chat_llm.with_structured_output(pydantic_model)
    # 3. 调用 LLM
    result = await structured_llm.ainvoke(prompt)
    # 4. Pydantic 模型 → dict 返回
    return result.model_dump() if isinstance(result, BaseModel) else result
```

**视频生成轮询**：

```python
async def generate_video(self, prompt: str, ...) -> str:
    # 1. POST /v1/videos 创建任务
    create_resp = await client.post(f"{base_url}/videos", json=body, headers=...)
    video_id = create_resp.json().get("video_id")

    # 2. 轮询 GET /agnesapi?video_id=<ID>
    for attempt in range(_VIDEO_MAX_POLLS):  # 最大 120 次
        await asyncio.sleep(_VIDEO_POLL_INTERVAL)  # 间隔 5s
        resp = await client.get(f"{api_base}/agnesapi", params={"video_id": video_id})
        data = resp.json()
        if data["status"] == "completed":
            return data["url"]
        if data["status"] == "failed":
            raise RuntimeError(f"video_generation_failed: {data.get('error')}")

    raise TimeoutError(f"video_poll_timeout: video_id={video_id}")
```

### 6.3 Token/Cost 埋点

每次 LLM 调用都会记录 Prometheus 指标 + Langfuse 追踪：

```python
# chat() 方法的埋点示例
start_perf = time.perf_counter()
try:
    response = await self.chat_llm.ainvoke(prompt)
    elapsed = time.perf_counter() - start_perf

    # Prometheus 指标
    LLM_CALL_TOTAL.labels(model=model, status="success").inc()
    LLM_CALL_DURATION.labels(model=model).observe(elapsed)

    # 提取 token 用量（LangChain response_metadata）
    meta = response.response_metadata or {}
    token_usage = meta.get("token_usage") or meta.get("usage") or {}
    prompt_tokens = int(token_usage.get("prompt_tokens", 0))
    completion_tokens = int(token_usage.get("completion_tokens", 0))
    total_tokens = prompt_tokens + completion_tokens

    if total_tokens > 0:
        LLM_TOKENS_USED.labels(model=model, type="prompt").inc(prompt_tokens)
        LLM_TOKENS_USED.labels(model=model, type="completion").inc(completion_tokens)
        # 费用估算：agnes-2.0-flash 约 $0.5/M input, $1.5/M output
        estimated_cost = (prompt_tokens * 0.0000005 + completion_tokens * 0.0000015)
        LLM_COST_TOTAL.inc(estimated_cost)

    # Langfuse 追踪
    trace_llm_call(
        model=model, prompt=prompt, response=content,
        tokens=total_tokens, latency_ms=int(elapsed * 1000),
    )
    return content
except Exception:
    LLM_CALL_TOTAL.labels(model=model, status="failed").inc()
    raise
```

**成本控制链路**（在 `MessageService._generate_reply` 中）：

```python
# 1. 熔断器检查（前置）
breaker = get_circuit_breaker()
if breaker and not await breaker.can_execute():
    return DEFAULT_ERROR_REPLY, 0, 0.0, "circuit_open"

# 2. 预算检查（前置）
budget_mgr = get_budget_manager()
if budget_mgr:
    budget_status = await budget_mgr.check_budget()
    if budget_status["exceeded"]:
        return DEFAULT_ERROR_REPLY, 0, 0.0, "budget_exceeded"

# 3. 调用 LLM
response = await self.llm.chat(prompt, model="chat")

# 4. 记录用量（后置）
if budget_mgr:
    await budget_mgr.record_usage(estimated_tokens, estimated_cost)
if breaker:
    await breaker.record_success()
```

---

## 七、数据架构

### 7.1 数据库设计原则

| 原则 | 实现 | 收益 |
|------|------|------|
| UUID v7 主键 | `pg_uuidv7` 扩展，`id UUID DEFAULT uuidv7()` | 时间有序，B-tree 顺序追加，页分裂少；防枚举；分布式友好 |
| TIMESTAMPTZ 时间字段 | 所有 `created_at` / `updated_at` / `timestamp` / `due_at` 统一 `TIMESTAMP(timezone=True)` | 时区一致，可直接用 PG 时间函数（`date_trunc`、`extract`） |
| pgvector halfvec | `HALFVEC(settings.embedding_dim)` + HNSW 索引 | 比 `vector` 节省 50% 存储，召回率损失可忽略（< 1%） |
| HNSW 索引 | `m=16, ef_construction=64, ef_search=40` | 检索 p95 < 30ms，支持 10M 级向量 |
| HASH 分区 | `memory_episodes` 按 `character_id` HASH 16 分区 | 分区裁剪避免全局扫描；写入并行；单分区索引更小 |
| 事件溯源 | `world_events`（差分）+ `world_snapshots`（快照） | 冷启动恢复时间恒定；事件可回放审计 |
| 事件幂等 | `UNIQUE(tick_id, event_type, event_key)` | Tick 重试/服务重启不产生重复事件 |
| 复合外键 | `reflection_sources(memory_id, memory_character_id)` 引用 `memory_episodes(id, character_id)` | 分区表作为外键父表，删除记忆时自动级联清理关联 |

### 7.2 混合数据访问策略

本项目重度使用 pgvector、JSONB、数组、分区表、HNSW 索引，采用 **ORM + 原生 SQL 混合策略**。

| 场景 | 工具 | 理由 |
|------|------|------|
| 模型定义 | SQLAlchemy 2.0 `Declarative Base` | 类型安全，IDE 提示 |
| 迁移管理 | alembic | Schema 版本化 |
| 简单 CRUD | ORM `session.add()` / `repo.get_by_id()` | 简洁，自动参数化 |
| 关系加载 | `selectinload` 防 N+1 | ORM 原生支持 |
| 向量检索 | 原生 SQL `text()` | `<=>` 算子、`SET hnsw.ef_search`、混合排序 CTE |
| HNSW 索引创建 | 原生 SQL DDL | ORM 不支持 HNSW 参数 |
| 分区表 DDL | 原生 SQL | ORM 不支持分区语法 |
| 批量写入 | 原生 SQL `COPY` 协议 | 性能最优 |
| 复杂分析查询 | 原生 SQL（窗口函数、物化视图） | 可读性更好 |

**代码组织示例**：

```python
# db/repositories/memory_repo.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ✅ 简单 CRUD 用 ORM
    async def add(self, ep: MemoryEpisode) -> MemoryEpisode:
        self.session.add(ep)
        await self.session.flush()
        return ep

    # ✅ 向量检索用原生 SQL（含 ef_search 调优 + 混合排序 CTE）
    async def search_hybrid(
        self, character_id: UUID, query_vec: list[float], top_k: int = 10
    ) -> list[dict]:
        stmt = text("""
            SET LOCAL hnsw.ef_search = :ef_search;
            WITH candidates AS (
                SELECT id, content, importance, timestamp,
                       1 - (embedding <=> :q_vec) AS sim_score
                FROM memory_episodes
                WHERE character_id = :cid
                ORDER BY embedding <=> :q_vec
                LIMIT :limit
            )
            SELECT id, content,
                   sim_score * 0.6 + importance * 0.05
                   + EXTRACT(EPOCH FROM (now() - timestamp)) / 86400.0 * (-0.05) AS final_score
            FROM candidates
            ORDER BY final_score DESC
            LIMIT :top_k;
        """)
        result = await self.session.execute(stmt, {
            "cid": character_id, "q_vec": str(query_vec),
            "limit": top_k * 3, "top_k": top_k, "ef_search": 40,
        })
        return [dict(r._mapping) for r in result]
```

**收益**：
- 向量与复杂查询**零抽象损失**，性能等同纯 asyncpg；
- 简单 CRUD 享受 ORM 的类型安全与迁移管理；
- HNSW/分区/CTE 等高级特性可直接使用，不被 ORM 限制；
- 团队心智负担可控：90% 代码用 ORM，10% 性能/向量热点用原生 SQL。

### 7.3 核心数据模型

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          核心数据模型                               │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────┐  1:1   ┌──────────────────┐
│   characters     │◀──────▶│ character_states │
│   角色档案        │        │ 角色实时状态      │
│   - id (UUID v7) │        │ - location       │
│   - name         │        │ - stamina/satiety│
│   - age          │        │ - mood           │
│   - occupation   │        │ - money          │
│   - traits(JSONB)│        │ - phone_battery  │
│   - backstory    │        │ - social_energy  │
│   - is_active    │        └──────────────────┘
└────────┬─────────┘
         │ 1:N
         ▼
┌──────────────────────────────────────────────────┐
│   memory_episodes（HASH 分区 16，halfvec 向量）  │
│   - id (UUID v7)                                 │
│   - character_id (分区键，FK ON DELETE CASCADE)   │
│   - content (Text)                               │
│   - embedding (HALFVEC(1536), nullable)          │
│   - materialized (bool，embedding 是否已生成)     │
│   - importance (1-10)                            │
│   - timestamp (TIMESTAMPTZ)                      │
│   - action_id / location                         │
│   - related_characters (UUID[])                  │
│   - is_reflected (bool，部分索引)                 │
│   - fail_count / next_retry_at（熔断+退避）       │
│   - source_type (action/conversation/reflection) │
│                                                  │
│   索引：                                          │
│   - HNSW(embedding halfvec_ip_ops) 父表创建      │
│   - (character_id, timestamp)                    │
│   - (character_id, importance)                   │
│   - 部分索引 is_reflected=FALSE                  │
│   - 部分索引 materialized=FALSE AND fail_count<5 │
└──────────────────────────────────────────────────┘
         │
         │ N:1（通过 reflection_sources 中间表）
         ▼
┌──────────────────┐  N:N   ┌──────────────────────────────────────┐
│   reflections    │◀──────▶│   reflection_sources                 │
│   反思记录        │        │   反思来源中间表                      │
│   - id (UUID v7) │        │   - reflection_id (FK CASCADE)       │
│   - character_id │        │   - memory_id                        │
│   - content      │        │   - memory_character_id              │
│   - created_at   │        │   - 复合外键引用 memory_episodes       │
│                  │        │     (id, character_id) ON DELETE     │
│                  │        │     CASCADE                          │
└──────────────────┘        └──────────────────────────────────────┘

┌──────────────────┐  1:N   ┌──────────────────┐
│  conversations   │◀──────▶│    messages      │
│  会话表           │        │   消息表          │
│  - id (UUID v7)  │        │ - id (UUID v7)   │
│  - character_id  │        │ - conversation_id│
│  - user_id       │        │ - sender         │
│  - platform      │        │   (user/char/sys)│
│  - context(JSONB)│        │ - content        │
│    (压缩摘要)     │        │ - tokens / cost  │
│  - last_msg_at   │        │ - extra_data     │
└──────────────────┘        │   (JSONB,        │
                            │    share_type 等) │
                            └──────────────────┘

┌──────────────────────────────┐  ┌──────────────────────────────┐
│   world_events（差分事件）    │  │  world_snapshots（完整快照）  │
│   - id (UUID v7)             │  │  - id (UUID v7)              │
│   - tick_id (BigInt)         │  │  - tick_id (BigInt)          │
│   - event_type               │  │  - world_time (TIMESTAMPTZ)  │
│     (time/weather/scene/     │  │  - weather                   │
│      resource/event)         │  │  - locations (JSONB)         │
│   - event_key (默认 default) │  │  - resources (JSONB)         │
│   - payload (JSONB)          │  │  - active_events (JSONB)     │
│   - created_at (TIMESTAMPTZ) │  │  - created_at (TIMESTAMPTZ)  │
│                              │  │                              │
│   UNIQUE(tick_id, type, key) │  │  每 1000 Tick 存一次         │
│   幂等保证                    │  │  冷启动恢复基线              │
└──────────────────────────────┘  └──────────────────────────────┘

┌──────────────────┐         ┌──────────────────┐
│  action_records  │         │     plans        │
│  行为记录表       │         │   计划表          │
│  - id (UUID v7)  │         │ - id (UUID v7)   │
│  - character_id  │         │ - character_id   │
│  - action_id     │         │ - type / title   │
│  - action_name   │         │ - status         │
│  - params (JSONB)│         │ - priority       │
│  - reason        │         │ - progress       │
│  - result        │         │ - deadline       │
│  - duration_min  │         │ - description    │
│  - location      │         └──────────────────┘
│  - related_chars │
│  - timestamp     │
└──────────────────┘
```

---

## 八、可观测性架构

可观测性覆盖三个维度：**指标（Metrics）**、**日志（Logs）**、**链路追踪（Traces）**。

### 8.1 Prometheus 指标

指标定义在 `src/observability/metrics.py`，通过 `/metrics` 端点暴露给 Prometheus 抓取。

#### World Tick 指标

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `ai_town_world_tick_duration_seconds` | Histogram | — | World Tick 执行耗时（buckets: 0.1/0.5/1/2/5/10/30） |
| `ai_town_world_tick_total` | Counter | — | World Tick 总执行次数 |
| `ai_town_world_tick_errors_total` | Counter | — | World Tick 错误次数 |
| `ai_town_world_tick_id` | Gauge | — | 当前 World Tick ID |

#### Character Tick 指标

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `ai_town_character_tick_duration_seconds` | Histogram | — | 单个角色 Tick 执行耗时 |
| `ai_town_character_tick_total` | Counter | `character_id` | 角色 Tick 总执行次数 |
| `ai_town_character_tick_errors_total` | Counter | `character_id` | 角色 Tick 错误次数 |

#### LLM 指标

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `ai_town_llm_call_total` | Counter | `model`, `status` | LLM 调用总次数 |
| `ai_town_llm_call_duration_seconds` | Histogram | `model` | LLM 调用耗时 |
| `ai_town_llm_tokens_total` | Counter | `model`, `type` | LLM token 消耗（prompt/completion） |
| `ai_town_llm_cost_total_usd` | Counter | — | LLM 总费用（USD） |

#### 消息处理指标

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `ai_town_message_processed_total` | Counter | `platform`, `status` | 消息处理总次数 |
| `ai_town_message_processing_duration_seconds` | Histogram | — | 消息处理耗时 |

#### Action 指标

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `ai_town_action_execution_total` | Counter | `action_id`, `status` | Action 执行总次数 |
| `ai_town_action_execution_duration_seconds` | Histogram | `action_id` | Action 执行耗时 |

#### 系统状态指标

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `ai_town_active_characters` | Gauge | — | 活跃角色数量 |
| `ai_town_redis_connected` | Gauge | — | Redis 连接状态（1=连接, 0=断开） |
| `ai_town_http_request_duration_seconds` | Histogram | `method`, `path`, `status` | HTTP 请求耗时 |
| `ai_town_http_request_total` | Counter | `method`, `path`, `status` | HTTP 请求总次数 |
| `ai_town_db_query_duration_seconds` | Histogram | — | 数据库查询耗时 |

#### HTTP 中间件

`PrometheusMiddleware` 是纯 ASGI 实现（非 `BaseHTTPMiddleware`），兼容 WebSocket 连接：

```python
class PrometheusMiddleware:
    """纯 ASGI 中间件：记录 HTTP 请求耗时、状态码、路径"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan 等非 HTTP 请求直接透传，不记录指标
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        status_code = 500

        async def send_with_status(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            duration = time.perf_counter() - start_time
            HTTP_REQUEST_DURATION.labels(
                method=scope.get("method", "UNKNOWN"),
                path=scope.get("path", "/"),
                status=status_code,
            ).observe(duration)
            HTTP_REQUEST_TOTAL.labels(...).inc()
```

### 8.2 结构化日志

使用 `structlog` 输出 JSON 格式结构化日志，关键事件全部带上下文。

**日志初始化**（`src/observability/logging.py`）：

```python
# config.py
log_level: str = "info"
log_format: str = "json"  # json / console

# main.py lifespan
setup_logging(log_level=settings.log_level, log_format=settings.log_format)
```

**关键事件日志示例**：

```python
# World Tick
logger.info("world_tick_start", tick_id=self.tick_id)
logger.info("world_tick_end", tick_id=self.tick_id, updates=list(updates.keys()), duration_seconds=duration)
logger.error("world_tick_error", tick_id=self.tick_id, error=str(e), exc_info=True)

# Character Tick
logger.info("character_tick_start", character_id=str(character_id))
logger.info("character_tick_end", character_id=str(character_id), action=decision.action)
logger.warning("character_tick_rate_limited", character_id=str(char.id), backoff_multiplier=backoff_multiplier)

# 消息处理
logger.info("message_handled", conversation_id=str(conv.id), character_id=str(cid),
            user_id=user_id, reply_length=len(reply_text), tokens=tokens, cost=cost, error=error)
logger.warning("prompt_injection_blocked", character_id=str(cid), user_id=user_id, pattern=matched_pattern)

# OneBot
logger.info("onebot_message_received", detail_type=detail_type, user_id=user_id,
            group_id=group_id, raw_message=raw_message[:100], is_group=is_group)
logger.info("onebot_group_smart_reply", group_id=group_id, user_id=user_id, reason=reason)

# 主动分享
logger.info("proactive_share_sent", character_id=str(cid), character_name=character.name,
            content_length=len(content), recipients=recipients, trigger_action=action.action_id)
```

**日志聚合**：通过 Grafana Alloy 采集，发送到 Loki，与 Grafana 面板统一查询。

### 8.3 Langfuse 追踪

Langfuse 用于 LLM 调用、Character Tick、World Tick 的链路追踪，提供 Prompt/Token/Cost 审计能力。

**初始化**（`src/observability/langfuse_tracing.py` + `langfuse_integration.py`）：

```python
# config.py
langfuse_host: str | None = None
langfuse_public_key: str | None = None
langfuse_secret_key: str | None = None

# main.py lifespan
setup_langfuse()
```

**追踪函数**：

```python
# src/observability/langfuse_tracing.py

def trace_llm_call(model: str, prompt: str, response: str, tokens: int, latency_ms: int):
    """记录 LLM 调用到 Langfuse"""
    # 创建 observation，含 prompt/response/tokens/latency

def trace_character_tick(character_id: str, action: str, duration_ms: int):
    """记录 Character Tick 到 Langfuse"""
    # 创建 span，关联角色 ID 与执行的 action

def trace_world_tick(tick_id: int, duration_ms: int):
    """记录 World Tick 到 Langfuse"""
```

**关闭时刷新**：

```python
# main.py lifespan shutdown 阶段
from src.observability.langfuse_tracing import flush_langfuse
flush_langfuse()  # 确保追踪数据已发送
```

**链路关联**：
- LLM 调用 → Character Tick → World Tick 形成层级 span；
- 通过 `character_id` / `tick_id` 关联同一角色的多次决策；
- Langfuse 面板可查看每次 LLM 调用的完整 Prompt、Response、Token 用量与耗时。

---

## 九、关键技术决策

### 9.1 为什么用 UUID v7 而不是 v4

**问题**：UUID v4 完全随机，作为聚簇主键时存在严重问题。

| 问题 | 影响 |
|------|------|
| B-tree 页分裂 | 随机插入导致频繁页分裂，索引碎片化 |
| 缓存局部性差 | 新数据散落在不同数据页，cache hit rate 低 |
| 写入性能衰减 | 数据量增大后插入性能明显下降（可达 30%–50%） |
| WAL 写放大 | 随机写入产生更多 WAL 日志 |

**决策**：使用 UUID v7（RFC 9562），前 48 位为毫秒级 Unix 时间戳，剩余位随机。

| 维度 | UUID v4 | UUID v7 | BIGINT IDENTITY |
|------|---------|---------|-----------------|
| 有序性 | 完全随机 | 时间单调递增 | 完全顺序 |
| 索引友好 | 差 | 好 | 最好 |
| 防枚举 | 是 | 是（部分） | 否 |
| 分布式友好 | 是 | 是 | 否（需中心化） |
| 体积 | 16 字节 | 16 字节 | 8 字节 |

**实现**：PG 17 使用 `pg_uuidv7` 扩展，应用层用 `uuid6` 库兜底。

### 9.2 为什么用 pgvector halfvec 而不是 vector

**决策**：使用 `HALFVEC(dim)` 而非 `VECTOR(dim)`。

| 维度 | vector (float32) | halfvec (float16) |
|------|------------------|-------------------|
| 存储 | 4 字节/维 | 2 字节/维（节省 50%） |
| 召回率 | 基准 | 损失 < 1%（可忽略） |
| 索引内存 | 基准 | 减半 |
| HNSW 构建速度 | 基准 | 提升 20%–30% |

在 1536 维（text-embedding-3-small）下，单条记忆向量从 6KB 降至 3KB，1000 万条记忆节省约 30GB 存储。

### 9.3 为什么用 HASH 分区而不是 RANGE 分区

**决策**：`memory_episodes` 按 `character_id` HASH 分区（16 分区）。

| 维度 | RANGE 分区（按时间） | HASH 分区（按 character_id） |
|------|---------------------|-----------------------------|
| 查询模式 | 时间范围查询 | 按角色查询（`WHERE character_id = :cid`） |
| 分区裁剪 | 按时间裁剪 | 按 character_id 裁剪，单角色查询只扫一个分区 |
| 热点问题 | 当前月份分区热点 | 角色均匀分散到各分区，无热点 |
| 扩容 | 可新增月份分区 | 需全表重分布（16 分区固定） |
| 数据倾斜 | 按时间均匀 | 角色数多时均匀；角色数少时可能倾斜 |

本项目查询模式以"按角色检索记忆"为主（`RetrievalService.search(character_id, ...)`），HASH 分区能精确裁剪到单分区，避免全局扫描。

**索引传播**：HNSW 索引在父表创建，PostgreSQL 自动传播到所有子分区（含未来新增），无需手动维护。

### 9.4 为什么用 Redis 分布式锁做 Leader Election

**问题**：后端可水平扩展，但 World Tick 若多实例并发执行会导致重复推进。

**决策**：Redis 分布式锁选主（`SET NX EX`）。

| 维度 | Redis 锁 | etcd / Consul | 数据库锁 |
|------|----------|---------------|----------|
| 运维复杂度 | 低（已有 Redis） | 高（额外组件） | 低 |
| 性能 | 高（内存操作） | 中 | 低（磁盘） |
| 故障转移 | TTL 自动过期（30s） | 租约机制 | 会话级 |
| 一致性 | AP（最终一致） | CP | CP |

本项目已有 Redis 依赖（实时状态缓存），复用 Redis 锁无需引入额外组件。TTL 30s + 续租间隔 10s 保证故障窗口 < 35s，对世界推进（30s/Tick）影响可接受。

**实现**：

```python
LOCK_KEY = "world:tick:leader"
LOCK_TTL = 30  # 秒
LOCK_RENEW_INTERVAL = 10  # 秒

# 获取锁
acquired = await redis.set(LOCK_KEY, f"leader:{now}", ex=LOCK_TTL, nx=True)
# 续租
renewed = await redis.expire(LOCK_KEY, LOCK_TTL)
# 释放（持锁实例关闭时）
await redis.delete(LOCK_KEY)
```

### 9.5 为什么事件幂等用 UNIQUE(tick_id, event_type, event_key)

**问题**：World Tick 可能因服务重启、网络重试等原因重复执行，导致 `world_events` 表产生重复事件。

**决策**：`UNIQUE(tick_id, event_type, event_key)` 约束保证幂等。

| 字段 | 作用 |
|------|------|
| `tick_id` | 标识哪个 Tick 产生的事件 |
| `event_type` | 事件类型（time/weather/scene/resource/event） |
| `event_key` | 事件键（默认 `default`，实体级事件用实体 ID） |

**为什么用三元组而非单一 `event_id`**：
- 支持同一 Tick 同一类型的多条事件（`event_key` 区分不同实体，如多个场景的状态变化）；
- 业务语义清晰，可直接从约束推断幂等边界；
- 重试时 `INSERT ON CONFLICT DO NOTHING` 自动跳过已存在事件。

### 9.6 为什么 memory_episodes 用异步 embedding

**问题**：每个 Character Tick 都会写入新记忆，若同步调用 LLM `embed()` 生成向量，会阻塞 Tick 主循环（embedding API 调用约 100–500ms）。

**决策**：记忆写入时 `embedding=NULL, materialized=false`，由 `EmbeddingWorker` 后台批量向量化。

**收益**：
- Tick 主循环耗时降低（省去 embedding 调用）；
- 批量调用 embedding API，提升吞吐（batch_size=20）；
- 支持多 Worker 实例并行处理（`FOR UPDATE SKIP LOCKED`）；
- 失败容错：单条 embedding 失败不影响其他记忆，`fail_count` 达 5 次后熔断，`next_retry_at` 指数退避。

**实现**：

```python
# EpisodeService.create_episode
episode = MemoryEpisode(
    character_id=character_id,
    content=content,
    embedding=None,          # 异步 worker 生成
    materialized=False,      # 标记为未向量化
    importance=importance,
    ...
)
saved = await self.repo.add(episode)

# EmbeddingWorker._process_batch
episodes = await repo.fetch_unmaterialized(limit=20)
for episode in episodes:
    try:
        embedding = await self.llm_client.embed(episode.content)
        await repo.update_embedding(episode.id, episode.character_id, embedding)
    except Exception as e:
        await repo.mark_embedding_failed(episode.id, episode.character_id, str(e))
        if episode.fail_count + 1 >= 5:
            # 熔断，不再重试
            ...
```

### 9.7 为什么反思用 reflection_sources 中间表

**问题**：一条反思由多条记忆归纳而来，需要记录"哪些记忆参与了这次反思"。

**方案对比**：

| 方案 | 优点 | 缺点 |
|------|------|------|
| `reflections.source_memory_ids UUID[]` | 查询简单 | UUID 数组无法建立外键约束，删除记忆时关联不会自动清理 |
| `reflection_sources` 中间表 | 支持外键约束，自动级联 | 多一次 JOIN |

**决策**：使用 `reflection_sources` 中间表，通过复合外键引用 `memory_episodes(id, character_id)`，`ON DELETE CASCADE` 自动清理。

```sql
-- reflection_sources 表
reflection_id UUID FK → reflections(id) ON DELETE CASCADE
memory_id UUID
memory_character_id UUID
-- 复合外键引用 memory_episodes(id, character_id)
FOREIGN KEY (memory_id, memory_character_id)
  REFERENCES memory_episodes(id, character_id)
  ON DELETE CASCADE
```

**收益**：
- 删除记忆时，关联的 `reflection_sources` 行自动清理（避免悬空引用）；
- 删除反思时，关联的 `reflection_sources` 行自动清理；
- 支持反向查询："哪些反思引用了此记忆"（通过 `idx_refl_sources_memory` 索引）。

### 9.8 为什么群聊用智能回复而不是仅@回复

**问题**：QQ 群聊中，如果仅在 @机器人 时回复，角色会显得"冷漠"，缺乏存在感；如果每条必回，又会刷屏打扰用户。

**决策**：四层智能回复决策（@ → 关键词 → 启发式 → LLM 判断），受 40% 概率上限约束。

**收益**：
- **存在感**：角色名被提及时主动回复，让用户感受到角色"在场"；
- **真实感**：疑问句、情绪强烈时概率回复，模拟真人选择性参与对话；
- **成本可控**：40% 概率上限确保即使 LLM 总是说"回复"，实际回复率受控；
- **fail-safe**：LLM 判断失败时默认不回复，避免异常导致刷屏；
- **可配置**：`onebot_group_at_only` 开关支持保守模式（仅 @ 回复）。

**对比**：

| 策略 | 存在感 | 刷屏风险 | 成本 |
|------|--------|----------|------|
| 仅 @ 回复 | 弱 | 无 | 低 |
| 每条必回 | 强 | 高 | 高 |
| 智能回复（本项目） | 中 | 低 | 中 |

---

## 十、部署架构

### 10.1 单机部署 vs 多实例部署

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        单机部署（开发/小规模）                       │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  AI Town Backend（单实例）                                   │   │
│  │  - World Engine（Leader）                                    │   │
│  │  - Character Tick Engine                                     │   │
│  │  - MessageService                                            │   │
│  │  - OneBotAdapter                                             │   │
│  │  - EmbeddingWorker                                           │   │
│  │  - WebSocketManager                                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│       │              │              │                               │
│       ▼              ▼              ▼                               │
│  ┌────────┐    ┌────────┐    ┌────────┐                           │
│  │  PG 17 │    │ Redis  │    │  LLM   │                           │
│  └────────┘    └────────┘    └────────┘                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    多实例部署（生产/高可用）                         │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Backend 实例 A    │  │ Backend 实例 B    │  │ Backend 实例 C    │  │
│  │ - World Engine    │  │ - World Engine    │  │ - World Engine    │  │
│  │   (Leader ✅)     │  │   (Standby ⏸)    │  │   (Standby ⏸)    │  │
│  │ - Char Tick ✅    │  │ - Char Tick ✅    │  │ - Char Tick ✅    │  │
│  │ - MessageSvc ✅   │  │ - MessageSvc ✅   │  │ - MessageSvc ✅   │  │
│  │ - OneBotAdapter  │  │ - OneBotAdapter  │  │ - OneBotAdapter  │  │
│  │ - EmbedWorker ✅  │  │ - EmbedWorker ✅  │  │ - EmbedWorker ✅  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│           │                     │                     │             │
│           └─────────────┬───────┴─────────────────────┘             │
│                         ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  共享基础设施                                                │  │
│  │  ┌────────────┐  ┌────────────┐                            │  │
│  │  │  PG 17     │  │  Redis 8.0 │                            │  │
│  │  │  + PgBouncer│  │  (Leader   │                            │  │
│  │  │            │  │   选举锁)  │                            │  │
│  │  └────────────┘  └────────────┘                            │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 Leader Election 保证 World Tick 单实例

多实例部署时，World Tick 通过 Redis 分布式锁确保**仅在一个实例上运行**：

- 所有实例竞争 `world:tick:leader` 锁（`SET NX EX 30`）；
- 仅持锁实例（`is_leader=True`）执行 `_tick_loop`；
- 持锁实例宕机后，锁 TTL（30s）到期释放，其他实例竞争接管；
- 故障转移窗口约 35s（TTL + 5s 缓冲）。

**Character Tick 可多实例分担**：
- 每个实例独立运行 `_character_tick_loop`，获取所有活跃角色；
- 通过 `char:tick:lock:{cid}` 角色级锁确保同一角色不会被多实例并发 Tick；
- `asyncio.Semaphore` 限制单实例并发数（`character_max_concurrent=10`）；
- 多实例自然分担角色 Tick 负载（角色级锁互不冲突）。

**EmbeddingWorker 可多实例并行**：
- 使用 `FOR UPDATE SKIP LOCKED` 跳过被其他 Worker 锁定的行；
- 多 Worker 实例自然分担 embedding 任务，无冲突。

### 10.3 水平扩展能力

| 组件 | 可否水平扩展 | 机制 |
|------|--------------|------|
| World Engine | ❌ 单实例 | Redis 分布式锁选主 |
| Character Tick | ✅ 多实例分担 | 角色级 Redis 锁 |
| MessageService | ✅ 多实例 | 无状态，依赖 PG 事务 |
| OneBotAdapter | ⚠️ 受限 | OneBot 实现通常单连接，多实例时只有首个连接生效 |
| EmbeddingWorker | ✅ 多实例 | `SKIP LOCKED` 并行处理 |
| WebSocketManager | ⚠️ 受限 | Web 客户端连接需粘性路由（sticky session） |

### 10.4 容器化：docker-compose

开发与生产环境均通过 `docker-compose` 编排，包含以下服务：

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| `backend` | 自构建 | 8000 | AI Town Backend（FastAPI） |
| `postgres` | postgres:17 + pgvector + pg_uuidv7 | 5432 | 主数据库 |
| `redis` | redis:8.0-alpine | 6379 | 缓存/锁/实时状态 |
| `prometheus` | prom/prometheus | 9090 | 指标采集 |
| `grafana` | grafana/grafana | 3000 | 可视化面板 |
| `loki` | grafana/loki | 3100 | 日志聚合 |
| `alloy` | grafana/alloy | — | 日志采集器 |
| `jaeger` | jaegertracing/all-in-one | 16686 | 链路追踪 |
| `langfuse` | langfuse/langfuse | 3000 | LLM 追踪 |
| `napcat` | napcat/napcat | — | OneBot 实现（QQ 机器人） |

**PgBouncer 配置**（生产多实例必须）：

```ini
[databases]
ai_town = host=127.0.0.1 port=5432 dbname=ai_town

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
```

注意：transaction 模式下不支持 prepared statements，SQLAlchemy 需关闭 `prepared_statement_cache_size`。

### 10.5 启动顺序与依赖

`main.py` 的 `lifespan` 上下文管理器按以下顺序初始化：

```text
1. setup_logging        日志初始化（最先，确保后续日志可输出）
2. Redis 连接 + ping    失败则中断启动
3. 成本控制 + 速率限制   依赖 Redis
4. 预创建数据库分区     pre_create_partitions(3)，失败不中断
5. LLM 客户端初始化     失败则中断启动
6. Action Registry      注册所有 Action
7. EmbeddingWorker      后台任务启动
8. PartitionScheduler   每月 25 号 03:00 自动预创建分区
9. World Engine         Leader Election + Tick 循环
10. Character Tick      后台循环
11. Phase 2 模块        SceneLoader / ScheduleSystem / MovementSystem
12. WebSocketManager    /ws/chat/{character_id}
13. OneBotAdapter       /ws/onebot/v12

shutdown 顺序（逆序）：
1. flush_langfuse       刷新追踪缓冲区
2. onebot_adapter.stop  关闭 OneBot 连接
3. partition_scheduler.stop
4. embedding_worker.stop
5. character_tick_task.cancel
6. world_engine.stop    释放 Leader 锁
7. redis.close
```

> **运行时依赖容器**：`lifespan` 初始化的实例通过 `src/runtime.py` 的 `set_*` 方法写入，业务模块通过 `get_redis()` / `get_llm()` / `get_registry()` 等 getter 读取，消除对 `main.py` 的反向依赖（避免 `from src.main import ...` 循环导入）。REST 路由按资源拆分到 `src/api/` 下 11 个模块，由 `main.py` 聚合 `include_router` 注册；全局异常处理器在 `src/api/exceptions.py` 统一错误响应格式并附带 `trace_id`。

---

## 附录：相关文档

| 主题 | 文档 |
|------|------|
| 角色设计 | [character-design.md](character-design.md) |
| 小镇与场景 | [town-design.md](town-design.md) |
| 世界引擎与角色 Tick | [world-engine.md](world-engine.md) |
| Action 系统与执行闭环 | [action-system.md](action-system.md) |
| 记忆系统与 pgvector | [memory-system.md](memory-system.md) |
| 模块管理与 MCP | [module-system.md](module-system.md) |
| 数据模型 DDL | [data-model.md](data-model.md) |
| 部署与高可用 | [deployment.md](deployment.md) |
| Docker 部署指南 | [docker-deployment.md](docker-deployment.md) |
| 项目不足审查与改进 | [gap-analysis.md](gap-analysis.md) |
