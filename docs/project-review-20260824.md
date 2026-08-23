# AI Town 全面审查报告（2026-08-24，基线 v0.2.0 / HEAD dabecc9）

> **本文地位**：对 stargaoyc/ai_town（LLM 驱动多智能体虚拟小镇 + QQ/Web 双端陪伴智能体）的首次全面审查。
> 覆盖项目定位与差异化、分层架构与模块边界、世界引擎与多智能体交互、认知机制完备性（记忆流/反思/规划/
> Person Memory/日记）、ReAct 工具调用、数据持久化、全链路可观测性、部署与工程化、前端与用户体验、
> 长期运行风险十大领域。
>
> **审查方法**：源码级调研——①核心决策链路逐行精读（`core/character/tick.py` 全量 1417 行 +
> `core/world/engine.py` 全文）、②认知子系统全部源码（memory/ 五个服务 + tools/registry +
> llm/fallback + cost_control）、③数据层（db/models 14 张表 + memory_repo 混合检索 SQL + alembic
> 迁移链 0001–0008）、④可观测性与部署（observability/ 全部 + docker-compose.yml + 两份 Dockerfile +
> CI workflow 全文）、⑤前端结构盘点（43 个 TS/TSX 文件、30 条路由）+ 配置/Prompt YAML 核对；
> 关键 P0 结论均经二次 grep 反查（调用点全库扫描证实"零消费方"）。
> 本地实测：`uv run mypy src/ tests/` **strict 模式 161 文件零错误**、`uv run ruff check src/ tests/`
> 全过、`pnpm run lint && pnpm run typecheck` 全过；pytest 未本地复跑（需 PG+Redis，CI 已含真实依赖
> 集成测试）。
>
> **严重度定义**：P0=核心功能静默失效/特性宣称失守，发布阻断；P1=功能性 bug/承诺违约/结构性缺陷；
> P2=纵深防御缺口/文档漂移/性能隐患；P3=瑕疵。

---

## 一、执行摘要

**一句话结论：这是一个"可靠性工程罕见地扎实、但认知闭环存在系统性断流"的项目——分布式锁/熔断/对账/
混合检索等基础设施达到商业产品水准，然而反思、Person Memory、日记三类认知产物全部"只写不读"，从未进入
任何 LLM 上下文；叠加线性时间衰减使 22 天前的记忆不可达，「让角色更懂你」「我记得你」的长期陪伴核心承诺，
当前只在数据库和前端页面里成立，不在模型的上下文窗口里成立。**

| 审查维度 | 评分(1-10) | 结论摘要 |
|---|:---:|---|
| 项目定位与差异化 | **9** | "世界驱动的陪伴"叙事在代码中真实落地（World Tick 独立于用户消息推进），非营销话术 |
| 分层架构与模块边界 | **7** | LLM 边界纪律严格（候选过滤/服务端注入）；core→messaging 反向依赖 + main.py 上帝模块 |
| 世界引擎与多智能体交互 | **6** | 引擎健壮（选主/演化器管线/差分事件）；角色间仅单轮寒暄级交互，撑不起"小镇社会" |
| 认知机制完备性 | **4.5** | 各机制单独实现尚可，但产出端与消费端系统性断裂（两个 P0 所在） |
| ReAct 工具调用 | **8** | 16 工具/5 命名空间，注入式参数安全，轮数上限克制 |
| 数据持久化设计 | **8** | HASH/RANGE 分区 + HNSW halfvec + 混合检索 SQL 精细；缺 retention 治理 |
| 全链路可观测性 | **9** | 20+ 真实埋点指标 + 11 条告警规则 + OTel/Langfuse/Loki，全项目最强项 |
| 部署与工程化 | **7.5** | CI 含真实 PG+Redis 集成测试；compose 凭据硬编码 + 版本三方漂移 |
| 前端工程化与 UX | **6.5** | 30 页运维台覆盖全面；零自动化测试 |
| 长期运行风险治理 | **3.5** | 并发防护到位，但记忆膨胀无任何对策（量化见 §十二） |

### P0 问题总览

| # | 领域 | 问题 | 关键证据 |
|---|------|------|---------|
| 1 | 认知机制 | **认知回流三断流**：Reflections / Person Memory / Diary 只写不读，均不注入任何决策或对话 Prompt，README「反思系统让陪伴更懂你」「Person Memory 让角色体现'我记得你'」两条特性宣称当前不成立 | `tick.py:1235`（仅写）；`person_memory_service.py:180`（get_relevant_context 全库零调用）；`chat.yaml:29-64`（无对应占位符） |
| 2 | 认知机制 | **线性时间衰减使 22 天前的记忆不可达**：正分上限 1.1 vs 每天扣 0.05，重排中老记忆永远输给近期——「长期记忆」核心能力静默失效 | `memory_repo.py:296-298` |

---

## 二、项目定位与差异化优势

### 真实成立的差异化

1. **世界驱动而非消息驱动**：World Tick（`core/world/engine.py`）独立推进虚拟时间/天气/场景拥挤度，
   Character Tick 每 30 秒驱动角色自主决策（`config.py:78`），用户的每次对话确实锚定在角色经历上——
   「你不在的时候他们也在生活」不是话术，是调度架构。
2. **LLM 边界纪律**（同类项目中最值得抄的部分）：Action precondition 由代码过滤、LLM 只能在候选中选择
   （`actions/base.py:34-66`）；executor 返回状态字典由执行层单事务写入（`tick.py:874-961`）；工具的
   money/inventory/关系强度参数由服务端注入（`tools/registry.py:296-373`），LLM 无法伪造资源。
3. **双端分工清晰**：Web Dashboard 是上帝视角运维台（30 页），QQ 是角色视角陪伴端；主动分享
   （`proactiveShareIntent` → `OneBotAdapter.push_share`，`tick.py:1316-1389`）打通"角色主动找你"。
4. **成本工程完整**：日预算 Lua 原子检查+记录（`budget_manager.py:39-56`）、多源 failover 带 5 分钟冷却
   （`fallback.py`）、批次级 429 指数退避（`main.py:514-539`）、熔断跳过（`tick.py:121-123`）。

### 相对 Generative Agents（斯坦福小镇）的核心差距

- 论文的检索三因子（recency×exponential / importance / relevance）中本项目 recency 用线性减法且系数
  过重（P0-2），实际效果是"短期记忆系统"；
- 论文的 reflection 产物会通过检索回流影响决策，本项目的 reflection 是认知死胡同（P0-1）；
- 论文的 agent-agent 对话是多轮 tree 结构，本项目是一次生成双方各一句（§四）。

---

## 三、分层架构与模块边界

### 实测结论：主干清晰，纪律真实

- `runtime.py` 服务定位器消除业务模块对 main.py 的反向依赖，TYPE_CHECKING 隔离类型导入；
- 12 个 Prompt 全部外置 `configs/prompts/*.yaml` 缺失即 fail-fast，compose 只读挂载（`docker-compose.yml:89`）；
- mypy strict 161 文件零错误，豁免逐条登记原因（`pyproject.toml [[tool.mypy.overrides]]`）。

### 问题清单

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **Core → Messaging 反向依赖**：按项目自订分层（AGENTS.md：API→Service→Core→Infra），Core 层不应知晓消息服务；主动分享应改为事件由 messaging 层订阅 | `core/character/tick.py:45` |
| P1 | **main.py 上帝模块（844 行）**：350 行 lifespan 装配 + 3 个业务后台循环（tick 循环/日记调度/状态对账）+ 内联 AuthMiddleware，循环属纯业务逻辑应下沉 scheduler/core | `main.py:96-448, 462-679, 703` |
| P2 | API 层直连 db models/repositories 共 5 处，与 AGENTS.md「禁止在 API 层写业务逻辑」张力大，admin.py 尤甚 | `api/{world,messages,characters,memory,admin}.py` |
| P2 | 循环依赖靠延迟 import 规避而非结构调整，残留注释自证 | `tick.py:1261`、`tools/registry.py:172-176` |
| P3 | 包根遗留探针脚本 `_cycle_probe.py`（413 字节，8/23 夜间调试残留）：使全仓口径 `ruff check` 报 42 错（CI 因只查 src/tests 而绿）——仓库卫生问题 | `packages/backend/_cycle_probe.py` |

---

## 四、世界引擎与多智能体交互

### 正确性亮点（实测确认）

- World Engine Redis 锁选主 + compare-and-expire 安全续租（`engine.py:150-194`），支持多副本单实例推进；
- 演化器管线按依赖排序（时间→天气→场景→资源→事件），单个失败不中断 Tick（`engine.py:261-269`）；
- 差分事件持久化带去重基线 + 每 1000 Tick 全量快照保证冷启动恢复时间恒定（`engine.py:369-525`）；
- Character Tick 并发防护成熟：唯一 token 分布式锁 + Lua CAS 释放 + 看门狗续租防 LLM 长调用过期易主
  （`tick.py:102-131`）；Semaphore 热更新（`tick.py:133-146`）；429 按异常类型判定退避至 10×（`main.py:448-459`）；
- `chat_with` 有同场景校验 + 跨角色资源锁防 A→B/B→A 竞争 + 双向关系更新（陌生人破冰 +2/其他 +5）+
  为双方各写第一人称记忆（`tick.py:963-1165`）；
- move 决策经 MovementSystem 校验拦截 LLM 幻觉场景，失败降级 wait（`tick.py:787-829`）。

### 问题清单

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **角色间对话是一次性的**：一次 LLM 调用生成"A 一句 + B 一句"即结束，无多轮往返/话题延续/发起方对回应的反应；"友谊"由固定 +5 数值累积而非对话质量驱动 | `tick.py:1061-1085` |
| P1 | 对话内容不进入对方的实时上下文：B 只能在下次 Tick 向量检索命中时才可能想起，交互即时性丢失 | `tick.py:1114-1152` |
| P1 | `_perceive` 性能：每 Tick 至少 5-6 个独立 DB session 串行往返；nearby_characters 循环内每角色开新 session 查关系——注释声称「批量读取，避免 N+1」（`tick.py:343`）而实现恰是 N+1 | `tick.py:258-385, 345-358` |
| P2 | 无群体动力学：无三人以上共同活动、无传闻传播、无关系三角；`related_characters` 字段已预留但无消费方 | `memory_episode.py:62` |
| P2 | world_events 去重基线存内存，重启后首轮可能重复写入少量事件 | `engine.py:73` |
| P2 | decision prompt 中 `scenes=""` 占位符未实现（代码注释自认「简化」），场景容量/开放时段信息缺席决策 | `tick.py:479` |

---

## 五、认知机制完备性（本报告最重要章节）

### 5.1 总体诊断：三条断流（P0-1）

认知产物的生产端全部实现且有质量，消费端系统性缺失：

| 认知产物 | 生产（写入） | 消费（回流决策/对话） | 判定 |
|----------|------------|---------------------|------|
| 记忆流检索 | ✅ 每 Tick 混合检索 top10 注入决策 | ✅ 仅 Tick 决策链路 | 通 |
| 反思 Reflection | ✅ 阈值 20 条触发，LLM 归纳落表 | ❌ `tick.py` 仅在 `_memorize` 末尾调用 `check_and_reflect`（`tick.py:1235`）；`_perceive` 与 decision prompt 均无 reflections；检索 SQL 只查 memory_episodes | **断流** |
| Person Memory | ✅ 每次用户交互后异步更新（`messaging/service.py:427-449`） | ❌ `get_relevant_context()`（`person_memory_service.py:180`）**全库零调用**（grep 证实）；`chat.yaml` 无对应占位符 | **断流** |
| 日记 Diary | ✅ 四周期幂等生成（`main.py:557-645`） | ❌ 无任何 prompt 组装读取日记 | **断流** |

**后果**：用户对话链路的实际上下文 = 角色档案 + 世界状态 + 会话摘要 + 会话历史（`chat.yaml:29-64`），
没有任何跨会话长期认知注入。「长期陪伴」的产品承诺与实现之间存在断层线。

### 5.2 记忆流：检索工程优秀，认知参数失当（P0-2）

- 混合检索公式 `sim*0.6 + importance*0.05 - 天数*0.05`（`memory_repo.py:287-302`）：向量召回 top_k×2
  后重排，HNSW ef_search=100，分区裁剪下 <10ms——工程实现优秀；
- **P0｜线性时间衰减过重**：正分上限 1.1（0.6+0.5），每天扣 0.05 → **22 天前的记忆 final_score 必为负**，
  在重排中永远输给近期记忆。Generative Agents 用指数衰减（乘法、可调），本项目用线性减法且系数过大——
  「三个月前的重要事件」在决策中不可达，与陪伴定位直接冲突；
- P1｜检索 query 过弱：固定模板 `"角色{X}当前在{Y}，最近在做什么"`（`tick.py:302`），不含计划/情绪/时段
  语义信号，向量区分度基本退化为"该角色最近的记忆"；top_k=10 固定；
- P1｜无去重：同一行为重复执行产生近似重复 episode（无 content hash / 相似度去重），加剧膨胀并稀释检索。

### 5.3 反思：触发机制对，消化机制错

阈值触发（20 条未反思）符合论文精神；但每批恰好取最近 20 条（`reflection_service.py:75`），无法跨期主题
归纳；产出为纯文本拼接（`reflection_service.py:112`），无结构化 tag；最致命的是 §5.1 所述不回流——
反思是认知死胡同。

### 5.4 规划系统：两层扁平 + 死功能

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **`planChanges` 是死功能**：LLM 决策 schema 输出计划变更，`_decide` 解析存入 DecisionResult 后被丢弃——`PlanRepository.update_plan` 仅被 api/characters.py 用户侧 CRUD 调用，全库无 Tick 侧落库路径 | `tick.py:536-547`、`plan_repo.py:50` |
| P2 | Plan 仅 long_term/short_term 两型，无日/小时层级；与独立的 ScheduleSystem（modules/schedule）互不相通；「计划影响 precondition」注释未找到实现 | `plan.py:5, 21-31` |

### 5.5 Person Memory：单槽位覆盖式更新的漂移风险

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **全文重写式更新**：每角色对每用户仅一行（unique index），每次交互让 LLM 基于「旧内容+本轮对话」全文重写——telephone game 效应，早期细节被逐步稀释遗忘且不可追溯 | `person_memory.py:64`、`person_memory_service.py:99-113` |
| P1 | `preferences` JSONB 结构化字段**从未被写入**；heat 只增不减，模型注释承诺的「后台衰减任务」不存在（全库 grep 证实） | `person_memory.py:27-29, 49` |
| P2 | 实现用裸 `text()` SQL 绕过 ORM 模型与 repository 规范；read-upsert 无并发保护（唯一索引兜底异常被吞） | `person_memory_service.py:51-60, 143-178` |

---

## 六、ReAct 工具调用系统

**设计克制且安全（8 分）**：16 个工具 / 5 命名空间（shop×5/knowledge×2/social×3/world×4/self_info×2），
进程内 async 直调替代 MCP 消除网络开销（`registry.py:1-14`）；状态变更类工具参数服务端注入，LLM 无法越权
伪造资源；轮数上限 3、超限强制降级 wait（`tick.py:174-204`）；工具结果自动沉淀为记忆 importance=7
（`tick.py:604-625`）；工具开关持久化 Redis hash 支持前端热切换，Redis 故障 fail-open（`registry.py:179-205`）。

| 级别 | 问题 | 证据 |
|---|---|---|
| P2 | `get_enabled_tools()` 每次 `hgetall` 无缓存：单次 Tick（最多 4 轮决策+工具调用）产生 5-8 次 Redis 往返 | `registry.py:179-205`、`tick.py:431-435` |
| P2 | ToolRegistry 在 `_decide`/`_execute_tool` 内反复实例化；实例字段 `_current_character_id` 是死代码 | `tick.py:432, 591`、`registry.py:222-224` |
| P3 | `self_info.search_memories` 为关键词匹配而非向量检索（描述已如实标注，能力落差而已） | `registry.py:162-168` |

---

## 七、数据持久化设计（8 分）

### Schema 全景（14 张表，UUIDv7 主键 + JSONB + 外键 CASCADE）

| 表 | 分区 | 关键索引 |
|----|------|---------|
| memory_episodes | **HASH(character_id) ×16**（0002） | HNSW(halfvec)；部分索引 unreflected / unmaterialized(含熔断过滤) |
| action_records | RANGE(timestamp)（0001） | 月度分区预创建 |
| character_state_history | RANGE(recorded_at)（0007） | 同上 |
| messages | RANGE（0003） | keyset 双游标 `(created_at,id)` 分页 |
| 其余 10 表（plans/reflections(+sources)/person_memories/diaries/relations/conversations/world_events/world_snapshots 等） | 无 | 常规复合索引 |

### 亮点

1. **halfvec(2048)** 半精度向量（`memory_episode.py:55-57`）：同等维度索引体积减半，PG18+pgvector 正确用法；
2. embedding 异步 Worker：`FOR UPDATE SKIP LOCKED` 批拉取 + 指数退避（60/180/600/1800s）+ 5 次熔断
   （`memory_repo.py:136-257`）——队列语义正确，多 worker 安全；
3. 月度分区预创建 APScheduler 化（每月 25 号 03:00，`partition_scheduler.py:31`），修复了「连续运行超
   3 个月月初写入失败」的真实事故模式；
4. 混合检索原生 asyncpg 绕开 ORM 类型转换冲突，`SET LOCAL hnsw.ef_search` 事务内生效（`memory_repo.py:277-310`）。

### 问题清单

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **无 retention/archival**：全库 grep 无任何记忆删除/归档任务；HASH 分区又无法像 RANGE 那样 drop 老数据——膨胀只能删库重建（量化见 §十二 R1） | 全库 grep `DELETE FROM memory_episodes\|retention\|cleanup_` 零命中 |
| P1 | **乐观锁字段存在但未用于并发控制**：CharacterState.version 前端可见，后端 update_state 路径无 version 校验；Tick 与 API 对话线程并发写同一角色为最后写入 wins；10 分钟对账以「Redis 为准」仲裁，API 刚写入的合法变更可能被回滚 | `api.ts:73` vs `character_repo.update_state`；`reconcile.py` |
| P2 | PgBouncer：README 技术栈表宣称「连接池 PgBouncer」，docker-compose.yml 无此服务——文档失真 | README:45 vs docker-compose.yml 全文 |
| P2 | backend 容器无 alembic 自动迁移步骤，首次 `docker compose up` 后需手动进容器迁移，与「一键部署」承诺有落差 | docker-compose.yml:65-91 |

---

## 八、可观测性（9 分，全项目最强项）

- **指标矩阵完整且真实埋点**：20+ 自定义指标覆盖 World/Character Tick、Action、LLM（token/cost 分模型）、
  消息、HTTP（纯 ASGI middleware 兼容 WebSocket，`metrics.py:155-189`）、对账漂移（RECONCILE_DRIFT/REPAIR）；
- **11 条 Prometheus 告警规则**（docker/observability/alerts.yml）覆盖 Tick 停摆、失败率、LLM 预算/熔断、
  状态漂移、Redis 断连、5xx——多数同类项目只有指标没有告警；
- OTel 全家桶（fastapi/asyncpg instrumentation）+ Jaeger；Langfuse 手动轻量埋点记录 LLM 输入输出/token/
  耗时，未配置时静默 no-op（`langfuse_tracing.py`）；structlog + Loki + Grafana 预置仪表盘；
- `/ws/dashboard` 实时推送（5s 帧）替代轮询。

| 级别 | 问题 | 证据 |
|---|---|---|
| P2 | Langfuse trace 与 OTel trace id 未关联，两套平行体系无法互跳 | `langfuse_tracing.py` 全文 |
| P2 | `trace_character_tick` 只记 action/duration，未串联同 Tick 内多次 LLM 调用的父子关系 | `langfuse_tracing.py:78+` |
| P3 | `DB_QUERY_DURATION` 指标定义后在业务路径未见 observe 点 | `metrics.py:109-113` |

---

## 九、部署、安全与工程化

### CI（高于平均）

真实 pgvector:pg18 + Redis service 容器跑集成测试（testcontainers 本地亦可用）、ruff check + format --check、
**mypy strict**（本次实测 161 文件零错误）、前端 lint+typecheck+build。Conventional Commits + CHANGELOG +
ADR 目录齐全。

### Docker Compose 问题清单

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **PG 凭据硬编码**且 `environment:` 优先级高于 env_file——`.env` 中的 DATABASE_URL 在 compose 下**失效** | `docker-compose.yml:24-33, 74-77` |
| P1 | Grafana `admin/admin123` 硬编码 | `docker-compose.yml:180-181` |
| P1 | **Redis 版本三处漂移**：README 称 8.0 / compose 用浮动 `redis:alpine` / CI 用 redis:7——浮动 tag + 三方不一致，复现性受损 | README:43 vs compose:43 vs ci.yml |
| P2 | observability profile 服务（prometheus/jaeger/grafana）用 `latest` 浮动 tag | `docker-compose.yml:111,143,157` |

### 安全（意识在线，遗留两处停滞依赖）

JWT + RBAC 三级（admin/operator/viewer，`rbac.py`）、WebSocket 握手鉴权（`websocket.py:301,511`）、Lua 原子
限流、prompt_guard、OneBot 消息 SETNX 幂等去重、群聊回复四层策略带概率上限含 CQ 码清理防误判
（`service.py:57-204`）。

| 级别 | 问题 | 证据 |
|---|---|---|
| P2 | python-jose 维护停滞（建议 PyJWT）；passlib[bcrypt] 与 bcrypt 4.x 兼容问题频发（建议 argon2-cffi） | `pyproject.toml` |
| P2 | 群聊智能回复默认读所有消息（`ONEBOT_GROUP_AT_ONLY=false` 默认值），大群隐私观感需文档明示 | README:121 |

---

## 十、前端工程化与用户体验

- **Web Dashboard**：30 条路由覆盖角色/世界/地图/记忆/反思/日记/计划/关系/向量搜索/QQ 监控/成本/告警/
  导入导出，是完整的上帝视角运维台；Glassmorphism + Framer Motion 视觉投入明显；openapi-typescript
  类型生成管道已就位（`types/api-generated.d.ts`）；本次实测 lint + typecheck 全过。
- **QQ 端体验细节用心**：三层回复决策避免打扰、多段回复模拟打字节奏（0.6s 间隔、500 字截断）、主动分享
  无需用户先开口。

| 级别 | 问题 | 证据 |
|---|---|---|
| P1 | **前端零自动化测试**（vitest/playwright 均未使用），30 个页面纯手工保障 | 全仓 `*.test.{ts,tsx}` 计数为 0 |
| P2 | 手写接口类型（`lib/api.ts`）与 openapi 生成类型并存，边界未收敛，漂移风险 | `api.ts:46-92` vs `types/api-generated.d.ts` |
| P2 | 角色间单薄的社会互动（§四）最终限制 QQ 侧「我的角色有自己的生活」的可感知性——UX 问题根源在后端认知深度 | — |

---

## 十一、技术选型评价

| 选型 | 评价 |
|------|------|
| LangChain 1.x + 原生 openai SDK 并存 | ⚠️ 实际只用 ChatOpenAI 包装与 structured_output，LangChain 抽象收益低、大版本升级破坏面大；可评估直接依赖 openai SDK 减一层 |
| PostgreSQL 18 + pgvector(halfvec/HNSW) + HASH/RANGE 分区 | ✅ 单库统一结构化+向量，避免额外向量库运维，千万行规模前都是正确选择 |
| Redis（锁/Streams/实时状态/预算/限流） | ✅ 用法克制且每处都有 Lua 原子性意识；版本漂移待修（§九） |
| FastAPI + SQLAlchemy 2.0 async + asyncpg | ✅ 主流稳妥；repository 模式基本贯彻（PersonMemory 除外） |
| React 19 + React Compiler + TanStack 全家桶 + oxlint | ✅ 前端栈激进但自洽 |
| OTel + Langfuse + LGTM 栈 | ✅ 覆盖度罕见地完整；两套 trace 体系待融合 |
| apscheduler（分区预创建） | ✅ 轻量合适 |
| python-jose / passlib | ❌ 均处维护停滞，建议替换（§九） |

---

## 十二、生产可靠性风险汇总（按修复优先级）

### 必须立即修（P0，合计约 2-4 人日，均为接线级改动）

1. **接通认知回流三断流**（P0-1）：
   - `_perceive` 取最近 N 条 Reflection 注入 decision prompt（新增 `{reflections}` 占位符）≈0.5 天；
   - 用户对话链路调用 `get_relevant_context()` 注入 chat system prompt ≈0.5 天，收益立竿见影；
   - 日记摘要注入对应周期决策上下文 ≈0.5 天；
2. **修复时间衰减公式**（P0-2）：改指数衰减（如 `-max(0, days-3)^0.5 * k`）或高重要性豁免衰减 ≈0.5 天；
3. 顺手清理 `_cycle_probe.py`、compose 凭据参数化（`${POSTGRES_PASSWORD:?}`）。

### 一个月内（P1）

记忆生命周期治理（importance 分级 retention + 低价值压缩归档 + 写入去重）；`_perceive` 批量化重构
（合并 session、关系 IN 批查，目标单 Tick DB 往返 ≤4 次）；`planChanges` 落库接线；PersonMemory 升级
（增量合并 prompt + 启用 preferences + heat 衰减任务 + 改走 ORM）；检索 query 动态化（拼入计划/情绪/时段）；
chat_with 多轮化并同步双方会话上下文；乐观锁 version 贯通 update_state；拆分 main.py 三个后台循环；
compose 移除/补齐 PgBouncer + 版本 pin；前端 vitest 冒烟测试。

### 战略级

群体动力学实验（基于 `related_characters` 做传闻传播，低成本提升"小镇感"）；LangChain 依赖去留评估；
建立「README 特性 ↔ 实现」一致性门禁（参照 minicoding-rs 的教训：宣称未实现的特性应在文档中标注状态）；
Redis 清空冷启动恢复演练（验证 rehydration.py 闭环）。

### 风险量化备忘

- **R1 记忆膨胀**：`character_tick_seconds=30` → 每角色 ≥2,880 条 episode/天；5 角色 ≈ **530 万行/年**，
  halfvec(2048) 每条约 4KB → 仅向量 **≈20GB/年**，外加 HNSW 索引放大；HASH 分区不能按时间 drop，
  磁盘与检索延迟随月线性恶化；
- **R2 并发窗口**：Tick 锁只护 Tick 之间；QQ 高频群聊 + 主动分享会放大 API/Tick 并发写冲突概率；
- **R3 成本敞口**：预算控制已完善，剩余风险在大群默认读全部消息的 flash 调用量与 LLM 评分开关
  （开启后每条记忆多一次 chat 调用）。

---

## 十三、总评

AI Town 是一个**基础设施素养远超同类早期项目**的作品：分布式锁的三重防护（唯一 token/CAS/看门狗）、
embedding 队列的指数退避熔断、月度分区预创建对真实事故模式的修复、11 条告警规则、mypy strict 零豁免滥用——
这些细节多数到了商业产品水准。「世界驱动陪伴」的定位有真实的调度架构支撑，不是营销话术。

它的核心病灶不是工程能力，而是**两类系统性裂缝**：

1. **认知闭环只有前半环**——反思、Person Memory、日记三个子系统都认真实现了"生产"，却集体遗忘了"消费"。
   这不是某个 bug，而是缺少一条"认知产物必须回流上下文"的架构不变量；README 因此描述了一个比实际更聪明
   的角色。修复高度集中（三处接线 + 一个公式），属于高杠杆低成本的改动；
2. **为当下正确、为未来裸奔**——并发防护、成本控制、故障恢复都为"现在能跑"做了充分设计，但记忆膨胀、
   老年记忆可达性、前端测试缺位都是"六个月后必然爆发"的账单。数据层作者显然懂分区（RANGE 表都能按时间
   治理），唯独给记忆选择了 HASH 分区却没有配套生命周期方案。

完成 P0 冲刺（约一周内可交付）后，该项目即可兑现其 README 承诺的基本盘；再补上记忆生命周期治理，
便有能力从「架构示范品」跨入「可长期运行的陪伴产品」。

---

## 十四、修复状态追踪（2026-08-24 修复批）

本报告发布当日已完成一轮修复，逐章对应关系如下（commit 为 main 分支哈希）：

| 章节 | 修复项 | 状态 | Commit |
|------|--------|------|--------|
| §三 | 删除 `_cycle_probe.py`（全仓 ruff 42 错来源） | ✅ 已修 | `230f80e` |
| §三 | core→messaging 反向依赖解耦（runtime 回调） | ✅ 已修 | `230f80e` |
| §三 | main.py 瘦身（循环下沉 scheduler/loops.py、AuthMiddleware 迁 auth/） | ✅ 已修 | `230f80e` |
| §三 | API 层直连 repository ×5 | ⏸ 暂缓 | FastAPI 常规模式，重构收益低回归风险高 |
| §四 | nearby_characters N+1（复用一次性关系查询） | ✅ 已修 | `3bd9665` |
| §四 | world_events 去重基线持久化 Redis | ✅ 已修 | `3bd9665` |
| §四 | decision prompt 注入真实场景描述 | ✅ 已修 | `3bd9665` |
| §四 | chat_with 两轮往返对话 | ✅ 已修 | `3bd9665` |
| §四 | 对话即时写入对方上下文 | ⏸ 暂缓 | 记忆检索为既定回流路径，避免双写不一致 |
| §五 | **P0-1 认知回流三断流**（反思/PersonMemory/日记注入） | ✅ 已修 | `4e1da7a` |
| §五 | **P0-2 时间衰减改指数公式**（25% 下限永不为负） | ✅ 已修 | `4e1da7a` |
| §五 | planChanges 死功能落库（带归属校验） | ✅ 已修 | `4e1da7a` |
| §五 | 记忆写入去重 / 检索 query 动态化 | ✅ 已修 | `4e1da7a` |
| §五 | PersonMemory ORM 化 + preferences 落库 + 热度衰减任务 + 增量合并语义 | ✅ 已修 | `4e1da7a` |
| §六 | 工具启用状态 5s TTL 缓存 / 死代码字段清理 | ✅ 已修 | `b7b19dd` |
| §七 | memory_retention_loop 分级清理（HASH 分区膨胀治理） | ✅ 已修 | `6c4e83c` |
| §七 | update_state 自增 version | ✅ 已修 | `6c4e83c` |
| §七 | 容器启动自动迁移 / README PgBouncer 失真移除 | ✅ 已修 | `6c4e83c` |
| §八 | Langfuse 附带 OTel trace id / DB_QUERY_DURATION 接入埋点 | ✅ 已修 | `1bcb8ab` |
| §九 | python-jose→PyJWT / 移除零使用 passlib | ✅ 已修 | `a202481` |
| §九 | Redis 版本三方统一 redis:8-alpine / 可观测性 tag 固定 | ✅ 已修 | `a202481` |
| §九 | README 群聊隐私提示 | ✅ 已修 | `a202481` |
| §十 | vitest 冒烟测试（queryKeys/auth store）+ CI 接入 | ✅ 已修 | `9a67885` |
| §十 | 手写类型与生成类型边界标注 | ✅ 已修 | `9a67885`（后端输出命名 schemas 后替换） |
| 战略级 | 群体动力学 / LangChain 去留 / 冷启动演练脚本 | 📋 规划中 | 见 §十二战略级清单 |

修复后验证基线：`ruff check/format` 全过、`mypy --strict` 163 文件零错误、pytest **355 passed**、
前端 lint/typecheck 全过 + vitest 6 passed。
