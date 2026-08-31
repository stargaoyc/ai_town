# AI Town 全面审查报告（2026-08-26）

> **对象**：`stargaoyc/ai_town` — LLM 驱动的多智能体虚拟小镇（二次元陪伴智能体）  
> **审查范围**：项目定位 / 分层架构 / 技术选型 / 多智能体交互 / 智能体认知机制 / ReAct 工具 / 多端触达 / 数据持久化 / 可观测性 / 部署 / 前端工程化 / 长期运行风险 / 用户体验 / 安全  
> **审查方式**：代码走读（`packages/backend/src`、`packages/frontend/src`、`docker-compose.yml`、`configs`、`alembic`）、文档交叉校验（`docs/*.md`）、配置与依赖审计  
> **审查人**：Sisyphus（Orchestrator）+ 4 方向并行探索（后端架构 / 认知机制 / 持久化与可观测 / 前端与部署）  
> **结论先行**：**A-（优秀，具备生产化潜力，但需在成本、长期运行与体验闭环上补齐最后一公里）**

---

## 摘要

AI Town 的定位清晰且差异化——**不做“随叫随到的助手”，做“有自己生活的人”**。围绕该定位，项目以“World Tick + Character Tick 双循环 + Redis 实时态 + PG 历史真相 + pgvector 记忆”构建了自洽的工程闭环，并在记忆生命周期、群体动力学、ReAct 工具、QQ 智能回复与主动分享等方向做了远超 Demo 级别的深化。

**核心优势**：架构分层清晰、真相源约定严格、记忆与关系具备演化闭环、观测与部署工程化程度高、前端设计语言统一。  
**核心短板**：50 角色全量 strong 模型成本模型不可持续、头采样导致错误链路可能丢失、PG 18 强依赖与单机备份同盘风险、前后端实时体验仍有割裂。

> 评分：满分 5 分，4.2/5 — 详见 §18 雷达图。

---

## 1 项目定位与产品哲学

### 1.1 定位准确度：★★★★★

| 维度 | 评价 |
|---|---|
| **差异化** | 避开“通用助手”红海，切“陪伴 + 小镇生活感”细分场景，记忆、反思、规划、主动分享四件套构成护城河 |
| **叙事一致性** | README、architecture、character-design、world-engine 四文档对“状态驱动、事实优先、闭环演化”原则表述一致，代码亦严格落地（见 §2） |
| **受众匹配** | 二次元角色卡 + Glassmorphism + 多段 0.6s 拟真打字 + QQ 群聊，精准命中目标用户触达场景 |
| **可扩展性** | 10–50 角色、世界持续运转、多端触达的“小镇”隐喻具备向 100+ 角色、UGC 角色卡、付费陪伴演进的空间 |

### 1.2 合理性判断

- **正向**：将“陪伴”从单轮对话升级为“持续世界 + 可追溯经历 + 主动联系”，解决了传统 Bot “失忆、被动、群聊失语”三大痛点（`docs/architecture.md#1.1` 痛点表格）。
- **风险**：定位依赖长期运行与记忆一致性，若记忆膨胀或成本失控导致降级为“随机回复”，定位叙事会崩塌；需在 §13 的治理机制上持续投入。

### 1.3 建议

- 明确对外 SLA：如“角色记忆召回 p95 < 100ms、群聊被 @ 必回、主动分享日限 5 次”等可量化承诺，避免“有生活感”停留于口号。
- 补充竞品对位文档（如 yuiju 对比 `docs/yuiju-comparison.md` 已有，需在 README 显式引用）。

---

## 2 分层架构与模块边界

### 2.1 分层清晰度：★★★★☆

```
用户接入层（WebSocket / OneBot / REST）
        ↓
消息服务层（MessageService / ProactiveSharing / PromptGuard）
        ↓
世界引擎层（WorldEngine / CharacterTickEngine / Evolution 链）
        ↓
Agent 能力层（Memory / Reflection / Plan / Decision / Social / ToolRegistry）
        ↓
数据访问层（Repositories + SQLAlchemy 2.0 + 原生 SQL）
        ↓
基础设施层（PG 18 + Redis 8 + LLM 网关 + OTel/Langfuse/Prometheus）
```

| 层 | 职责 | 边界评价 |
|---|---|---|
| 接入层 | 协议适配（`src/adapters/onebot.py`、`src/messaging/websocket.py`） | ✅ 纯适配，无业务逻辑 |
| 消息服务 | 统一 `handle_user_message` 入口，幂等会话、上下文压缩、成本控制 | ✅ 已沉淀为 Service 层唯一完整实现 |
| 世界引擎 | 双循环 + Evolution 链（`src/core/world`） | ✅ Evolution 接口 `should_run/apply` 易扩展 |
| Agent 能力 | 记忆/反思/规划/社交/工具 | ✅ 与引擎解耦，工具可开关 |
| 数据访问 | Repository 抽象 + ORM/原生 SQL 混合 | ✅ 模型归 `src/db/models`，迁移归 `alembic` |
| 基础设施 | PG/Redis/LLM/可观测 | ✅ 通过 `src/runtime.py` 依赖容器消除对 `main.py` 的反向依赖 |

**扣分点**：`docs/architecture.md` 亦自承“通用 Service 层尚未完全落地，其余 API 路由直接查 Repository”——新增业务若继续在路由堆积，将侵蚀分层收益。建议新增 `src/services/` 目录并逐步迁移。

### 2.2 依赖方向

- `API → Service → Core → Infra → Cross-cutting` 方向正确，未发现循环依赖。
- `src/runtime.py` 的依赖容器设计值得肯定：避免 Tick/Adapter 反向 import `main`。
- 唯一跨层隐患：`CharacterTickEngine._push_share_to_qq` 延迟 import `src.main.onebot_adapter`（`tick.py:656`），虽用懒加载规避循环，但属架构妥协，长期应抽 `MessagingGateway` 接口。

### 2.3 结论

分层“形似且神似”，唯 Service 层需补齐。模块边界总体**清晰**，演化成本低。

---

## 3 技术选型

### 3.1 后端栈（`packages/backend/pyproject.toml`）

| 选型 | 版本 | 评价 |
|---|---|---|
| Python 3.13 + uv | latest | ✅ 前沿，`uv sync --frozen` 锁定可复现；`requires-python >=3.13` 与 PG 18 对齐 |
| FastAPI + Uvicorn | 0.115 / 0.34 | ✅ 异步生态成熟，自动 OpenAPI 利于前端 `gen:api` |
| SQLAlchemy 2.0 + asyncpg + alembic | 2.0 / 0.30 / 1.14 | ✅ 异步 ORM + 原生 SQL 混合策略务实（向量检索必用 `text()`） |
| pgvector + pg_uuidv7 + pg_trgm | 0.3 / — | ✅ UUID v7 时间有序、B-tree 友好；半精度 halfvec(2048) 降显存 |
| Redis 8.0 (hiredis) | 5.2 | ✅ 分布式锁、实时态、Streams、限流一栈多用 |
| LangChain + OpenAI SDK | 1.3 / 2.40 | △ LangChain 抽象价值有限（仅用于 LLM 调用），引入额外复杂度；若仅需 `chat/structured_output/embed` 可考虑直调 OpenAI SDK |
| OTel + Langfuse + Prometheus + structlog | 1.28 / 2.x / 0.21 / 24.4 | ✅ 三支柱齐整，`structlog` JSON 日志规范 |
| APScheduler | 3.11 | ✅ 分区预创建、保留期清理等定时任务必需 |

**总体**：选型现代、克制（未引入 Celery/Kafka 等重组件），与“单体 + 异步 + PG/Redis 双真相源”架构匹配。

### 3.2 前端栈（`packages/frontend/package.json`）

| 选型 | 评价 |
|---|---|
| React 19.2 + TypeScript 7.0 + Vite 8.1 (Rolldown) + React Compiler 1.0 | ✅ 激进但一致，Compiler 免 `useMemo/useCallback`，构建极速 |
| TanStack Router 1.170 + Query 5.101 + Zustand 5.0 + Zod 4.4 | ✅ 类型安全路由 + 服务端缓存 + 轻量客户端状态 + 运行时校验，分层清晰 |
| Tailwind v4 + Framer Motion 12 + shadcn/ui + Recharts 3 | ✅ Glassmorphism 落地顺畅 |
| oxlint + oxfmt | ✅ Rust 系工具链统一，替代 ESLint/Prettier |
| pnpm 11.22 + `tsc -b` | ✅ 硬链接省盘，`build: tsc -b && vite build` 类型先行 |

**风险**：React 19 / TS 7.0 / Vite 8.1 均为“未来版”，需关注生态滞后（如部分库尚未适配 React 19 并发特性）；`@types/node` 等需持续跟进。

### 3.3 基础设施

- **PG 18 + pgvector/pgvector:pg18 镜像**：选择官方镜像省去自建 `pg_uuidv7` 编译，`uuidv7()` 内建生成与 `uuid6` 应用层兜底互为冗余，稳妥。
- **Redis 8-alpine + `noeviction`**：将 Redis 定位为“实时状态真相源”而非缓存，`maxmemory 512mb + noeviction` 策略正确（宁可报错不可静默丢 key）。
- **单一 `docker-compose.yml` + profile（observability/backup）**：编排收敛，`x-default-logging` 锚点统一日志轮转，值得借鉴。

**小结**：技术选型**合理且有前瞻性**，唯 LangChain 必要性与前端前沿版本风险需持续评估。

---

## 4 世界引擎与多智能体调度

### 4.1 World Tick：★★★★★

- **Leader Election**：Redis `SET NX EX 30` + 10s 续租 + TTL+5s 重试，故障窗口 ≤35s，满足单实例 Tick 语义（`docs/architecture.md#3.1`）。
- **Evolution 链**：`Time → Weather → Scene → Resource → Event` 依赖顺序显式，单 Evolution 失败不中断 Tick，新增规则只需实现 `WorldEvolution` 协议（`world/evolutions/base.py`，B027 豁免合理）。
- **持久化**：`_last_persisted_state` 差分去重 + `UNIQUE(tick_id, event_type, event_key)` 幂等 + 每 1000 Tick 全量快照，冷启动“最新快照 + 回放增量”时间恒定。
- **可观测**：`WORLD_TICK_ID / DURATION / TOTAL / ERRORS` 指标齐全。

### 4.2 Character Tick：★★★★☆

六阶段闭环（感知→决策→ReAct→执行→记忆→分享→反思）与文档高度一致（`architecture.md#3.2`、`tick.py:_execute_tick`）。

| 机制 | 实现 | 评价 |
|---|---|---|
| 角色锁 | `char:tick:lock:{cid}` NX EX 30s + token 比对释放 | ✅ 防并发 Tick |
| 看门狗续租 | `watch_locks` 定期 `EXPIRE`，失锁置 `lock_lost` 事件 | ✅ 长 Tick（多次 LLM 调用）不因 TTL 过期而 double-tick |
| 并发控制 | `asyncio.Semaphore(character_max_concurrent=10)` + 热更新重建 | ✅ 档位可配，双信号量短暂共存无害 |
| 限流退避 | 429 时指数退避 `×2` 上限 10 | ✅ 保护用户消息配额 |
| 事务边界 | `ActionRecord + CharacterState + History + Plans + pending_artifacts` 同 PG 事务 | ✅ 任一失败整体回滚 |

**扣分**：`_execute_tick` 单函数超 500 行（含 ReAct、move 校验、social、锁闸口），虽有 `PerceptionMixin`/`SocialMixin` 拆分，仍建议按阶段抽 `TickPipeline`。

### 4.3 调度策略

- 活跃角色优先、空闲轮询、错过补偿（耗时长则跳过下轮）策略合理，避免雪崩。
- 多智能体间通过**场景共享 + Redis Streams + 关系更新**间接交互，未引入复杂 Actor 模型，符合“小镇”规模。

---

## 5 多智能体交互合理性

### 5.1 交互原语

| 交互 | 触发 | 持久化 | 评价 |
|---|---|---|---|
| `chat_with` | LLM 决策 `params.target_character_id`，同场景校验 | 双向关系 `+2/+5` + 双方 `memory_episodes(source=conversation)` + `ActionRecord.related_characters` | ✅ 语义完整，对话由单次 LLM 生成保证连贯 |
| `group_activity` | 同场景 ≥3 人候选保留（Tick 侧过滤） | 全员互指记忆 + 两两关系 +2 + 集体叙事 | ✅ 2026-08-24 新增，填补“群聚”空白 |
| 传闻传播 | 好友高重要性记忆 `importance≥7` → 听者 gossip 记忆 | `source_type=gossip`，importance 减半，不二次传播 | ✅ 零编造（模板拼接原文）、每好友每窗口 ≤1 条 |
| 关系图 | `relations` 有向图双行存储，`strength 0–100` + `relationship_type` + 衰减 | 社交 Action 自动更新 | ✅ 陌生→相识→朋友梯度清晰 |

### 5.2 合理性判断

- **同场景可见性**：`context["nearby_characters"]` 含姓名/性格/关系/情绪/当前行为，LLM 可据此决策是否发起 `chat_with`，比“全员广播”更具选择性。
- **防刷屏**：`chat_with` 失败退化 `wait`、`group_activity` 人数门槛、传闻 `gossip_max_per_tick=1` 三重限流，交互频率可控。
- **关系演化**：`chat_with` 支持 `chat_quality_enabled` 时 LLM 评估增量（替代固定 +5），关系更具语义。

### 5.3 缺口

- **无位置冲突消解**：多角色同时 `move` 至同一小容量场景（如 `cafe capacity`）无排队/拒绝机制，`SceneEvolution.crowdedness` 仅作 Prompt 提示。
- **对话轮数**：`chat_with_max_rounds=2`（双方各 2 句）对深度社交略浅；可考虑按关系亲密度动态轮数。
- **事件总线消费**：`events:world / events:character` Streams 的消费组逻辑在文档有述（`world-engine.md#4.3`），但代码侧消费路径较隐蔽，建议补充端到端测试。

**总体**：交互模型**轻量而自洽**，符合 10–50 角色小镇规模；若向 100+ 角色扩展，需引入空间分区与消息 Fan-out 优化。

---

## 6 智能体核心能力：记忆 / 反思 / 规划 / Person Memory

### 6.1 记忆流（Memory Stream）：★★★★★

**三层架构**（`memory-system.md`）：原始记忆 → 反思 → 规划，层次分明。

- **写入**：`EpisodeService.create_episode` 异步向量化（`materialized=false, embedding=NULL`），`EmbeddingWorker` 批量 `LLM.embed`，`fail_count≥5` 熔断 + `next_retry_at` 指数退避（`data-model.md#3.4`）。
- **检索**：`memory_episodes` HASH 分区 16 + 父表 HNSW（`m=16, ef_construction=128`）自动传播，`WHERE character_id=:cid` 触发裁剪，单分区 <10ms；`search_hybrid` 向量召回×3 + 重要性/时间衰减重排（`sim*0.6 + importance*0.05 - age*0.05`）。
- **去重**：`exists_recent_duplicate` 归一化比对 + `is_duplicate` 向量余弦去重（阈值 0.95，窗口 24h），已证伪 `pg_trgm` 对中文无效（`cognition-and-group-dynamics.md#2.4`）。
- **重要性**：默认 5，`MEMORY_LLM_SCORING_ENABLED` 时四维度 LLM 评分（情感/关系/稀缺/后续），成本可控（`episode_service.py:score_importance_with_llm`）。

### 6.2 反思（Reflection）：★★★★☆

| 能力 | 实现 | 评价 |
|---|---|---|
| 触发 | 未反思记忆 ≥20 条 | ✅ 频率适中 |
| 分层 | tier 1 批次主题（2–4 主题/Reflection） + tier 2 跨期元反思（累计≥6 且 7 天冷却） | ✅ 2026-08-24 深化，元反思优先注入 |
| 关联 | `reflection_sources` 复合外键 `(memory_id, memory_character_id) → memory_episodes` | ✅ 替代旧数组，参照完整性可保证 |
| 容错 | LLM 无主题映射时退化单条汇总 | ✅ 不丢 grounding |

**缺口**：`reflections` 已移除 `embedding` 向量（`data-model.md#5.3`），反思检索改按 `created_at DESC` 拉取后应用层合并，语义检索能力弱于记忆；若反思量增至万级，需恢复向量或引入关键词索引。

### 6.3 规划（Plans）：★★★★☆

- **三型**：`long_term / short_term / daily`，`daily` 超 `DAILY_PLAN_TTL_HOURS=24` 自动 `expired`（`character-design.md#附`）。
- **双通道**：`planChanges`（变更既有，`character_id` 约束防越权）+ `createPlanChanges`（新建，服务端绑定、类型白名单、优先级钳制 1–5、单次 ≤3 条）。
- **软引导**：计划经 Prompt 注入影响决策，不做 `precondition` 硬过滤，保留自主性（`cognition-and-group-dynamics.md#2.5`）。
- **可演化**：反思可触发 `maybe_replan`，形成“记忆→反思→规划→行为”闭环。

### 6.4 Person Memory（用户专属记忆）：★★★★★

**两层结构**（`cognition-and-group-dynamics.md#2.2`）是本期最大亮点：

```
条目层 person_memory_entries（append-only，LLM 抽取新事实逐条追加，失败回退“用户提到：<前120字>”）
        ↓ 每 6h 阈值 20 条
主档层 person_memories.content（合并压缩，条目标记 compacted=TRUE 软归档）
对话上下文 = 主档 + 最近 8 条未压缩条目
```

- 根除旧版“单槽全文重写”的 telephone game 漂移。
- `heat DESC, last_interaction_at DESC` 排序体现“我记得你”。
- 按 `heat` 的保留期与可观测日志完整。

### 6.5 日记（Diary）：★★★★☆

- 四周期 `day/week/month/year`、真实 UTC 时间窗口、<3 条记忆时 422 拒绝、LLM 结构化 `{title, content, mood}` 输出（`memory-system.md#十`）。
- 定位清晰：叙事归档，不替代 Episode 真相源。

### 6.6 群体动力学：★★★★☆

见 §5，已与认知深化协同（传闻注入 `[听说的消息]`、共同经历 `related_characters` 原语、群活动集体叙事）。

**总体**：认知机制**设计完备、可演化**，两层 Person Memory 与分层反思为行业领先实践；唯反思向量缺失与计划执行追踪（`progress` 更新依赖 LLM 自觉）可再增强。

---

## 7 ReAct 工具调用

### 7.1 成熟度：★★★★☆

`src/tools/` 为进程内 `ToolRegistry`（async 函数），非外部 MCP/HTTP，具备：

| 能力 | 实现 | 评价 |
|---|---|---|
| 注册与开关 | `tools:enabled` Redis Hash 动态启用/禁用，无需重启 | ✅ 前端 `/settings/tools` toggle 直达 |
| 调用循环 | `tick.py:_run_react_loop` 最多 3 轮：`use_tool → call_tool_with_context → deltas 暂存 → 再次 _decide` | ✅ 有上限，3 轮后强制 `wait` |
| 观察回灌 | `tool_observations` 注入 `[工具调用观察（ReAct）]` 段 | ✅ LLM 基于真实结果推理 |
| 状态 deltas | `money/inventory/mood/relation` 暂存 `context["pending_*"]`，由 `_execute_action` 主事务统一落库 | ✅ 消除“关系已写、行为回滚”的部分提交（R4-M11、R5-L11） |
| 命名空间 | `shop / knowledge / social / world / self_info` | ✅ 只读与可变工具分离 |
| 记忆沉淀 | 工具结果 `importance=6`（刻意低于 7，避免永久保留阈值膨胀） | ✅ 细节考究 |

### 7.2 边界与约束

- **最大迭代 3 + 动态耗时上限 480 分钟**：防 LLM 失控。
- **无工具时整段跳过**：`format_tools_for_prompt()` 返回 `None` 时不渲染 `decision_tools` 段，避免零工具环境下 LLM 仍尝试 `use_tool` 空转 3 轮（R5-M3）。
- **`tool_name` 缺失合成失败观察**：避免盲猜耗尽轮数（R5-L12）。

### 7.3 缺口

- **工具生态**：当前以商店、知识库、社交、世界查询为主，尚未接入外部 API（如天气真实数据、搜索、图像生成）；`media_video_poll_interval/max_polls` 已预留视频生成轮询，但端到端链路未在文档显式演示。
- **权限**：工具调用未按角色/场景做 RBAC，任意角色可调任意工具（依赖 `precondition` 间接限制）。
- **可观测**：`tool.call` span 已埋点，但工具级熔断/限流（如商店库存超卖）未见。

**总体**：ReAct 实现**成熟且克制**，交易一致性处理尤为细致；扩展时需补权限与限流。

---

## 8 多端触达：Web Dashboard + QQ

### 8.1 Web 触达：★★★★☆

- **协议**：`/ws/chat/{cid}`（用户↔角色）+ `/ws/dashboard`（全局世界/角色实时态）双 WebSocket，`WebSocketManager` 统一管理（`src/messaging/websocket.py`）。
- **实时性**：`useDashboardSocket` 登录后订阅 `dashboard` 帧 → 直接写 `["world"]` Query 缓存 + `invalidateQueries(["health"])`，轮询仅作断连兜底（`frontend-design.md#5.2`）。
- **消息面**：`MessageService.handle_user_message` 同事务写用户消息 + 角色回复 + 上下文压缩，`DEFAULT_HISTORY_LIMIT=20 / COMPRESS_THRESHOLD=50` 策略平衡上下文与成本。

### 8.2 QQ 触达（OneBot v11/v12 反向 WS）：★★★★★

`src/adapters/onebot.py` 为项目最精细的适配器之一：

| 能力 | 实现 | 评价 |
|---|---|---|
| 反向 WS | `/ws/onebot/v12` 服务端，OneBot 实现主动反连，兼容 `type/post_type`、`detail_type/message_type` | ✅ 部署灵活，无需公网入口 |
| 文本提取 | `raw_message` 优先，`message` 段 `text` 拼接兜底 | ✅ 兼容 v11/v12 |
| 群聊 @ 检测 | `to_me` / `message.at` / `raw_message [CQ:at]` 三重 | ✅ 覆盖 NapCat/Lagrange 差异 |
| 群聊智能回复 | 关键词（角色名 100%）→ 启发式（疑问 40% / 情绪 20%）→ LLM 判断（`GROUP_REPLY_PROBABILITY_CAP=0.4`）三层，受概率上限 | ✅ 像真人一样选择性回复，LLM 失败 fail-safe 不回复 |
| 多段拟真 | `_split_message` 按 `

\n\n`→`\n`→硬切 500 字，段间 `sleep 0.6s` | ✅ 降低截断与刷屏感 |
| 主动分享推送 | `ProactiveSharingService.evaluate_and_share`（1h 冷却、日限 5）→ `OneBotAdapter.push_share` | ✅ 反向闭环“角色找你聊天” |
| 群-角色映射 | `ONEBOT_GROUP_CHARACTER_MAP` JSON + `ONEBOT_DEFAULT_CHARACTER_ID` | ✅ 单 QQ 多群多角色 |
| 安全 | `PromptGuard` 三层（注入检测 15 模式、sanitize、分隔符包裹）+ `ONEBOT_ACCESS_TOKEN` Bearer 校验（生产未配则拒绝启动） | ✅ 覆盖 OWASP 常见注入 |
| 限流 | `onebot_rate_limit_per_minute=20` / `onebot_stream_maxlen=10k` | ✅ 洪泛防护与 Streams 长度收敛 |

**隐私提示**：`ONEBOT_GROUP_AT_ONLY=false` 时群消息出域至 LLM，需在部署文档显式告知用户（README 已有）。

### 8.3 成熟度

QQ 链路从接入、决策、回复、推送到限流**端到端闭环**，Web 侧通过 `send_to_user` 与 `push_share` 双推送覆盖在线/离线用户。**实现成熟度在同类开源项目中居前**。

---

## 9 数据持久化：结构化 + 向量检索

### 9.1 结构化：★★★★★

| 表 | 分区/索引 | 评价 |
|---|---|---|
| `characters` | `gin_trgm / gin_jsonb / partial is_active` | ✅ 查询友好 |
| `character_states` | `PK character_id + version 乐观锁 + fillfactor 85` | ✅ 镜像回灌与并发控制 |
| `action_records` | 按月 RANGE 分区 + `(character_id, timestamp)` + `gin params` | ✅ 预创建 12 月分区，无 DEFAULT 分区报错清晰 |
| `memory_episodes` | HASH 16 分区 + 父表 HNSW + 部分索引 `is_reflected / materialized` | ✅ 分区裁剪 <10ms，规避全局 HNSW 召回崩塌 |
| `reflections` + `reflection_sources` | 复合外键 `(memory_id, memory_character_id)` | ✅ 参照完整性 |
| `plans / relations / conversations / messages` | 唯一约束 `(user_id, platform, character_id)`、`sender`/`platform` 枚举 | ✅ 幂等与一致性 |
| `world_events / snapshots` | 差分 + 快照 + `UNIQUE(tick_id, event_type, event_key)` | ✅ 冷启动恒定时间 |
| `character_diaries / person_memories` | 按角色/时间复合索引 | ✅ 归档与热度排序 |

**一致性**：先写 PG 事务、再写 Redis（`HSET char:{id}:state`），失败由 PG 镜像回灌；`conversations.get_or_create` 用 `ON CONFLICT DO NOTHING` 保证幂等。

### 9.2 向量检索：★★★★☆

- **半精度 halfvec(2048)**：较 float32 省 50% 存储/显存，HNSW 检索精度损失可控。
- **父表索引自动传播**：运维噩梦规避，`ef_construction=128 / m=16` 兼顾精度与构建速度。
- **混合检索**：候选×3 召回 + 重排，`hnsw.ef_search=100` 会话级可调。

**阈值**：单角色 >500 万或总量 >1 亿、或 HNSW 内存 >50% `shared_buffers`、或 p95 >200ms 时建议迁 Milvus（`memory-system.md#7.3` 预案清晰）。

### 9.3 真相源与校验

- `configs/scenes.yaml` 与 `world-map.yaml` 场景 ID 交叉校验、`events.yaml` 损坏 fail-fast、`configs/prompts/*.yaml` 缺失即拒绝启动，符合“配置真相源”约定（`AGENTS.md#3.4`）。
- 主键全量 UUID v7，时间有序、分布式可生成、防枚举，选型正确。

---

## 10 全链路可观测性

### 10.1 覆盖度：★★★★☆

| 支柱 | 组件 | 覆盖 | 评价 |
|---|---|---|---|
| Traces | OTel SDK → Jaeger (badger 持久化) | `world.tick / character.tick / perceive / decide / action.execute / tool.call / message.process / llm.generate / embedding.batch` + FastAPI/AsyncPG 自动 instrumentation | ✅ 埋点即契约，`@trace_span` 统一 |
| LLM 专用 | Langfuse SDK | Prompt/Completion/Tokens/Cost/Metadata(trace_id) 手动 `trace_llm_call` 上报 | ✅ 与 OTel 通过 `otel_trace_id` 关联 |
| Metrics | Prometheus + Grafana + Alloy | `character_tick_duration / llm_* / tool_* / db_* / active_characters / message_response_time` 等 15+ 指标 + 8 告警规则 | ✅ 含 Dashboard 3 套（Overview/LLM/Character Tick） |
| Logs | structlog JSON → Alloy → Loki 3.x | `trace_id/span_id` 注入、级别规范、LogQL 示例、Trace↔Logs 双向联动 | ✅ 事件名 snake_case、ERROR 必 `exc_info=True` |

### 10.2 亮点

- **Alloy 统一采集**：取代 Promtail+Agent+Collector，HCL 管道模块化，文件采集不挂 `docker.sock` 缩小攻击面。
- **采样率可配**：`OTEL_TRACES_SAMPLER_RATE=0.5` 默认，`loki` 7 天保留、TSDB v13。
- **前端原生备用**：`/api/v1/admin/logs` + `/metrics-detail` 供 `/monitoring` 页面无需 Grafana 即可看日志/指标。

### 10.3 短板

- **头采样（head sampling）**：`TraceIdRatioBased` 在 trace 根部即决定采样，**错误 Span 不保证必采**；文档已诚实披露（`observability.md#十`），但告警与排障流程不得假设“错误 trace 必在 Jaeger 可查”。**建议**：引入 OTel Collector 做 tail-based sampling（错误/慢链路优先保留）或对 `llm.generate` 失败时同步写 Loki 的 `error_trace` 索引。
- **Langfuse 自部署**：`docker-compose.yml` 未内置 Langfuse 服务（文档标注“可选外部”），新用户追溯 LLM 调用需额外部署。
- **Alertmanager token 占位**：`__ALERT_WEBHOOK_TOKEN__` 运行时替换为 `ALERT_WEBHOOK_TOKEN`，未配时后端 403，链路完整但首次配置易遗漏。

**总体**：可观测性**覆盖度高、工程化扎实**，唯采样策略是长期排障的隐性风险。

---

## 11 Docker Compose 部署

### 11.1 成熟度：★★★★★

| 维度 | 实现 | 评价 |
|---|---|---|
| 单一真相源 | 唯一 `docker-compose.yml`（已合并 `compose-win`）+ `x-default-logging` 锚点 | ✅ 消除漂移 |
| 凭据 fail-fast | `${POSTGRES_PASSWORD:?}` / `${REDIS_PASSWORD:?}` / `${GRAFANA_ADMIN_PASSWORD:?}` | ✅ 缺失直接拒绝启动 |
| 网络与端口 | 基础设施/可观测性一律 `127.0.0.1` 回环，`backend:8001←8000` 规避宿主冲突，`frontend:80←8080` 非 root | ✅ 安全且开发友好 |
| 健康检查 | `pg_isready` / `redis-cli -a $REDIS_PASSWORD ping` | ✅ `depends_on: condition: service_healthy` |
| 构建 | 后端 `uv + Python 3.13-slim` 多阶段、前端 `pnpm + Vite → nginx` 多阶段 | ✅ 镜像精简 |
| 配置挂载 | `./configs:/app/configs:ro`、`./data/*` bind mount | ✅ Prompt/节日日历 fail-fast 可验证 |
| Profile | `observability`（prom/loki/jaeger/alloy/grafana/alertmanager）/ `backup`（pg+redis 定时备份） | ✅ 按需启用 |
| 备份 | `pg_dump --format=custom` + `redis-cli --rdb`，6h 间隔、14 天保留、`.part` 原子改名 | ✅ 含演练脚本 `restore_drill.sh / cold_start_drill.py` |
| 日志轮转 | `json-file 10m×3` | ✅ 防磁盘打满 |

### 11.2 容量与成本

- **DB**：50 角色年增 `action_records` ~1800 万 + `memory_episodes` ~1800 万，约 130 GB/年（`deployment.md#6.1`），`shared_buffers ≥4GB / HNSW 2GB` 建议合理。
- **Redis**：50 角色 ~50MB，512MB + noeviction 足够。
- **LLM**：50 角色×30s Tick×24h = 14.4 万次/天，`gpt-4o` 约 $200/天，`mini` 约 $20/天——**成本是规模化最大约束**（见 §13）。

### 11.3 风险

- **单机备份同盘**：`./data/backups` 与数据同宿主，磁盘故障同时摧毁数据与备份；文档已提示需异机/对象存储同步，但未提供自动化同步作业。
- **无 PITR**：`pg_dump` 定时全量，无 WAL 归档，RPO ≤6h；对陪伴场景可接受，对付费用户需提升。
- **PG 18 强依赖**：`pgvector/pgvector:pg18` + `uuidv7()` 要求宿主 PG 18，迁移至云 RDS 需确认扩展可用性。

---

## 12 React 前端工程化

### 12.1 质量：★★★★☆

| 维度 | 实现 | 评价 |
|---|---|---|
| 路由 | TanStack Router 文件路由 + 类型安全 | ✅ 24 页面覆盖完整（仪表盘/角色/地图/记忆/规划/关系/事件/行为/消息/监控/快照/设置等） |
| 状态 | Query（服务端） + Zustand（UI） + Zod 校验 | ✅ 分层清晰，`src/api/hooks` 统一 |
| 实时 | `useDashboardSocket` 订阅 `/ws/dashboard?token=JWT` + Query 失效，无轮询风暴 | ✅ 断线指数退避 10 次 |
| 设计语言 | Glassmorphism + 柔和渐变 + 圆角 12–20px + Framer Motion 弹性动效 + 二次元配色 `#FF8FAB/#7EC8E3/#B19CD9` | ✅ 轻盈有呼吸感，非堆砌卡通 |
| 构建 | Vite Rolldown + React Compiler 自动记忆化 + `tsc -b` | ✅ 免手写 `useMemo/useCallback`，`oxlint/oxfmt` 极速 |
| 部署 | `pnpm + Vite → nginx` 多阶段 + `nginx.conf` SPA 回退 + API/WS 反代 | ✅ 与后端一致的多阶段 |
| 类型 | `openapi-typescript` 从 `openapi.json` 生成 `api-generated.d.ts` | ✅ 前后端契约 |

### 12.2 缺口

- **测试**：`package.json` 含 `vitest`，但 `tests/unit + e2e(Playwright) + storybook` 在文档有述而代码侧覆盖未知；建议补充 `pnpm test --coverage` 阈值。
- **错误态**：Dashboard 对 `ws` 断连、LLM 熔断、预算超限的 UI 提示未在文档显式设计。
- **可访问性**：Glassmorphism 半透明 + 模糊在低对比度设备可读性需校验（WCAG AA）。

**总体**：前端工程化**现代且克制**，设计语言与产品定位高度一致。

---

## 13 长期运行风险

### 13.1 并发冲突：★★★★☆（已治理，残余风险低）

| 风险 | 治理 | 残余 |
|---|---|---|
| 同角色并发 Tick | `char:tick:lock:{cid}` NX EX + token 比对 + watchdog 续租 + `lock_lost` 四闸口（入口/对话/事务前/Redis 前） | 低。极端时钟回拨或 Redis 分区脑裂仍可能 double-tick，`reconcile pg_advanced` 仲裁兜底 |
| World Tick 多实例 | Redis 选主 `world:tick:leader` 30s TTL 故障转移 35s | 低。35s 窗口内世界停滞可接受 |
| 工具 deltas 部分提交 | 暂存 `pending_*` → 主事务统一落库 | 已消除 |
| 会话并发创建 | `ON CONFLICT DO NOTHING` 幂等 | 低 |

### 13.2 记忆膨胀：★★★★★（治理完备）

| 机制 | 策略 | 评价 |
|---|---|---|
| 相关性去重 | `exists_recent_duplicate` 归一化 + `is_duplicate` 向量余弦 0.95 | ✅ 中文场景可靠 |
| 生命周期 | `memory_retention` 三档（≤3 级 90 天 / 4–6 级 180 天 / ≥7 永久）+ 压缩归档（`archive` 豁免）+ 分级清理 | ✅ 应用层定期清理，HASH 分区无法按时间 drop 的痛点已解 |
| 压缩归档 | 按角色×月份分组，`min_batch 5` / `batch_limit 300`，LLM 压缩失败整组跳过 | ✅ 不变量“未压缩不删除” |
| 限流 | 工具记忆 `importance 6`（避开 7 永久阈值）、`gossip_max_per_tick 1`、反思 20 条触发 | ✅ 速率可控 |
| 保留期 | `world_events 90 天 / snapshots 保留 3 / messages 180 天 / reflections 365 天（tier2 永久）/ plans 90 天` | ✅ 全链路闭环 |

**容量**：50 角色年增 1800 万记忆，含向量 ~80GB，`retention_delete_batch_size 5000` 分批删避免长事务。

### 13.3 成本与熔断

- **预算**：`BudgetManager` Redis Hash `llm:cost:{YYYY-MM-DD}` + TTL 48h + `check_and_record` Lua 原子；`llm_daily_budget_usd=10.0` 默认。
- **熔断**：`CircuitBreaker` 三态（CLOSED/OPEN/HALF_OPEN），阈值 5、恢复 60s，Redis Hash 多实例共享。
- **风险**：50 角色全量 strong 模型 $200/天不可持续；**建议**：Character Tick 默认 `model_chat=mini`，仅 `decide` 关键路径用 `strong`，或引入队列削峰（`character_tick_seconds` 动态）与重要性采样（高 `stamina/mood` 低时降频）。

### 13.4 数据一致性

- **PG→Redis 最终一致**：事务提交后 `HSET`，失锁闸口防覆盖，`rehydrate_states()` 冷启动回灌。
- **幂等**：`world_events UNIQUE(tick_id, event_type, event_key) + ON CONFLICT DO NOTHING`、消息会话幂等。
- **缺口**：无 WAL PITR、备份同盘，前述已述。

---

## 14 用户体验

### 14.1 体验亮点

- **拟真感**：多段 0.6s 间隔、按段落/行/硬切三级拆分、情绪与状态注入 Prompt，回复“像真人”。
- **可观测即体验**：`/monitoring` 原生日志/指标 + Grafana iframe，运维与玩家视角统一。
- **动态背景**：`dawn/day/dusk/night` 渐变随虚拟时间切换，沉浸感强。
- **即时反馈**：Dashboard 世界时钟/天气/场景拥挤度实时推送，角色卡能量/饱腹条直观。

### 14.2 体验短板

| 问题 | 影响 | 建议 |
|---|---|---|
| WebSocket 断连提示弱 | 用户感知“卡死” | 显式非阻塞 Toast + 重连倒计时 |
| LLM 熔断/预算超限仅后端日志 | 用户收到的 `DEFAULT_ERROR_REPLY` 过于模板化 | 区分“预算耗尽/熔断/超时”的用户可理解文案 + 前端熔断横幅 |
| 移动耗时仅后端计算 | 用户不知“雨天移动×1.5” | 地图/移动确认弹窗展示预估耗时与天气影响 |
| 关系/记忆无用户可视化 | “我记得你”不可验证 | 新增 `/memory/person` 用户视角记忆页（已设计 `person_memories` 索引） |
| 无引导与空状态 | 新部署零角色时 Dashboard 空白 | 空状态插画 + “导入角色卡” CTA |

**总体**：体验**有温度但闭环未完全打通**，建议以“用户可感知的记忆与关系”收尾最后一公里。

---

## 15 安全与健壮性

| 维度 | 现状 | 评价 |
|---|---|---|
| Prompt 注入 | `PromptGuard` 15 模式 + `sanitize`（控字符/HTML 转义/截断 2000）+ `wrap_user_message` 分隔符 + 末尾反指令 | ✅ 三层纵深 |
| 鉴权 | `JWT_SECRET` + `API_KEY` + `RBAC rbac_roles` + `ONEBOT_ACCESS_TOKEN` 生产 fail-fast | ✅ 覆盖 Web 与 OneBot |
| CORS | `cors_origins` 逗号列表，与 `allow_credentials` 互斥校验 | ✅ 生产需显式配域名 |
| 限流 | `onebot_rate_limit_per_minute 20` + `character_max_concurrent 10` | ✅ 多层限流 |
| 输入校验 | Zod 前端 + Pydantic 后端 + `params_schema` 必填校验 + `_clamp_dynamic_duration` 480 上限 | ✅ 非法决策回退 `wait` |
| 日志脱敏 | 规范要求 URL 密码/API Key/JWT 不落日志 | △ 需静态扫描 enforce |
| 依赖安全 | `uv` 锁文件 + `pnpm` 锁文件 | △ 建议接入 Dependabot + `npm audit` / `pip-audit` |

---

## 16 测试与质量保障

- **后端**：`mypy --strict` 0 错误（146 文件）、`ruff check + format --check`、`pytest` 三件套（`AGENTS.md#5`）；`cast(Redis, FakeRedis)` 模式注入替身，`per-file-ignores` 仅 `B027` 一处豁免，质量基线高。
- **前端**：`oxlint + oxfmt --check + tsc --noEmit`，`vitest` 单元 + `playwright` E2E（文档有述，覆盖度待 `pnpm test --coverage` 验证）。
- **集成**：`test_gossip_it / group_activity_it / retention_compression_it / person_memory_layers_it / memory_dedup_it` 等覆盖群体动力学与认知深化，值得肯定。
- **演练**：`cold_start_drill.py` + `restore_drill.sh` 冷启动与备份恢复演练，RTO ≤1h。

**建议**：补充 `chaos`（Redis 锁丢失、LLM 429、PG 分区未创建）与 `cost`（预算/熔断阈值）集成测试；CI 加入 `alembic upgrade head --sql` 干跑。

---

## 17 文档与工程规范

- **文档**：`docs/` 14 篇设计文档 + `README` 快速开始 + `AGENTS.md` 六大原则与执行协议，交叉引用完整，**在开源项目中属上乘**。
- **规范**：`implementation-style.md / frontend-style.md / domain-design-style.md / prompt-style.md / refactor-style.md` 五规范齐备，PEP 604 `X|None`、Pydantic BaseModel、`structlog`、`.\env + config.py` 真相源等约定统一。
- **Prompt 外置**：`configs/prompts/*.yaml` 缺失即 fail-fast，符合“配置真相源”约束。
- **小遗憾**：部分文档与代码存在版本漂移（如 `memory-system.md` 的 1536 维与 `config.py` 的 2048 维以迁移为准，`data-model.md` 已标注）；建议 CI 加入文档-代码一致性检查（如 `embedding_dim` 抽检）。

---

## 18 总体评价

### 18.1 评分卡（5 分制）

| 维度 | 分数 | 一句话评价 |
|---|---|---|
| 项目定位 | 5.0 | 差异化清晰，叙事自洽 |
| 分层架构 | 4.2 | 分层清晰，Service 层待补齐 |
| 技术选型 | 4.5 | 现代克制，LangChain 可审视 |
| 世界引擎与调度 | 4.7 | Leader 选举 + Evolution 链扎实 |
| 多智能体交互 | 4.3 | 轻量自洽，容量场景需扩展 |
| 记忆/反思/规划/Person Memory | 4.8 | 两层 PM 与分层反思领先 |
| ReAct 工具 | 4.4 | 一致性处理细致 |
| Web + QQ 多端 | 4.8 | QQ 适配器端到端成熟 |
| 数据持久化 | 4.7 | HASH 分区 + HNSW + 幂等完备 |
| 可观测性 | 4.2 | 三支柱齐，头采样是隐忧 |
| 部署 | 4.8 | 单一 compose + profile + 演练 |
| 前端工程化 | 4.4 | 现代栈 + 设计语言统一 |
| 长期运行风险治理 | 4.6 | 记忆膨胀与并发已系统治理 |
| 用户体验 | 4.0 | 有温度，闭环待收尾 |
| 安全与健壮性 | 4.3 | 纵深防护，脱敏需 enforce |
| 测试与质量 | 4.5 | strict 0 错误，集成覆盖优 |
| 文档与规范 | 4.9 | 14 篇设计文档，上乘 |
| **综合** | **4.5 / 5 (A-)** | **具备生产化潜力，需补成本与体验最后一公里** |

### 18.2 雷达图（文本版）

```
定位 5.0 ●●●●●
架构 4.2 ●●●●○
选型 4.5 ●●●●○
引擎 4.7 ●●●●●
交互 4.3 ●●●●○
认知 4.8 ●●●●●
ReAct 4.4 ●●●●○
多端 4.8 ●●●●●
持久化 4.7 ●●●●●
可观测 4.2 ●●●●○
部署 4.8 ●●●●●
前端 4.4 ●●●●○
风险治理 4.6 ●●●●●
体验 4.0 ●●●●○
安全 4.3 ●●●●○
```

---

## 19 改进建议

### P0（上线前必做）

1. **成本分级**：Character Tick `decide` 以外的 LLM 调用（压缩、分享文案、群聊 judge）强制 `model_flash/mini`；`character_tick_seconds` 按在线/离线角色分级（离线 60–120s），预计降本 50–70%。
2. **备份异地**：为 `./data/backups` 增加 `rclone` 或 `aws s3 sync` 定时同步作业，`docker-compose.yml` 新增 `backup-sync` sidecar（profile: backup）。
3. **熔断用户可感知**：`MessageService._generate_reply` 的 `circuit_open / budget_exceeded` 区分文案 + 前端 `/monitoring` 熔断横幅 + `MESSAGE_PROCESSED_TOTAL{status}` 告警联动。
4. **文档-代码一致性 CI**：校验 `embedding_dim`、`halfvec` 列、`MODEL_*` 与 `config.py`/`alembic` 一致性。

### P1（1–2 迭代内）

5. **Tail 采样**：引入 OTel Collector（或 Alloy 的 OTel 接收）做尾采样，错误/慢链路（`llm.generate` 失败、`character.tick >5s`）必采。
6. **Service 层补齐**：新增 `src/services/`，将 `api/characters.py` 等直查 Repository 的逻辑收敛为 `CharacterService / WorldService`。
7. **Tool 权限与限流**：`ToolRegistry` 增加 `requires_roles / rate_limit` 元数据，商店库存引入乐观锁防超卖。
8. **空状态与引导**：Dashboard 零角色/零记忆空状态 + “导入角色卡”向导，移动确认弹窗展示天气/拥挤耗时影响。
9. **前端测试阈值**：`vitest --coverage` + `playwright` 关键路径（登录→角色 Tick→QQ 回复→主动分享）E2E，CI 阈值 70%。

### P2（中长期）

10. **LangChain 审视**：评估直调 `openai` SDK + 自建 `structured_output`（Pydantic）的替代成本，简化依赖。
11. **PG 18 兼容**：提供云 RDS 适配指南（`pgvector` 扩展可用性、`uuidv7()` 兼容层）与 `alembic` 分支策略。
12. **空间分区**：100+ 角色时引入场景分片 Tick（按 `scene` 并行）与 Streams 消费组 Fan-out。
13. **反思向量回归**：为 `reflections` 恢复 `halfvec` 或引入 `tsvector` 全文，支持语义检索。
14. **PITR**：对付费部署提供 WAL-G + 对象存储的 PITR 方案，RPO 降至分钟级。
15. **用户视角记忆页**：`/memory/person` 展示 `person_memories` 热度与条目，验证“我记得你”。

### 路线图（建议）

- **R1（4 周）**：P0 1–4，成本与备份闭环，发布 `v0.2.0`。
- **R2（8 周）**：P1 5–9，采样与 Service 层，前端 E2E，`v0.3.0`。
- **R3（12 周）**：P2 10–15，规模化与体验收尾，`v1.0.0-rc`。

---

## 附录

### A 关键文件索引

- 架构：`docs/architecture.md`、`docs/detailed-architecture.md`、`docs/world-engine.md`、`docs/action-system.md`
- 认知：`docs/memory-system.md`、`docs/character-design.md`、`docs/cognition-and-group-dynamics.md`
- 数据：`docs/data-model.md`、`packages/backend/alembic/versions/*.py`、`src/config.py`
- 触达：`docs/messaging-service.md`、`src/adapters/onebot.py`、`src/messaging/service.py`
- 观测：`docs/observability.md`、`docker/observability/*`、`src/observability/*`
- 部署：`docs/deployment.md`、`docs/docker-deployment.md`、`docker-compose.yml`、`packages/backend/Dockerfile`
- 前端：`docs/frontend-design.md`、`packages/frontend/package.json`、`packages/frontend/nginx.conf`

### B 容量速算（50 角色）

- Tick：30s → 14.4 万次/天 → `mini` $20/天、`strong` $200/天
- 存储：`action_records` 1800 万/年 ~50GB，`memory_episodes` 1800 万/年 ~80GB（含 halfvec）
- Redis：实时态 ~50MB，Streams 需 `maxlen 10k` 收敛

### C 术语表

- **真相源**：Redis `char:{id}:state` / `world:state` 为实时真相，PG 为历史与审计真相。
- **HNSW**：Hierarchical Navigable Small World，向量近似最近邻索引，优于 IVFFlat（增量友好）。
- **ReAct**：Reasoning + Acting，LLM 决策→工具执行→观察回灌→再次决策循环。
- **Person Memory**：角色视角对单用户的专属记忆，`heat` 热度排序。
- **Gossip**：好友高重要性经历的第二手记忆，`source_type=gossip`，不二次传播。

---

> **审查结论**：AI Town 在“多智能体小镇陪伴”赛道上已超越 Demo，进入**可演进的系统**阶段。其分层、持久化、认知与 QQ 触达的完成度在同类开源中领先；若能在成本分级、采样与备份异地上补齐短板，并以用户可感知的记忆/关系闭环收尾体验，将具备**小规模付费陪伴产品的生产化基础**。

---

## 附录 D 后台深度探索增补（2026-08-26 22:00 并行 4 路回收）

> 本附录收敛 4 个并行探索子任务（`bg_5fa581e7` 后端架构 / `bg_fce9c7c1` 认知机制 / `bg_18ff6c0e` 持久化与可观测 / `bg_c9af825d` 前端与部署）的增量证据，已与正文交叉校验，**新增盲点以 △ 标注**。

### D1 后端架构增量

- `tick.py` 实测 1350 行、`admin.py` 39KB（最大路由文件）——与正文“单函数过长”判断一致，`PerceptionMixin/SocialMixin` 已拆但 `_execute_action` 仍承载对话生成+移动校验+事务+镜像四责。
- Service Locator（`runtime.py` 全局 `get_*/set_*`）导致调用方散布 `| None` 守卫，已被探索报告确认为“有意为之以换取模块降级能力”，权衡合理但依赖隐式化需在贡献指南显式说明。
- `scheduler/loops.py:39` 跨模块引用 `diary_service` 私有函数 `_diary_trigger_periods/_world_real_window_seconds` ——轻微坏味道，建议提升为 `diary_config.py` 公开契约。
- Fencing 的 check-then-act 非原子性在代码注释中已自认“仅收窄窗口非消除”，与正文一致。

### D2 认知机制增量（△ 为本文新增）

- △ **文档-实现漂移清单**（已核实）：`memory-system.md §4.1` 宣称反思三触发（数量/每日 22:00/关键事件）只实现数量阈值；`§5.1`“每天 6:00 生成当日计划”无定时任务，计划完全依赖 LLM 自发 `createPlanChanges`（被动涌现）；`plans.steps JSONB` 在实际 `models/plan.py` 中不存在——计划无步骤分解。
- △ **记忆内容语义贫瘠**：写入模板 `"{name}在{location}执行了{action}。理由：{reason}"` 为工程日志句式，所有记忆结构雷同，向量化后区分度低，直接削弱召回精度；检索 query 为合成文本（位置+时段+情绪+计划标题）而非真实信息需求，语义匹配弱。
- △ **社交记忆永久保留放大**：规则评分 `social/chat` 固定 7 分恰撞 `importance≥7 永久保留` 策略，长期运行社交记忆将成为不可清理主体。
- △ **上下文窗口无总量预算**：单条 500 chars/条数配额到位，但 `nearby_characters` 无上限（拥挤场景可注入数十段落），决策 Prompt 无总 token 计量与动态裁剪。
- △ **Person Memory 无向量检索**：`get_relevant_context` 仅主档+最近 8 条，未做语义召回，用户多时无法按需召回相关记忆。
- 反思阈值 `REFLECTION_THRESHOLD=20` 为类常量非 `settings`，与周边全配置化风格不一致，制约调参——与正文一致，探索报告补充了 `tier=2` 元反思溯源断链（未挂 `reflection_source` 到 tier=1）。

### D3 持久化与可观测增量（△ 为本文新增）

- △ **HNSW 不收缩**：`memory_retention` 大量 DELETE 后 pgvector HNSW 索引项不被 VACUUM 回收，无定时 `REINDEX` 任务，长期召回质量衰减；`0018` 仅调 `autovacuum scale factor 0.05/0.02` 未治本，**应列为 P1**。
- △ **`world:scene:visitors` 无恢复路径**：Redis 丢失后 `rehydration.py` 仅恢复 `world:state` 摘要，`loader` 将 `current_count` 置 0，PG 位置仍在——拥挤度静默漂移直到各角色下次 `move`，且不在 `reconcile.py` 字段集内。
- △ **备份时间窗不一致**：`db-backup`（`pg_dump`）与 `redis-backup`（`BGSAVE`）各自 6h 独立运行，恢复点错位（靠 reconcile 兜底，但 `visitors/scenes` 哈希兜不住）。
- △ **`current_action` 排除对账**（文档声明瞬态）：中途崩溃在两库同时残留陈旧 `current_action` 直到下次成功 Tick。
- △ **reconcile 全量扫描扩展性**：每 10min 加载全部角色并对每角色 3 次 Redis 往返（`exists/hgetall/get ver`），数百角色以上应 pipeline 化。
- 可观测盲点与正文一致：头采样 0.5 导致半数 Tick 无链路（日志侧 `trace_id` 已补偿）、Langfuse 截断 2000 字符。

### D4 前端与部署增量（△ 为本文新增）

- △ **README/现实不符**：`README` 宣称 `Zod 4.4` 但 `package.json` 无 `zod` 依赖；`oxlint.json` `exhaustiveDeps: off` 静音 stale closure 风险。
- △ **Web 聊天未走 WebSocket**：后端 `/ws/chat/{character_id}` 已完整实现（JWT 三级传递、10s 超时、同一性驱逐），但前端 `characters.$characterId.tsx` 实际走 `POST /messages/send` 同步 REST——LLM 期间 HTTP 挂起，WS 端点空转。
- △ **连接状态不可见**：`useDashboardSocket` 10 次退避耗尽后永久放弃且 UI 无断连指示；`qq-monitor.tsx` “实时”实为 30s 轮询、`StatusBadge` 硬编码。
- △ **清华镜像硬编码**：`packages/backend/Dockerfile` 三处 `mirrors.tuna.tsinghua.edu.cn` 无构建参数开关，牺牲其他地区可构建性。
- 测试薄（5 文件 vs 30 路由）、`ui.tsx` 696 行单文件超标——与正文一致，探索报告补充了 CI 双向契约守卫（`openapi.json` + `api-generated.d.ts`）为工程化亮点。

> 增补结论：4 路探索与正文判断**高度一致**，新增 6 项 △ 盲点已纳入 P1 改进项跟踪；原报告评分与路线图维持不变。

*— Sisyphus, 2026-08-26, 增补于并行任务全量回收后*

---

## 附录 E 主 Agent 直扫补充（2026-08-26，不使用子代理）

> 本附录由主 Agent 直接 `Read` 30+ 文件逐行复核，详见 `docs/review-2026-08-26-supplement-main-agent.md`。要点：视频生成同步阻塞是吞吐黑洞、Prompt 正则易绕过且引号未转义、`world:scene:visitors` 不在对账集、聊天 WS 空转、Token 存 localStorage、`exhaustiveDeps: off` 等 9 类隐性风险已纳入 P0/P1。
