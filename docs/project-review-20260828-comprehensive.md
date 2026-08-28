# ai_town 全面架构评审报告

> **审查日期**：2026-08-28
> **代码基线**：`main` @ `86d4d8b`（feat(chat): inject recent experiences from Tick perception into messaging replies）
> **审查范围**：项目定位、架构分层、认知机制、多智能体交互、ReAct 工具调用、多端触达、数据持久化、可观测性、安全、前端工程化、Docker 部署、长期运行风险、用户体验
> **审查方法**：全量代码走查（后端 46,447 行 / 前端 16,690 行）+ 配置与迁移脚本审阅 + CI 与编排审阅 + 关键结论逐条代码复核
> **证据约定**：本报告所有结论均标注 `文件:行号`，未标注者为归纳性判断

---

## 目录

- [一、执行摘要](#一执行摘要)
- [二、项目定位评估](#二项目定位评估)
- [三、分层架构与模块边界](#三分层架构与模块边界)
- [四、核心子系统深度评估](#四核心子系统深度评估)
- [五、长期运行风险](#五长期运行风险)
- [六、用户体验评估](#六用户体验评估)
- [七、技术选型评估](#七技术选型评估)
- [八、问题清单](#八问题清单)
- [九、改进建议与路线图](#九改进建议与路线图)
- [十、总体评价](#十总体评价)

---

## 一、执行摘要

### 1.1 总体判断

ai_town 是一个**完成度相当高、工程纪律罕见地严格**的 LLM 驱动多智能体虚拟小镇实现。它不是玩具项目：775 个测试用例、CI 四道质量闸门（ruff + mypy strict + pytest + OpenAPI/前端类型契约守卫 + 部署冒烟）、21 个数据库迁移、16 个编排服务、分层记忆治理、带死信队列的事件兜底机制——这些是成熟工程团队的产出特征。

项目对 **Generative Agents（Park et al. 2023）** 的理解是准确的，且在若干方面做了有价值的工程化改进（时间衰减设 25% 下限避免老记忆永久不可达、反思的语义去重、压缩前绝不删除的不变量、版本感知的对账仲裁而非朴素 LWW）。

但项目当前存在**三个结构性短板**，它们共同决定了"能否长期稳定运行"这一核心命题：

| # | 结构性短板 | 性质 | 影响 |
|---|---|---|---|
| **S1** | 记忆生成速率与清理速率存在约 **230 倍**缺口 | 架构性 | 长期运行必然存储与索引崩塌 |
| **S2** | 并发保护是**协作式**而非**防护式**（无 fencing token） | 架构性 | 存在最长约 10 秒的跨实例双写窗口 |
| **S3** | 权限模型**已声明但未接线**（scopes 全库零读取） | 架构性 | 无真正的角色隔离，静态 Key 等同超级管理员 |

这三点的共同特征是：**机制都已建好，但关键参数或关键接线缺失**。这不是设计能力问题，而是"最后一公里"问题——也因此是可修复的。

### 1.2 分项评分

| 维度 | 评分 | 判断依据 |
|---|:---:|---|
| 项目定位清晰度 | ★★★★☆ | 定位准确，但"多智能体"的自主性边界尚未定义清楚 |
| 分层架构与模块边界 | ★★★★☆ | 分层清晰、依赖方向正确；Service 层沉淀不完整（自认现状） |
| 世界引擎与 Tick 循环 | ★★★★☆ | 链路完整、锁与看门狗齐备；缺 fencing token |
| 认知机制完备性 | ★★★★☆ | 记忆/反思/规划/用户记忆四层齐备且可演化；反思触发与规划修订偏刚性 |
| 多智能体交互合理性 | ★★★☆☆ | 交互通道丰富，但自主性受限、无共享对话状态机 |
| LLM 抽象与 ReAct | ★★★☆☆ | ReAct 骨架正确；双栈并存、embed 无超时、参数校验弱 |
| 多端触达成熟度 | ★★★★☆ | Web + QQ 双通道均打通，事件队列设计优秀；输出侧无过滤 |
| 数据持久化设计 | ★★★★☆ | 分区、向量索引、索引治理考虑周到；分区策略与保留策略不匹配 |
| 可观测性覆盖 | ★★★★☆ | OTel + Langfuse + Prometheus + structlog 四件套齐备 |
| 安全与权限 | ★★☆☆☆ | 入站防护到位；RBAC 未接线、越权校验缺失、输出零过滤 |
| 前端工程化 | ★★★★☆ | 技术栈现代、类型契约自动化；测试仅 5 个文件且零页面测试 |
| Docker 部署 | ★★★★☆ | 编排完整、资源受限、profile 分层；backend 无健康检查 |
| 文档与代码一致性 | ★★☆☆☆ | `memory-system.md` 与实现显著漂移 |
| **综合** | **★★★☆☆（3.6/5）** | 工程底座扎实，需补完"最后一公里"才能承载长期运行 |

### 1.3 最须优先处理的五个问题

| 优先级 | 问题 | 位置 |
|:---:|---|---|
| **P0** | 记忆清理吞吐（300/日）远低于生成速率（约 69,120/日） | `loops.py:702`、`config.py:139,228` |
| **P0** | `embed()` 无超时，最坏可挂 30 分钟且持续占锁与信号量 | `client.py:115,123` |
| **P1** | 预算检查与记账非原子，且无按角色/用户配额 | `client.py:987,1013` |
| **P1** | RBAC scopes 声明后全库零读取，静态 Key 权限等同超管 | `api_keys.py:50`、`middleware.py:103-108` |
| **P1** | `_memorize` 绕过失锁闸口，违背 H10 文档化不变量 | `tick.py:327` vs `tick.py:991-994` |

---

## 二、项目定位评估

### 2.1 定位陈述

项目自我定位为「LLM 驱动的多智能体虚拟小镇」：24 个角色（`configs/characters/`）在 12 个场景（`configs/scenes.yaml`）中按 30 秒世界节拍自主生活、社交、形成记忆与关系，并通过 Web Dashboard 与 QQ 双通道与真实用户交互。

### 2.2 与参照系的对齐度

对照 Generative Agents 的三大支柱：

| 支柱 | 论文原设计 | ai_town 实现 | 评价 |
|---|---|---|---|
| 记忆流 Memory Stream | 向量 + 重要性 + 新近度三因子检索 | `memory_repo.py:44-48`，加权和 × 指数衰减 | ✅ 实现，且有改良 |
| 反思 Reflection | 积累到阈值触发，自下而上提炼 | `reflection_service.py:80-83`，阈值 20 + 重大事件触发 | ✅ 实现，且双通道触发 |
| 规划 Planning | 日计划 → 递归分解 | `daily_plan_service.py:34-54`，每日 06:00-09:00 生成 | ⚠️ 仅日计划，无递归分解 |

**对齐度良好**。项目并非照搬，而是针对"长期连续运行"这一论文未覆盖的工程命题做了大量补强（分区表、索引治理、分级保留、压缩归档、预算熔断、对账仲裁），这是它的真实价值所在。

### 2.3 定位上的模糊地带（需明确）

**问题：未定义"多智能体"的自主性边界。**

当前架构中，角色 B 的对话回复是在角色 A 的 Tick 内同步生成的（`social.py:230-242`）：

```
A 的 Tick → 决策 chat_with → 在 A 的 Tick 内调用 LLM 生成 B 的回复 → 写入双方记忆与关系
```

这在工程上有合理性（避免跨 Tick 的对话状态机复杂度），并已用跨角色资源锁保护（`social.py:145`）。但它意味着：

- **B 的行为可被 A 代打**：B 的回复不受 B 自身的决策流程、当前计划、情绪状态驱动，而是由 A 的上下文拼装出的 prompt 决定。这削弱了 "B 是独立智能体" 的语义。
- **B 的记忆被外部进程写入**：B 的记忆流中混入了并非由 B 自己"经历"的内容（虽然是同一事件的第一人称改写，语义上可接受）。

**建议**：不必推翻现有设计（跨 Tick 对话状态机的复杂度不值得），但应在文档中明确定义：本项目的"多智能体自主性"是**Tick 级自主 + 交互级协作代打**模型，并说明其取舍。定位清晰比定位完美更重要。

### 2.4 定位合理性结论

**定位合理且可信**。项目在"LLM 驱动多智能体"这一领域处于**工程实现层面的前列**——多数同类开源项目止步于演示原型，而 ai_town 认真处理了持久化、并发、成本、可观测性、部署这些"不酷但致命"的问题。这不是学术复现项目，而是一个**可持续运行的系统**。

---

## 三、分层架构与模块边界

### 3.1 分层现状

```
API 层      src/api/         62 个 REST 端点 + 1 WS + 1 OneBot WS
                ↓
Service 层  src/services/、src/messaging/、src/memory/     ⚠️ 沉淀不完整
                ↓
Core 层     src/core/        world/engine.py、character/tick.py
                ↓
Infra 层    src/db/、src/llm/、src/adapters/
                ↓
Cross-cut   src/observability/、src/cost_control/、src/security/、src/auth/
```

项目在 `AGENTS.md §4.4` 中已诚实自认现状：

> 通用 Service 层尚未完全落地——目前仅 `MessageService`（messaging）是完整的 Service 层组件，其余 API 路由直接查询 Repository。

不过从 `git log` 可见近期已有改进动作（`cd8dc2a refactor(backend): service-ize world/actions api, unify background tasks...`），方向正确。

### 3.2 边界的正面评价

- **`src/modules/` 的领域模块划分清晰**：`movement` / `duration` / `relation` / `schedule` / `town` / `character` 各自内聚，与世界引擎解耦，可独立演化。
- **配置外置彻底**：Prompt 全部 YAML 化（`configs/prompts/` 21 个文件）、角色卡 YAML 化、场景与连通矩阵 YAML 化，且启动时 fail-fast 校验（矩阵对称性、场景 ID 交叉校验、embedding 维度探测）。这是优秀实践。
- **副作用边界明确**：`Action executor` 不直接写状态，返回 `new_state` 由执行层统一写入；工具产生的 delta 只改内存，与 ActionRecord 同事务落库（`tick.py:1234`）。LLM 无法直接触碰状态（状态参数由 `injected_params` 注入，LLM 不可伪造 —— `registry.py:473-483`）。这一条守得很牢。

### 3.3 边界存在的问题

| 问题 | 位置 | 说明 |
|---|---|---|
| **API 层直连 Repository** | `api/characters.py`、`api/admin.py` | 业务逻辑散落在路由函数内，`admin.py` 已达 1065 行 |
| **LLM 双栈并存** | `client.py:113-127` (原生 AsyncOpenAI) 与 `fallback.py:119` (LangChain ChatOpenAI) | 两条并行调用链，超时/重试/成本策略不统一 |
| **`src/core/character/tick.py` 过大** | 1425 行 | 承担感知、决策、ReAct、执行、记忆、社交、分享、传闻，职责过载 |
| **`scheduler/loops.py` 过大** | 1076 行，9 个后台循环 + 4 类保留任务 | 运维逻辑与调度逻辑混杂 |

**建议**：`tick.py` 应拆分为 `TickOrchestrator`（编排）+ 各步骤策略对象；`loops.py` 中的 4 类保留任务应抽到 `src/retention/` 独立模块。

---

## 四、核心子系统深度评估

### 4.1 世界引擎与 Tick 循环

#### 4.1.1 执行链路

一个角色 Tick 的完整链路（`tick.py:216-352`）：

```
SET NX EX 抢锁(:232) → 启看门狗(:241) → 信号量(:249) → _execute_tick(:278)
  ├─ 1. _perceive              感知（1× embed）
  ├─ 2. get_candidates          代码过滤候选 Action
  ├─ 3. _decide                 1× structured_output
  ├─    _run_react_loop         最多 3× structured_output
  ├─ ⛳ H10 失锁闸口(:314)
  ├─    _maybe_structured_encounter   round-7 G1 结构化相遇
  ├─ 4. _execute_action         PG 事务 → Redis 镜像
  ├─ 5. _memorize               记忆沉淀            ⚠️ 无失锁闸口
  ├─ 5.5 _propagate_gossip      传闻传播            ⚠️ 无失锁闸口
  └─ 6. _maybe_proactive_share  主动分享
```

**最坏情况单次 Tick 需 12 次串行 LLM 往返**（感知 1 + 决策 1 + ReAct 3 + 对话 4 + 质量评估 1 + 群活动 1 + 记忆评分 1），全部 `await` 串行，无批处理、无并行。按每次 2-5 秒计，单 Tick 延迟可达 **25-60 秒**，已接近或超过 30 秒的节拍间隔（`config.py:228`）。这是一个**隐性瓶颈**：节拍会被实际执行时间拖长。

#### 4.1.2 并发模型（正面为主）

| 机制 | 实现 | 评价 |
|---|---|---|
| 多角色并行 | `asyncio.gather`（`tick.py:1419`），上限 10（`config.py:229`） | ✅ |
| 单角色锁 | `char:tick:lock:{id}`，TTL 30s，uuid4 token（`tick.py:190-191`） | ✅ |
| 看门狗续租 | Lua CAS，间隔 ttl/3=10s（`locks.py:47-53,206`） | ✅ |
| 失锁闸口 | 6 处（`tick.py:314,998,1140,1239`、`tick.py:765`、`social.py:298`） | ✅ |
| 跨角色锁 | 按 ID 排序获取防死锁（`locks.py:120`） | ✅ 考虑周到 |
| 世界引擎 | 纪元 CAS 原子写（`engine.py:52-63`） | ✅ 真正的防护式并发 |

#### 4.1.3 并发问题

**问题 C1（中）：Tick 锁无 fencing token**

世界引擎用了纪元 CAS（`engine.py:52-63`），但角色 Tick 锁是**协作式轮询检测**——看门狗每 10 秒才检查一次续租结果。若锁在 T 时刻过期、他实例 T+0 接管，本实例要到 T+10 才感知，**存在最长约 10 秒的双写窗口**。

> 对比：`WorldEngine` 已实现防护式并发，说明团队具备该能力，Tick 侧属于未补齐。

**问题 C2（中）：跨角色锁路径无失锁信号**

`lock_watchdog`（`locks.py:167-177`）续租失败**只记日志**；`watch_locks`（`locks.py:180-221`）才会置位 `lost`。二者差异在 `locks.py:189` 的 docstring 中被明确记录：

> 后者只记日志，调用方无从感知锁已易主

而 `social.py:145` 的 `acquire_resource_locks` 使用的是前者。后果：A→B 的 chat_with 期间若 B 的锁静默易主，A 仍会写入 B 的关系与记忆（`social.py:306-355`），与 B 的新持有者构成双写。

**问题 C3（中）：PG → Redis 双写非原子**

写入顺序为 PG 事务提交（`tick.py:1146`）→ 再写 Redis（`tick.py:1245`）。Redis 失败时重试一次并入优先对账队列（`tick.py:1288-1299`），对账周期 600 秒（`loops.py:357`）。因此**状态漂移窗口最长 = 1 次重试 + 600 秒**。

对账采用**版本感知仲裁**而非朴素 LWW（`reconcile.py:252-308`）：以 `char:{id}:rec_ver` 为基线，PG 版本前进则 PG 胜、否则 Redis 胜。设计正确。

> 附带问题：`rec_ver` 键用 `redis.set` 写入且**无 TTL**（`reconcile.py:319`），仅在删除角色时清理（`character_repo.py:187`），存在缓慢泄漏。

#### 4.1.4 冷启动恢复

`rehydrate_states`（`rehydration.py:48-83`）采用"键缺失才回灌"策略，场景占用三件套全量重建（`:86-120`），演化器哈希依赖 `evolve` fallback 重建（`:69`）。有 5 个测试用例支撑（`tests/test_rehydration.py`）。恢复路径设计合理。

### 4.2 认知机制：记忆流 / 反思 / 规划 / 用户专属记忆

这是项目最完整、也最见功力的部分。

#### 4.2.1 七层认知产物

| 层 | 载体 | 写入时机 | 检索方式 |
|---|---|---|---|
| 感知/短期 | `conversations.context` (JSONB) | 会话 >50 条触发压缩（`messaging/service.py:55,992`） | 整段摘要注入 |
| 情景记忆 | `memory_episodes` | 每 Tick 1 条（`tick.py:1332`）+ 工具产物 + 传闻 + 社交 | pgvector 混合检索 |
| 反思 | `reflections` tier=1/2 | 阈值 20 条 / 重大事件（`reflection_service.py:80,58`） | 向量 + 时间兜底 |
| 日记 | `character_diaries` | 世界时间 22:00-06:00 + 周/月/年（`diary_service.py:61-77`） | 仅取最新 1 篇 |
| 计划 | `plans` | 每日 06:00-09:00（`loops.py:291-338`） | 全量注入（截断 6 条） |
| 用户专属记忆 | `person_memories` + `person_memory_entries` | 每次交互后异步追加（`messaging/service.py:504-514`） | 字符二元组重叠 |
| 传闻 | `memory_episodes.source_type='gossip'` | Tick 步骤 5.5 | 时间倒序取 2 条 |

#### 4.2.2 检索算法（有改良的实现）

```sql
-- memory_repo.py:44-48
(sim_score * 0.6 + importance * 0.05) * (0.25 + 0.75 * exp(-GREATEST(0, days) / 30.0))
```

三处工程改良值得肯定：
1. **时间衰减设 25% 下限**（`memory_repo.py:466-467`）——避免老记忆永久不可达，这是对原论文的实质性改进。
2. **`GREATEST(0, ·)` 钳制时钟回拨**（`memory_repo.py:44-47`）——防御性但必要。
3. **候选池放大 + ef_search 调优**：`top_k × 4`（`memory_repo.py:508`），`SET LOCAL hnsw.ef_search=100`（`memory_repo.py:482`）。

**问题 M1（中）：重要性权重形同虚设**

`importance` 权重 0.05，`sim_score` 权重 0.6。importance 从 1 到 10 仅贡献 0.45 分差，而相似度一项即可贡献 0.6 分差。实际效果是**重要性对召回排序几乎没有影响**，退化为纯语义检索。建议将 importance 权重提升至 0.2-0.3 量级并做离线评测校准（`git log` 显示已引入 retrieval quality 评测管线 `ef676be`，正好可用于调参）。

**问题 M2（中）：无 token 预算控制**

只有单条 500 字符硬截断（`perception.py:40`）、计划 6 条、日记 300 字的**条数/字符数**约束，**没有全局 prompt token 预算**。随着反思、人物记忆、计划、日记、传闻逐层叠加，决策 prompt 会单调膨胀——这既推高成本，也会稀释注意力。

#### 4.2.3 反思机制

- **双通道触发**：数量阈值 20 条（`reflection_service.py:80-83`）+ 重大事件（importance ≥ 9 且有 300s 冷却键，`reflection_service.py:58-65`）。
- **双层反思**：tier-1 主题反思（2-4 条，挂 `reflection_sources` 溯源）→ tier-2 元反思（累计 ≥6 且 7 天冷却且 tier-1 ≥3 条，产出「[长期倾向]」，`reflection_service.py:195-260`）。
- **语义去重**：写前与既有 tier-1 做余弦比对，阈值 0.95 且**不限时间窗**（`reflection_repo.py:74-106`），避免反思自我繁殖。
- **回灌决策**：语义召回 5 条注入决策 prompt 的 `{reflections}`（`perception.py:198-205`、`tick.py:581`）——形成闭合回路。

**评价**：这是本项目认知设计中最出色的部分。双层 + 溯源 + 语义去重 + 回灌，构成了一个**可演化的认知架构**，而非一次性流程。

> 小瑕疵：`episodes[memory_id - 1]`（`reflection_service.py:176`）依赖 LLM 返回 1-based 编号，虽有 `_parse_themes` 钳制到 `[1, total]`（`:296`），但主题 memory_ids 为空时 `derived_importance` 恒为 3，空 grounding 主题仍会落库。

#### 4.2.4 规划机制（相对最弱）

- 生成：每世界日 06:00-09:00，每角色最多 2 条（`daily_plan_service.py:23,99`）。
- 追踪：字符二元组重叠 ≥0.34 则 `progress += 10`（`plan_applier.py:135-168`）。
- 失效：超 24h 置 `expired`（`loops.py:659-684`）。
- 修订：LLM 可通过 `planChanges` 通道改进度/状态（`tick.py:618-622`）。

**问题 P1（中）：幂等判定脆弱**

`_has_daily_plan` 用 `day_key in plan.title` 做字符串匹配（`daily_plan_service.py:56-61`）。任何 LLM 生成含日期串的标题都会被误判为"今日已规划"；且 `get_active_plans` 只返回 active，计划过期后同日重跑会重复生成。应改为独立的 `plan_date` 列 + 唯一约束。

**问题 P2（中）：无独立的重规划（replan）任务**

`docs/memory-system.md:203` 自认"无独立 replan 任务"。当前规划修订完全依赖 Tick 内的 LLM 主动输出 `planChanges`——被动且不可靠。

**问题 P3（中）：无递归分解**

论文的日计划 → 小时分解 → 分钟动作三层递归未实现。当前只有"日计划 + 逐 Tick 决策"两层，中间层缺失，导致日计划与即时行为之间的耦合较弱（二元组重叠 0.34 的匹配方式也较粗糙）。

#### 4.2.5 用户专属记忆（person_memory）

**设计**：
- 两层结构：`person_memory_entries`（append-only 事实条目）+ `person_memories.content`（主档）。每次交互追加新事实，**不做全文重写**（`person_memory_service.py:85-143`）——避免了信息丢失，设计正确。
- 合并：每 6h，未压缩条目 ≥20 的 (角色, 用户) 对由 LLM 合并进主档，条目标 `compacted=TRUE` 软归档（`loops.py:498-592`）。
- 膨胀控制：热度 `heat+1` 上限 500（`person_memory_service.py:230`）；14 天未交互者 heat 减半（`loops.py:465-495`）；已压缩条目 180 天后删除（`loops.py:768-780`）。
- 隔离：`character_id + user_id` 双条件查询（`person_memory_service.py:56-59`）；API 层校验 `user_id != user["user_id"]` 且非特权角色 → 403（`api/memory.py:137`）。

**问题 PM1（中）：召回算法无语义**

近 50 条未压缩条目 → 按**字符二元组重叠**取 8 条（`person_memory_service.py:24-29,273-286`）。二元组重叠不携带语义，对多义词、长句、改写后的表达的召回质量差。这与记忆流主体用的 pgvector 形成明显落差——用户记忆恰恰是**最需要准确召回**的场景（用户会期待角色"记得我说过什么"）。建议改为 pgvector 检索。

**问题 PM2（低-中）：跨用户间接泄露面**

`get_top_users_context` 将用户记忆注入**镇内决策**，且仅剥离平台前缀（`person_memory_service.py:321`），仍将可识别的 `user_id` 与亲密度送进 LLM prompt，并被 `action_records` 持久化。虽不是直接泄露，但构成间接通道。

#### 4.2.6 记忆膨胀治理（**最严重的问题**）

分级保留策略本身设计正确（`config.py:131-133`、`memory_repo.py:251-283`）：
- `importance <= 3` → 保留 90 天
- `importance 4-6` → 保留 180 天
- `importance >= 7` → **永久保留**

压缩流程也有正确不变量：**压缩失败整组跳过，绝不未压缩先删**（`loops.py:1011`）。

但**吞吐量配置存在数量级错误**：

```
生成速率：24 角色 × (86400s / 30s) = 69,120 条/日      [config.py:228，24 个角色卡]
清理速率：300 条/周期 × (86400s / 86400s) = 300 条/日   [config.py:139，loops.py:702]
                                ─────────────────────
                                缺口约 230 倍
```

且 `interval = 24 * 3600` 在 `loops.py:702` 中**硬编码**，无法通过配置调整。

**量化影响**：每条 memory_episode 含 `halfvec(2048)` 向量（约 4KB）+ 文本，按 5KB/行估算：
- 日增约 345 MB（不含 gossip、工具记忆、社交记忆的额外写入）
- 年增约 **126 GB**，叠加 HNSW 索引膨胀（约 1.5-2 倍）实际更高

**附带问题**：`memory_episodes` 使用 **HASH 分区（16 个）**（`0002_optimize.py:116`），无法像 RANGE 分区那样按时间 `DROP PARTITION`，膨胀治理只能在应用层逐行处理——这进一步放大了吞吐缺口的影响。`loops.py:695-697` 的 docstring 已明确记录这一约束，说明团队知晓，但尚未匹配解决方案。

> **这是本项目最需要在上线前解决的问题。** 建议见 §9.1。

#### 4.2.7 嵌入管线

- 异步：Tick 只写 `embedding=NULL, materialized=False`，不阻塞（`episode_service.py:184-197`）；`EmbeddingWorker` 后台轮询。
- 批量 + 并发安全：`fetch_unmaterialized(limit=20)` 配 `FOR UPDATE SKIP LOCKED`（`memory_repo.py:330-352`），整批单次 API 往返。
- 失败处理：指数退避 60/180/600/1800s，`fail_count >= 5` 熔断不再拉取（`memory_repo.py:386-451,343`）。
- 维度安全：启动时探测 + 校验，维度错配 fail-fast（`main.py:228-253`）；`_fit_dim` 只截不升（`client.py:150-158`）。
- 写入去重：向量化时余弦 ≥0.95 判重，重复行置 `is_duplicate=TRUE, embedding=NULL` **并同时置 `is_reflected=TRUE`** 防幻影计数（`memory_repo.py:220-233`）——细节考虑周到。

**问题 E1（中）：无 embedding 缓存**

同一 query 每 Tick 重新 embed（`perception.py:154`）。感知 query 的重复率高，缓存命中率应当可观。

### 4.3 多智能体交互合理性

#### 4.3.1 交互通道清单

| 通道 | 实现 | 位置 |
|---|---|---|
| 1v1 对话（`chat_with`） | 最多 2 轮往返，4 次 LLM 调用 | `social.py:230-242` |
| 群体活动（`group_activity`） | 同场景 ≥3 人触发 | `tick.py:301-303` |
| 结构化相遇 | round-7 G1，wait 时概率触发闲聊 | `tick.py:321` |
| 传闻传播（gossip） | 好友显著经历 → 二手记忆 | `tick.py:331` |
| 关系图 | 双向关系强度增量 | `modules/relation/graph.py:241` |
| 主动分享 | LLM 决策产生分享意图 | `tick.py:344` |
| 冲突化解 | `social.resolve_conflict` 工具 | `registry.py` |

#### 4.3.2 合理性评价

**正面**：
- **传闻传播**是最有价值的设计——它使信息能在智能体网络中扩散，而无需全局广播或让每个智能体都"亲历"事件。这是实现"社会性"的低成本路径，也是 Emergent 行为的主要来源。
- **结构化相遇**（round-7 G1）是对"LLM 很少主动选择社交"这一实测问题的务实修正——不依赖 LLM 偶然决策，用规则兜底提升交互密度。
- **跨角色资源锁**（`social.py:145`）正确处理了 A→B 与 B→A 的并发竞争，且按 ID 排序防死锁。

**问题 I1（中）：对方回复由发起方"代打"**

如 §2.3 所述，B 的回复在 A 的 Tick 内生成。这带来两个后果：
1. B 的回复不受 B 自身决策流程驱动，削弱智能体自主性语义。
2. B 的记忆与关系被 A 的 Tick 进程写入（虽已用跨角色锁保护，但锁若静默失效则无感知 —— 见问题 C2）。

**问题 I2（中）：无共享对话状态机**

`chat_with` 固定 2 轮（`config.py:265`），且是 A→B→A→B 的刚性结构。没有：
- 多轮持续对话（跨 Tick 延续话题）
- 打断、沉默、拒绝交谈等交互形态
- 对话中的角色主动性（B 无法发起话题，只能由 A 发起）

结果是**对话形态单一**，长期观察易显重复。

**问题 I3（低-中）：交互密度依赖场景共现**

24 个角色分布在 12 个场景，随机共现概率不高；`group_activity` 要求同场景 ≥3 人，`chat_with` 也要求同场景。结构化相遇机制部分缓解了这一点，但交互仍以"偶遇"为主，缺少**基于关系的主动寻访**（如"去找好友聊天"的移动意图）。

#### 4.3.3 交互设计结论

**交互机制丰富且有巧思，但深度不足。** 项目构建了完整的交互"骨架"（对话、群活动、传闻、关系、冲突），但每类交互的"血肉"（多轮持续性、主动性、交互形态多样性）尚浅。这直接决定了用户长期观察时的**新鲜感衰减速度**——见 §6.2。

### 4.4 LLM 抽象与 ReAct 工具调用

#### 4.4.1 LLM 抽象层

能力矩阵（`client.py`）：`chat`、`chat_with_usage`、`multimodal_chat`、`structured_output`、`multimodal_structured_output`、`embed`、`embed_batch`、`embed_multimodal`、`generate_image`、`generate_video`。

**问题 L1（高）：双栈并存，策略不统一**

| 调用路径 | 客户端 | 超时 | 重试 | fallback | 预算 |
|---|---|---|---|---|---|
| `chat` / `structured_output` | LangChain `ChatOpenAI` | 30s（`fallback.py:119`） | 2（`config.py:28`） | ✅ | ✅ |
| `embed` / `embed_batch` | 原生 `AsyncOpenAI`（`client.py:115,123`） | **无 → SDK 默认 600s** | SDK 默认 2 | ❌ | ✅ |
| `generate_image` | `self.openai`（`client.py:569`） | 无 | 默认 | ❌ | ❌ |
| `generate_video` | httpx（`client.py:652`） | 无 | 默认 | ❌ | ❌ |

**问题 L2（高）：`embed()` 无超时 —— 最危险的单项缺陷**

`AsyncOpenAI` 构造未传 `timeout`（`client.py:115,123`），SDK 默认 600 秒 × 默认 2 次重试 = **最坏 30 分钟**。

而 `embed()` 在感知阶段被同步调用（`perception.py:154`），此时角色持有 Tick 锁（持续续租）并占用并发信号量槽位。后果：

```
embed 挂起 30 分钟 → 该角色槽位被占满 30 分钟 → 锁被持续续租 30 分钟
  → 其他实例无法接管该角色（锁未过期）→ 该角色"假死"半小时
  → 并发上限 10，多个角色同时挂起则整个小镇停摆
```

这是**单线程故障即可拖垮全局**的缺陷，且极易在真实网络抖动中触发。修复成本极低（构造时传 `timeout=30`），性价比最高的改进项。

**问题 L3（中）：无流式输出**

`src/llm/` 全目录无 `astream` / `stream=True`。对于 Web 聊天与 QQ 这类交互式场景，用户需等待完整生成结束才能看到回复（长回复可达 10+ 秒），体验损失明显。

**问题 L4（中）：schema 转换能力弱**

仅支持 6 类映射，嵌套 object/array 退化为 dict/list，不支持 enum（`client.py:1022-1048`）。这限制了 structured output 的表达力，也是问题 L5 的成因之一。

#### 4.4.2 ReAct 实现

- 位置：`tick.py:639-698`，入口 `:309-310`
- 最大轮次：**3 轮**（`tick.py:655`），超限强转 `wait`（`:690-696`）
- 解析方式：**structured output**（`tick.py:611`），schema 内联（`:527-564`）
- 观察回注：`<observation>` 标签 + 800 字符截断（`tick.py:603-608`）
- 失败恢复：缺 `tool_name` 合成失败观察（`:672-681`）；超时/异常转观察不抛（`registry.py:423-430`）
- 与"直接选 Action"共存：`_resolve_action_id` 放行 `use_tool` 保留字，其余非候选回退 `wait`（`tick.py:83-91`）——共用同一 `action` 字段，设计简洁

**问题 L5（中）：工具参数校验过弱**

只校验必填参数是否存在（`registry.py:488-495`），**无类型校验、无枚举校验、无范围校验**。幻觉出的 `quantity=-5` 或 `item_id="不存在的ID"` 会直接进入工具函数，由各工具自行兜底。

**问题 L6（中）：串行执行，无并行工具调用**

多个工具调用逐个执行。考虑到单 Tick 最坏已有 12 次 LLM 往返，工具再串行会进一步拉长延迟。

**问题 L7（高）：`generate_video` 脱离 ReAct 管控**

`media.py:82` 使用 `spawn_background` 启动后台任务，绕过 `tool_timeout`（`registry.py:417`）与 `lock_lost` 信号。视频生成耗时可达分钟级，其完成回调若写入角色状态，将完全在锁保护之外进行。

#### 4.4.3 工具集

18 个工具，按 namespace 组织（`registry.py:49-212`）：

```
shop.*         5 个   商品浏览/购买/出售
knowledge.*    2 个   知识库查询/分类
social.*       3 个   送礼/邀约/冲突化解
world.*        4 个   世界信息/找人/场景信息
self_info.*    2 个   关系查询/记忆搜索
media.*        2 个   绘图/视频
```

其中 `state_mutating=True` 共 5 个（`registry.py`），需产生状态 delta。

**正面**：
- **状态参数由 `injected_params` 注入，LLM 不可伪造**（`registry.py:473-483`）——这是工具设计中最关键的安全边界，守得很牢。
- 内嵌业务校验扎实：送礼校验自送/库存/关系区间/分层价值上限（`social.py:216-277`）、约会要求关系 ≥40（`social.py:371`）。

**问题 T1（中）：无统一的 precondition 机制**

`AGENTS.md §4.2` 明确规定「Action 必须有 precondition」，且 Action 层确实实现了（`actions/registry.py:115`）。但**工具层没有对等的 precondition 抽象**，业务校验内嵌在各工具函数内。这导致工具的可用性判断无法被统一查询、测试与组合。

**问题 T2（中）：工具权限仅有全局开关**

Redis `tools:enabled`（`registry.py:31,239-278`，5s TTL）是**全局开关**，无按角色/场景过滤。所有角色在任何场景都能调用全部 18 个工具——包括"在森林里买咖啡"这类语义不合理的情况。相比 Action 层严格的 `scene + activity` 绑定（§AGENTS.md 4.2），工具层的一致性明显更弱。

### 4.5 多端触达：Web Dashboard + QQ

#### 4.5.1 QQ（OneBot）通道

适配器形态：**WebSocket 服务端**，接收 NapCat / Lagrange 等实现的反向连接（`adapters/onebot.py:4-6`、`onebot.py:513`）。

已实现能力：
- ✅ 私聊 + 群聊（`@` 检测支持 OneBot 11 的 at 段、`[CQ:at]` 码、`to_me` 字段三种形态 —— `onebot.py:302-312`）
- ✅ 群-角色映射（不同群绑定不同角色，`onebot.py:195-229`）
- ✅ 用户映射 `qq_{user_id}` + `platform="qq"`（`onebot.py:13`）
- ✅ 入站限流（固定窗口，按 chat_type + chat_id 分键 —— `onebot.py:78,126-134`）
- ✅ 派发串行化（同会话按 chat_key 排队，`onebot.py:808-838`）
- ✅ 出站文本消毒（`sanitize_outbound_qq_text`，`onebot.py:410,1363`）
- ✅ 主动消息 / 主动分享（`messaging/proactive_sharing.py:678`）
- ✅ 动作响应解析兼容 v11/v12（`onebot.py:138-165`）

#### 4.5.2 事件队列（**本项目的亮点设计**）

`messaging/event_queue.py` 基于 Redis Streams Consumer Group 实现**至少一次语义**：

```
入站事件 → XADD 持久化 → 内联快速处理 → 成功则 XACK + XDEL
                                     ↘ 崩溃/失败 → recover_drain() 重放
                                                 → 投递 >5 次 → 死信流
```

值得特别肯定的两处工程判断：

1. **成功必须 XDEL 而非只 XACK**（`event_queue.py:9-17` 的 docstring 详细论证）：XACK 只清 PEL，内联快速路径的条目从未经 `XREADGROUP` 投递、根本不进 PEL，对它 XACK 是空操作，条目会永久留在流中被恢复循环当新条目无限重投。**这类"踩过坑才知道"的知识被完整记录进 docstring，是高质量工程的标志。**
2. **毒消息转死信流**（`event_queue.py:86-102`），投递超 5 次即隔离，不阻塞后续。

**评价**：多端触达的**可靠性设计成熟度很高**，达到了生产级消息系统的水准。

#### 4.5.3 Web Dashboard 通道

- WebSocket 推送 + 30 秒轮询兜底（前端 `queries.ts:78,98,126,200,229`），推送会主动 invalidate，轮询仅作断连兜底——设计正确。
- 断线重连：指数退避 `min(1000 × 2^n, 30000)` 且有次数上限（`useDashboardSocket.ts:97-104`）。
- Token 经 `Sec-WebSocket-Protocol` 子协议传递（`useDashboardSocket.ts:79`），规避 URL 传参泄露。

#### 4.5.4 触达层问题

**问题 D1（中）：输出侧零过滤**

入站用户消息有 `PromptGuard` 防护（`messaging/service.py:356,377,862`），但 **LLM 输出 → 用户** 的方向没有任何过滤。角色可能输出不当内容、泄露内部字段（如 action id、user_id）、或被诱导输出系统提示词。

**问题 D2（中）：二阶提示注入未防护**

`PromptGuard` 全库仅 3 处调用，均在 `messaging/service.py`。以下路径不设防：
- **工具输出 → ReAct 观察**：仅 800 字符截断（`tick.py:603`），无注入检测
- **记忆/人物记忆 → 决策 prompt**：person_memory 内容注入镇内决策（`tick.py:588`）

虽当前 `knowledge.query_kb` 查的是本地知识库（非外部内容），风险有限，但随着工具集扩展（如接入联网搜索），这将成为真实攻击面。

**问题 D3（低）：WebSocket 鉴权在应用层**

WS 不走 `AuthMiddleware`（`middleware.py:164-167` 对非 http scope 直接透传），鉴权在端点内完成。虽已实现，但与 HTTP 路径的鉴权逻辑分离，易产生不一致。

### 4.6 数据持久化：结构化 + 向量检索

#### 4.6.1 Schema 概览

17 张表（`db/models/__init__.py:21-37`），21 个迁移（最新 `0021_embedding_dim_sync.py`）。

**分区设计**：

| 表 | 策略 | 键 | 评价 |
|---|---|---|---|
| `action_records` | RANGE 月分区 | `timestamp` | ✅ 保留 12 月，可 DROP PARTITION |
| `character_state_history` | RANGE 月分区 | `recorded_at` | ✅ 保留 6 月 |
| `memory_episodes` | **HASH 16 分区** | `character_id` | ⚠️ 无法按时间 drop |

**向量检索**：

- `halfvec(2048)` 列（`config.py:50`）→ 半精度节省一半存储，正确选择
- **HNSW + `halfvec_cosine_ops`** 索引（`0005:36-37`、`0015:36-37`）
- 查询时 `SET LOCAL hnsw.ef_search=100`（`memory_repo.py:482`）——精度/性能可调
- 30 天 `REINDEX INDEX CONCURRENTLY` 治理（`loops.py:377-404`），AUTOCOMMIT 连接（避免长事务）
- 分区表 autovacuum 参数收紧至 0.05/0.02（`memory_episode.py:15-17`）

**评价**：向量检索栈的设计是**专业级**的。halfvec、HNSW、ef_search 调优、并发重建索引、autovacuum 调参——这些是真正跑过大规模向量数据才会做的配置。

#### 4.6.2 持久化问题

**问题 DB1（中）：ORM 与物理 schema 漂移**

HNSW 索引**仅存在于迁移 DDL**（`0005:36-37`、`0015:36-37`），ORM `__table_args__` 未声明（`memory_episode.py:91-119`、`reflection.py:54`）。后果：若通过 `Base.metadata.create_all()` 建库（如测试环境或某些部署路径），将丢失向量索引，查询退化为全表扫描且**无任何报错**。

**问题 DB2（中）：`memory_episodes` 分区策略与保留需求不匹配**

HASH 分区按 `character_id` 散列，其优化目标是"单角色查询"，但运维需求是"按时间清理"。二者直接冲突，导致只能逐行 DELETE，而逐行清理的吞吐又被限制在 300 条/日 —— 与 §4.2.6 的吞吐缺口叠加，构成复合风险。

> 建议：改为 **RANGE(created_at) 月分区 + character_id 上的本地索引**。单角色查询仍走 `character_id` 索引（分区裁剪后各分区内索引即可），同时获得按时间 DROP PARTITION 的能力。

**问题 DB3（低）：`importance >= 7` 永久保留**

无上限的永久保留（`config.py:133`）。若 LLM 重要性评分系统性偏高，永久保留集会持续膨胀。建议增加容量上限或"永久记忆也参与压缩但保更高保真度"。

**问题 DB4（低）：`relations` 表无二级索引**

仅复合主键（`relation.py:34-35`）。关系图查询若按强度排序或按时间过滤将全表扫描。

**问题 DB5（中）：删除作业的工程化质量高，但覆盖面不足**

`_pk_batched_delete` 用主键子查询分批（非 ctid）、每批 5000 行、循环至删空（`loops.py:616-656`）——这是正确处理大表删除的方式。但如 §4.2.6 所述，吞吐配置不匹配。

### 4.7 可观测性

#### 4.7.1 覆盖现状

| 层 | 实现 | 覆盖度 |
|---|---|---|
| 分布式追踪 | OTel，12 处 `@trace_span`（tick/perceive/decide/tool.call/action.execute/memory.write/world.tick/llm.generate/embedding.batch/message.process/message.push） | ✅ 主链路全覆盖 |
| 采样 | OTel Collector **tail_sampling**：错误/慢链路必采（`docker-compose.yml:115-117`） | ✅ 优于头部采样 |
| LLM 专用追踪 | Langfuse：tick trace、LLM call、error（`langfuse_tracing.py:73,94,112,193,254`） | ✅ |
| 指标 | 36 个 Prometheus Counter/Histogram/Gauge（`metrics.py`） | ✅ |
| 日志 | structlog 结构化 + 统一脱敏（`sanitizer.py:15,34`） | ✅ |
| 可视化 | Grafana + Jaeger + Loki + Alloy + Alertmanager（16 个 compose 服务） | ✅ |

#### 4.7.2 亮点

- **tail sampling 而非 head sampling**（`docker-compose.yml:115-117`）：错误与慢链路必采，正常链路降采样。这是对可观测性成本与有效性权衡的正确判断。
- **日志脱敏单一真相源**：`sanitizer.py` 的 `SENSITIVE_KEY_PATTERNS` 被 structlog 处理器与手动脱敏共用（`logging.py:85`），避免脱敏规则分散。
- **Langfuse 与 OTel trace_id 打通**：`_otel_trace_id()`（`langfuse_tracing.py:55`）使 LLM 调用可回溯到完整链路。

#### 4.7.3 可观测性问题

**问题 O1（中）：认知质量无在线指标**

有性能与成本指标，但缺少**质量维度**的可观测性：
- 反思产出的主题数/空 grounding 比例
- 记忆检索的命中分布（相似度分位数）
- 决策的候选利用率、ReAct 轮次分布
- 计划的完成率

这些指标缺失，使得"认知机制是否在正常工作"无法被观测——只能看系统是否活着，不能看智能体是否"聪明"。

> `git log` 显示已引入 `ef676be test(memory): add offline retrieval quality evaluation pipeline`，说明团队已在关注。建议将其产出接入 Grafana。

**问题 O2（低）：无 SLO 与告警规则审计**

Alertmanager 已编排（`docker-compose.yml:225`），但未审阅告警规则是否覆盖关键路径（如 tick 延迟 P99、embed 失败率、记忆积压深度）。

### 4.8 安全与权限

#### 4.8.1 做对的部分

- **入站提示注入防护**：两条通道（Web + QQ）的用户消息**都**经过 `PromptGuard`——QQ 侧经 `MessageService.handle_user_message`（`onebot.py:1079` → `messaging/service.py:356,377`），Web 侧同源。26 条正则规则含中文角色覆盖（`prompt_guard.py:30-62`）。
- **工具状态参数防伪造**：`injected_params` 机制（`registry.py:473-483`）。
- **启动 fail-fast 校验**：弱凭据（`startup_checks.py:56-71`）、CORS（`:74-93`）、OneBot token（`:32-53`）、embedding 维度（`:105-140`）。
- **公开端点已收敛**：`P0-8` 移除 messages/conversations/admin 前缀、`R4-H2` 移除 characters/memories 前缀（`middleware.py:129-132`），并配 IP 限流（`:148-161`）。权限收窄的演进轨迹清晰。

#### 4.8.2 安全问题

**问题 S1（高）：RBAC 已声明但未接线**

`scopes` 字段在 `api_keys.py:50` 被写入，**全库零处读取**（已核实 `grep -rn "scopes" src/ --include=*.py`，除 `middleware.py` 外仅出现在 `api_keys.py` 的写入与文档字符串中）。

这意味着：
- 权限模型形同虚设，所有通过鉴权的用户拥有**完全相同的权限**
- `auth_dependency` 只返回 `{"user_id", "auth_method"}`（`middleware.py:66,78`），不携带任何权限信息

**问题 S2（高）：静态 API Key 等同超级管理员**

静态 Key 返回 `user_id="static"`, `scopes=[]`（`middleware.py:103-108`）。由于 scopes 无人校验，持有静态 Key 者可访问包括 `/api/v1/admin/*`（17 个端点）在内的全部接口。若静态 Key 用于前端或运维便利共享，风险显著。

**问题 S3（高）：中间件不校验资源归属**

`AuthMiddleware.__call__`（`middleware.py:210-214`）只判 `authenticated` 布尔值，**不校验 user_id 与资源归属**，也不向请求注入身份。鉴权与授权完全脱节。

> 缓解：`api/memory.py:137` 等端点在业务层做了归属校验。但这是**逐端点人工保证**，而非框架级保证——只要有一个端点漏写就产生越权。62 个端点的人工保证不可靠。

**问题 S4（中）：预算非原子 + 粒度仅全局**

```python
# client.py:987  → 检查（读）
# client.py:1013 → 记账（写）
```

两步非原子，`check_and_record` 原子版本（`budget_manager.py:189`）**全库零调用点**（已核实）。10 个并发角色可同时通过检查 → 预算被击穿。

且预算粒度仅 `llm:cost:{date}` 全局 + UTC 日（`budget_manager.py:32,102`），**无按角色/用户配额**。QQ 是公开入口——单用户高频对话即可耗尽全局日预算（$10，`config.py:122`），导致整个小镇停摆。这是**可用性风险**，不只是成本风险。

**问题 S5（中）：熔断与 fallback 语义冲突**

熔断器用全局单 key（`circuit_breaker.py:26`），不按 LLM 源隔离。而 `fallback.py` 的设计是多源轮换——多源全部失败只记 1 次 failure（`client.py:1031→1015`），单源持续失败又会拖累全局熔断。两者语义未对齐。

**问题 S6（中）：降级策略是"抛异常"**

超预算时 `essential=True` 的请求放行（`client.py:988-995`），其余抛异常中断 Tick。**没有规则式降级路径**（如回退到模板回复、缓存回复、或跳过本轮 Tick 保持状态不变）。熔断/超预算时智能体会直接"卡死"而非优雅退化。

### 4.9 前端工程化

#### 4.9.1 技术栈与质量

| 项 | 选择 | 评价 |
|---|---|---|
| 框架 | React 19 + React Compiler（`vite.config.ts:33-36`） | ✅ 前沿 |
| 路由 | TanStack Router + `autoCodeSplitting: true`（`vite.config.ts:22-25`） | ✅ 自动代码分割 |
| 数据 | TanStack Query v5，47 个 query hooks | ✅ |
| 状态 | Zustand | ✅ 轻量得当 |
| 样式 | Tailwind CSS v4 | ✅ |
| 图表 | Recharts | ✅ |
| 类型 | TypeScript 7.0.2 | ✅ |
| 规范 | oxlint + oxfmt | ✅ |

**类型安全优秀**：全库仅 **1 处** `any` 使用（已排除生成文件）。

**契约自动化完备**（这是最值得肯定的部分）：

```
后端路由 → scripts/export_openapi.py → openapi.json
                                          ↓ (CI 守卫：git diff --exit-code)
前端 pnpm gen:api → src/types/api-generated.d.ts
                                          ↓ (CI 守卫：git diff --exit-code)
```

`ci.yml:78-85` 校验 openapi.json 与路由同步，`ci.yml:113-118` 校验前端类型与 openapi.json 同步。**双向契约守卫**确保了前后端不可能静默漂移。这是许多成熟团队都做不到的。

#### 4.9.2 前端问题

**问题 F1（中）：测试覆盖严重不足**

仅 5 个测试文件（`AnimeBackground.test.tsx`、`ErrorBoundary.test.tsx`、`ui.test.tsx`、`queries.test.ts`、`auth.test.ts`），**33 个路由页面零测试**。测试集中在 UI 原子组件与工具函数，业务逻辑与页面交互无覆盖。

**问题 F2（中）：33 个平铺路由，信息架构缺分组**

路由清单：`index / world / map / characters / characters.index / characters.$id / character-card / chat.$id / conversations / memories / reflections / diaries / actions / plans / relationships / person-memory / shares / notifications / events / snapshots / state-charts / vector-search / metrics / monitoring / cost / qq-monitor / admin / settings / login / import / export / compare`

这是**扁平的 33 项导航**。缺少按使用者角色分组（观察者 / 运营者 / 管理员），新用户的认知负担重。建议按「世界」「角色」「记忆」「运维」「管理」分组折叠。

**问题 F3（低）：无国际化与主题基础设施**

`api-generated.d.ts` 与页面文案均为中文硬编码。若定位为开源项目，这会限制采用范围。

**问题 F4（低）：CI 有行数守卫但无复杂度守卫**

`ci.yml:106-109` 的 `check-loc.mjs` 限制单文件行数（P3-5）。这是防"文件膨胀"的有效手段，但只限制了行数，未限制圈复杂度或组件职责数。

### 4.10 Docker 部署与 DevOps

#### 4.10.1 编排现状

16 个服务，按 profile 分层：

| 类别 | 服务 | profile |
|---|---|---|
| 核心 | postgres、redis、backend、frontend | 默认 |
| 备份 | db-backup、redis-backup | `backup` |
| 可观测 | prometheus、alertmanager、loki、jaeger、otel-collector、alloy、grafana、langfuse-db、langfuse-web、langfuse-worker | `observability` |

**正面**：
- 全部服务设 `mem_limit`（0.5-2g），backend 2g（`docker-compose.yml:107`）
- `restart: unless-stopped` 全覆盖
- postgres / redis / langfuse-db 有 healthcheck，且 redis healthcheck 带认证（`:81` 注释说明 `requirepass` 后必须带认证否则误报 unhealthy——细节到位）
- backend `depends_on` 用 `condition: service_healthy`（`docker-compose.yml:121-125`）
- 端口均绑定 `127.0.0.1`（`:129,152`），不直接暴露公网
- 配置用 `${VAR:?msg}` 强制插值校验（`:113,114`），缺失即 fail-fast
- 日志驱动 `json-file` 带全局配置（`:23-24`）
- **备份服务独立成 profile**（db-backup、redis-backup），考虑周到

#### 4.10.2 部署问题

**问题 OPS1（中）：backend 容器无 healthcheck**

postgres、redis、langfuse-db 有 healthcheck，但 **backend 没有**（已核实 `docker-compose.yml:97-137`）。后果：
- `depends_on: backend`（frontend，`:147-148`）只等容器启动，不等服务就绪
- 编排层无法感知 backend 半死状态（如依赖未就绪、迁移卡住）

**问题 OPS2（中）：多副本迁移竞态**

`RUN_MIGRATIONS: "1"` 硬编码（`docker-compose.yml:119`），注释说明"多副本部署时附加实例设 `RUN_MIGRATIONS=0`"。这是**人工约定而非机制保证**——若运维疏忽，多副本同时迁移会冲突。建议改为分布式锁 + leader 选举，或拆为独立 init job / Kubernetes Job。

**问题 OPS3（中）：无 TLS 与反向代理**

compose 只绑回环端口，生产需经网关。但仓库未提供 nginx/Traefik 配置示例或部署文档说明，`docs/deployment.md` 与 `docs/docker-deployment.md` 需确认是否覆盖。

**问题 OPS4（低）：`mem_limit` 用旧版字段**

`mem_limit` 是 Compose v2 的旧字段，新版推荐 `deploy.resources.limits`。功能等价但建议迁移。

**问题 OPS5（低）：CI 冒烟未覆盖 observability profile**

`ci.yml` 的 deploy-smoke 只启动 `postgres redis backend frontend`，可观测性栈（Grafana/Langfuse 等）虽做了 config 插值验证（`ci.yml:130`），但**未实际启动验证**。Langfuse 侧三个密钥的必填性已被发现并处理（注释说明），说明存在过配置陷阱。

#### 4.10.3 CI 质量（优秀）

`ci.yml` 四道闸门：
1. **后端**：ruff check + ruff format --check + mypy strict（须 0 错误）+ pytest（含集成测试，PG/Redis service 容器）
2. **前后端契约**：openapi.json 守卫 + 前端类型守卫
3. **前端**：oxlint + 行数守卫 + tsc --noEmit + vitest + build
4. **部署冒烟**：`compose config -q` → build images → boot core stack → 后端健康检查 → nginx 代理健康检查 → teardown

**评价**：这是**超出同类项目平均水准**的 CI。特别是部署冒烟（`ci.yml:120-162`），其注释明确记录了它要防的两类事故（镜像不可构建、nginx 运行时 502）——说明 CI 是**从真实事故中演进出来的**，而非照抄模板。

### 4.11 文档与代码一致性

**问题 DOC1（中）：`docs/memory-system.md` 显著过期**

| 文档陈述 | 实际实现 | 证据 |
|---|---|---|
| 「时间阈值/事件触发反思 ❌ 未实现」(`:157-158`) | 已实现 | `reflection_service.py:48` |
| 「每日定时规划 ❌ 未实现」(`:204`) | 已实现 | `loops.py:291` |
| reflections 有 `summary`/`detail`/`source_memory_ids` (`:60-63`) | 实为 `content`/`tier`/`importance`/`source_reflection_ids` | `models/reflection.py:39-51` |
| plans 有 `horizon`/`steps`/`due_at` (`:72-75`) | 实为 `type`/`progress`/`deadline` | `models/plan.py:43-48` |
| `source_type` 为 `action/conversation/reflection/event` (`:49`) | 实为 `action/gossip/archive` | `memory_episode.py:89` |

文档滞后于实现是快速迭代的常态，但**关键认知机制文档与实际不符**会误导后续开发者与评审者。建议纳入 CI 或定期审计。

---

## 五、长期运行风险

### 5.1 风险矩阵

| 风险 | 触发条件 | 影响 | 现有缓解 | 残余风险 | 等级 |
|---|---|---|---|---|---|
| **记忆存储崩塌** | 连续运行 ≥1 月 | 磁盘耗尽、HNSW 索引膨胀、查询退化 | 分级保留 + 压缩归档（正确但吞吐不足） | **约 230 倍缺口** | 🔴 极高 |
| **embed 挂起拖垮全局** | 上游 embedding 服务抖动 | 角色假死、并发槽耗尽、全小镇停摆 | 无 | **无超时保护** | 🔴 极高 |
| **跨实例双写** | 锁 TTL 过期未续租成功 | 状态错乱、记忆重复 | 看门狗 + 6 处闸口（协作式） | 最长约 10 秒窗口 | 🟠 高 |
| **预算击穿/停摆** | 并发高峰或单用户刷量 | 全局 LLM 不可用 | 日预算 $10 + 熔断 | 非原子 + 无按用户配额 | 🟠 高 |
| **Prompt 单调膨胀** | 长期运行 | 成本上升、注意力稀释 | 条数/字符数硬截断 | 无 token 预算 | 🟡 中 |
| **永久记忆无界** | 评分系统性偏高 | 存储持续增长 | 无 | 无上限 | 🟡 中 |
| **rec_ver 键泄漏** | 角色频繁增删 | Redis 内存缓慢增长 | 仅删角色时清理 | 无 TTL | 🟢 低 |
| **后台循环无通用退避** | 依赖服务持续故障 | 日志风暴、无效重试 | `except Exception` 续跑（防崩溃） | 无退避增量 | 🟡 中 |

### 5.2 并发冲突：深度分析

项目的并发保护呈现**明显的能力分层**：

```
WorldEngine       防护式（纪元 CAS）           ← 强
Tick 主锁         协作式（看门狗轮询，10s）     ← 中
跨角色资源锁      协作式 + 只记日志（无信号）   ← 弱
后台视频任务      完全无保护                    ← 无
```

同一代码库内存在四种强度，说明**并发策略未统一抽象**。建议将 WorldEngine 的纪元 CAS 模式提炼为通用原语，统一应用到 Tick 锁与跨角色锁。

**正面**：`test_lock_loss_abort.py`（300 行）与 `test_tick_concurrency.py` 说明团队对并发有持续的测试投入，`reconcile.py:152-331` 的版本感知仲裁也不是事后补丁而是正向设计。**基础是好的，缺的是统一与收口。**

### 5.3 记忆膨胀：量化推演

**前提**（均取自代码配置）：
- 角色数：24（`configs/characters/`）
- Tick 间隔：30 秒（`config.py:228`）
- 每 Tick 写 1 条 episode（`tick.py:1332`）
- 保留周期：24 小时（`loops.py:702`，硬编码）
- 单周期处理上限：300 条（`config.py:139`）
- 向量维度：halfvec(2048) = 4KB

**推演**：

| 周期 | 生成 | 可清理 | 净增 | 累计 |
|---|---|---|---|---|
| 1 日 | 69,120 | 300 | +68,820 | 68,820 |
| 1 月 | 2,073,600 | 9,000 | +2,064,600 | ~207 万 |
| 1 年 | 24,883,200 | 109,500 | +24,773,700 | ~2,477 万 |

**存储估算**（5KB/行，含 HNSW 索引约 1.7 倍）：
- 1 年：**约 210 GB**

这还**未计入** gossip、工具记忆、社交记忆的额外写入，以及 `importance >= 7` 的永久保留集。

**结论**：在 24 角色 × 30 秒节拍的默认配置下，系统**不具备长期连续运行的存储可持续性**。即使把 `interval` 改为每小时（7200/日），缺口仍有约 10 倍。

> 注意：这是**配置与容量规划问题，不是设计缺陷**。机制（分级保留、压缩归档、批删、索引重建）都正确且完整，只是参数与分区策略未匹配实际生成速率。修复方案见 §9.1。

---

## 六、用户体验评估

### 6.1 面向开发/运维用户

**优点**：
- **33 个页面的 Dashboard 覆盖面极广**：世界状态、地图、角色卡、记忆流、反思、日记、计划、关系图、事件、快照、状态图表、向量搜索、指标、监控、成本、QQ 监控、设置、导入导出、对比——**可观测性直达业务语义层**，不只是技术指标。
- **导入导出 + 对比页面**：支持角色/世界数据的迁移与版本对比，运维友好。
- **WebSocket 实时推送 + 30 秒轮询兜底**：断连自愈，体验连贯。

**待改进**：
- 33 项扁平导航认知负担重（问题 F2）
- 无引导式 onboarding，新用户面对 24 个角色 × 33 个页面易迷失

### 6.2 面向终端用户（与角色交互）

这是项目定位的**核心价值面**，也是当前**相对最薄弱**的一环。

**优点**：
- 双通道触达（Web + QQ），QQ 侧支持群聊 @ 与私聊，降低了交互门槛
- 用户专属记忆（person_memory）让角色能"记住你"，这是差异化价值
- 主动分享（`proactive_sharing.py:678`）让角色主动发起互动，不止于被动应答

**待改进**：

| 问题 | 影响 | 位置 |
|---|---|---|
| **无流式输出** | 长回复需等待 10+ 秒无反馈 | `src/llm/` 无 astream |
| **交互形态单一** | `chat_with` 固定 2 轮，无持续话题、无拒绝/沉默 | `config.py:265` |
| **回复延迟高** | 单次对话最多 4 次 LLM 往返 + 质量评估 = 5 次串行 | `social.py:230-277` |
| **用户记忆召回无语义** | 二元组重叠匹配，用户会感觉"角色记错了/没记住" | `person_memory_service.py:273-286` |
| **无输出过滤** | 角色可能输出不当内容或泄露内部字段 | 全输出路径 |

**最关键的一点**：用户记忆的召回质量直接决定"角色是否记得我"这一核心体验。当前用字符二元组重叠（无语义）召回，而记忆流主体却用 pgvector——**在用户能最直接感知的环节用了最弱的技术**，这是一个优先级错配。建议优先改为向量检索。

### 6.3 体验层面的总体判断

**运维体验优秀，交互体验中等。** Dashboard 的深度令人印象深刻，但真正"使用产品"（与角色聊天）的体验还有明显提升空间，且改进成本不高（流式 + 向量召回 + 输出过滤三项即可显著改善）。

---

## 七、技术选型评估

### 7.1 选型清单与判断

| 领域 | 选型 | 判断 | 理由 |
|---|---|---|---|
| 语言 | Python 3.13 | ✅ | 异步生态成熟，AI 库首选 |
| Web 框架 | FastAPI | ✅ | 异步原生、OpenAPI 自动生成（支撑了契约守卫） |
| ORM | SQLAlchemy 2.0 + Alembic | ✅ | 21 个迁移，演进可追溯 |
| 实时状态 | Redis | ✅ | 语义正确（实时状态真相源） |
| 持久化 | PostgreSQL + pgvector | ✅ | 结构化 + 向量统一，避免双库一致性问题 |
| 向量类型 | `halfvec(2048)` | ✅ | 半精度省 50% 存储，专业选择 |
| 向量索引 | HNSW + `halfvec_cosine_ops` | ✅ | 查询性能与召回率平衡 |
| LLM 编排 | LangChain + 原生 OpenAI SDK | ⚠️ | **双栈并存**（问题 L1） |
| 结构化输出 | LangChain structured output | ✅ | 比文本解析可靠 |
| 配置 | pydantic-settings | ✅ | 类型安全 + fail-fast |
| 日志 | structlog | ✅ | 结构化 + 统一脱敏 |
| 追踪 | OpenTelemetry + Jaeger + Langfuse | ✅ | 通用追踪 + LLM 专用追踪互补 |
| 指标 | Prometheus + Grafana | ✅ | 事实标准 |
| 包管理 | uv | ✅ | 快，锁定可靠 |
| 前端框架 | React 19 + TanStack Router | ✅ | 前沿且类型安全 |
| 前端构建 | Vite 8 + Rolldown + React Compiler | ✅ | 激进但合理 |
| 前端规范 | oxlint + oxfmt | ✅ | 比 ESLint 快一个量级 |
| 类型检查 | mypy --strict（146 文件 0 错误） | ✅ | **罕见的高标准** |
| 编排 | Docker Compose + profile | ✅ | 单机构署的合理选择 |

### 7.2 选型问题

**问题 TS1（中）：LangChain 的定位不清**

项目同时用 LangChain（`fallback.py:119` 的 `ChatOpenAI`）和原生 `AsyncOpenAI`（`client.py:115`）。若只把 LangChain 当"带超时重试的 OpenAI 封装"，则引入了 50+ 传递依赖却只用到很小一部分能力；若要用其 Agent/Chain 能力，则当前 ReAct 是手写循环（`tick.py:639-698`），并未使用。

**建议**：明确二选一。
- 方案 A：全面回归原生 SDK，自己管超时/重试/fallback（代码量不大，依赖大幅减少）
- 方案 B：全面拥抱 LangChain，ReAct 用其 Agent 抽象

当前"脚踏两条船"导致行为不一致（embed 无超时正源于此）。

**问题 TS2（低）：TypeScript 7.0.2 与 Vite 8 过于激进**

TypeScript 7（原生 Go 编译器）、Vite 8（Rolldown）、React Compiler 1.0 都是很新的版本。虽有性能红利，但生态兼容性风险与社区支持滞后。若定位为长期项目可接受；若希望被广泛采用需评估。

**问题 TS3（低）：`character_max_concurrent=10` 与 24 角色不匹配**

24 个角色、并发上限 10（`config.py:229`）。若单 Tick 耗时接近 30 秒节拍，会有角色持续排队。需实测单 Tick P50/P99 延迟后校准。

---

## 八、问题清单

### 8.1 按严重度汇总

| ID | 严重度 | 问题 | 位置 | 修复成本 |
|---|:---:|---|---|:---:|
| **记忆-01** | 🔴 P0 | 记忆清理吞吐 300/日 vs 生成 69,120/日（约 230 倍缺口） | `loops.py:702`、`config.py:139` | 中 |
| **LLM-01** | 🔴 P0 | `embed()` 无超时，最坏挂 30 分钟且占锁与信号量 | `client.py:115,123` | **极低** |
| **并发-01** | 🟠 P1 | Tick 锁无 fencing token，最长约 10 秒双写窗口 | `tick.py:232` | 中 |
| **并发-02** | 🟠 P1 | `_memorize`/gossip 绕过失锁闸口，违背 H10 不变量 | `tick.py:327,331` | **低** |
| **并发-03** | 🟠 P1 | 跨角色锁路径无失锁信号（只记日志） | `locks.py:167-177` | 低 |
| **成本-01** | 🟠 P1 | 预算检查与记账非原子，原子版本零调用 | `client.py:987,1013` | 低 |
| **成本-02** | 🟠 P1 | 预算仅全局+日粒度，无按角色/用户配额 | `budget_manager.py:32` | 中 |
| **安全-01** | 🟠 P1 | RBAC scopes 全库零读取，权限模型未接线 | `api_keys.py:50` | 中 |
| **安全-02** | 🟠 P1 | 静态 API Key 等同超管，可访问 17 个 admin 端点 | `middleware.py:103-108` | 中 |
| **安全-03** | 🟠 P1 | 中间件不校验资源归属，62 端点人工保证 | `middleware.py:210-214` | 中 |
| **LLM-02** | 🟠 P1 | `generate_video` 后台任务绕过 tool_timeout 与 lock_lost | `media.py:82` | 中 |
| **LLM-03** | 🟡 P2 | 无流式输出，交互体验差 | `src/llm/` | 中 |
| **LLM-04** | 🟡 P2 | 工具参数无类型/枚举/范围校验 | `registry.py:488-495` | 低 |
| **LLM-05** | 🟡 P2 | 双栈并存（LangChain + 原生 SDK），策略不一致 | `client.py` / `fallback.py` | 中 |
| **记忆-02** | 🟡 P2 | `memory_episodes` HASH 分区无法按时间 drop | `0002_optimize.py:116` | 高 |
| **记忆-03** | 🟡 P2 | importance 权重 0.05 vs sim 0.6，重要性近乎无效 | `memory_repo.py:44-48` | 低 |
| **记忆-04** | 🟡 P2 | 无 prompt token 预算控制 | `perception.py` | 中 |
| **记忆-05** | 🟡 P2 | 用户记忆召回用二元组重叠（无语义） | `person_memory_service.py:273-286` | 中 |
| **记忆-06** | 🟡 P2 | 无 embedding 缓存 | `perception.py:154` | 低 |
| **记忆-07** | 🟡 P2 | `importance>=7` 永久保留无上限 | `config.py:133` | 低 |
| **交互-01** | 🟡 P2 | 对方回复由发起方"代打"，削弱自主性 | `social.py:230-242` | 高 |
| **交互-02** | 🟡 P2 | 无跨 Tick 持续对话，固定 2 轮 | `config.py:265` | 中 |
| **交互-03** | 🟡 P2 | 无基于关系的主动寻访 | — | 中 |
| **安全-04** | 🟡 P2 | 工具输出/记忆二阶注入未防护 | `tick.py:603,588` | 中 |
| **安全-05** | 🟡 P2 | 输出侧零过滤 | 全输出路径 | 中 |
| **DB-01** | 🟡 P2 | HNSW 索引未在 ORM 声明，元数据漂移 | `memory_episode.py:91-119` | **低** |
| **DB-02** | 🟢 P3 | `relations` 表无二级索引 | `relation.py:34-35` | 低 |
| **DB-03** | 🟡 P2 | `rec_ver` 键无 TTL | `reconcile.py:319` | **低** |
| **OPS-01** | 🟡 P2 | backend 容器无 healthcheck | `docker-compose.yml:97-137` | **低** |
| **OPS-02** | 🟡 P2 | `RUN_MIGRATIONS=1` 硬编码，多副本竞态靠人工约定 | `docker-compose.yml:119` | 中 |
| **前端-01** | 🟡 P2 | 测试仅 5 文件，33 个路由页面零测试 | `packages/frontend/src` | 中 |
| **前端-02** | 🟡 P2 | 33 项扁平导航缺分组 | `src/routes/` | 低 |
| **架构-01** | 🟡 P2 | `tick.py` 1425 行 / `loops.py` 1076 行，职责过载 | — | 中 |
| **架构-02** | 🟡 P2 | API 层直连 Repository，Service 层沉淀不足 | `api/admin.py` 等 | 中 |
| **文档-01** | 🟡 P2 | `memory-system.md` 与实现显著漂移 | `docs/memory-system.md` | 低 |
| **规划-01** | 🟡 P2 | 计划幂等用标题字符串匹配 | `daily_plan_service.py:56-61` | 低 |
| **规划-02** | 🟡 P2 | 无独立 replan 任务，无递归分解 | `docs/memory-system.md:203` | 中 |

### 8.2 "低成本高收益"优先项

以下问题修复成本极低但收益显著，建议**立即处理**：

| 问题 | 修复 | 成本 |
|---|---|---|
| LLM-01 | `AsyncOpenAI(..., timeout=30.0, max_retries=2)` | 2 行 |
| 并发-02 | `_memorize(..., lock_lost=lock_lost)` + 闸口 | 5 行 |
| 成本-01 | 将 `check_budget()` + `record_usage()` 替换为 `check_and_record()` | 10 行 |
| DB-03 | `redis.set(key, val, ex=...)` 加 TTL | 1 行 |
| DB-01 | ORM `__table_args__` 补充 HNSW 索引声明 | 10 行 |
| OPS-01 | compose 补 backend healthcheck | 8 行 |
| 记忆-03 | 调整 importance 权重并用评测管线校准 | 1 行 + 评测 |

---

## 九、改进建议与路线图

### 9.1 P0：阻断长期运行崩溃（1-2 周）

#### 9.1.1 记忆吞吐治理

三层并进：

**① 参数与可配置化**
```python
# config.py
memory_retention_interval_seconds: int = 3600      # 从硬编码 24h 改为可配置
memory_compression_batch_limit: int = 5000         # 从 300 提升
```
并将 `loops.py:702` 的 `interval = 24 * 3600` 改为读配置。

**② 分区策略改造（根治）**

将 `memory_episodes` 从 HASH(character_id) 16 分区改为 **RANGE(created_at) 月分区 + (character_id, created_at) 本地索引**：

- 获得 `DROP PARTITION` 能力，过期数据 O(1) 清理，不产生 VACUUM 压力
- 单角色查询仍可分区裁剪 + 本地索引
- 迁移需重建表，建议在低峰期执行并预留双倍磁盘

**③ 源头减量（最有效）**

当前**每个 Tick 都写 1 条记忆**，这是生成速率过高的根本原因。建议引入**显著性门禁**：

```
只有满足以下之一才写入 episode：
  - importance >= 阈值（由 LLM 评分，当前 memory_llm_scoring 默认关闭）
  - 涉及状态显著变化（位置变更、关系变更、金额变更）
  - 涉及其他角色（社交事件）
  - 距上次同类记忆超过 N 个 Tick
```

`wait`、`relax`、`use_phone` 这类低信息量动作不应产生记忆。此举可将生成速率降低一个数量级，**比提升清理吞吐更根本**。

**④ 容量监控**
新增 Prometheus 指标：`memory_episodes_total`、`memory_pending_compression`、`memory_generation_rate_per_hour`，并在 Grafana 配面板 + 告警（净增速率持续 > 清理速率即告警）。

#### 9.1.2 embed 超时（立即）

```python
self.openai = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    timeout=30.0,        # 与 ChatOpenAI 对齐
    max_retries=2,
)
```
`_embedding_client` 同样处理。这是**性价比最高的一行改动**。

### 9.2 P1：并发与权限收口（2-4 周）

**① 统一并发原语**

将 `WorldEngine` 的纪元 CAS 模式（`engine.py:52-63`）提炼为通用 fencing 原语，应用到：
- Tick 主锁（消除 10 秒双写窗口）
- 跨角色资源锁

统一 `lock_watchdog` 与 `watch_locks` 为**单一实现**（带失锁信号），删除只记日志的变体。

**② 补齐失锁闸口**

`_memorize` 与 `_propagate_gossip` 传入 `lock_lost` 并在写 PG 前检查，使实现与 `tick.py:991-994` 的文档化承诺一致。补单元测试断言"失锁后 PG 写入数为 0"。

**③ 预算原子化 + 分层配额**

- 全局路径改用 `check_and_record()`（`budget_manager.py:189`）
- 增加 `llm:cost:{date}:{character_id}` 与 `llm:cost:{date}:user:{user_id}` 维度，按角色/用户设配额
- QQ 侧按 `qq_{user_id}` 单独限预算，防止单用户拖垮全局

**④ 权限模型接线**

- 定义 scope 常量（如 `admin:read`、`admin:write`、`memory:read_own`、`memory:read_all`）
- `auth_dependency` 返回 scopes，端点用 `Depends(require_scope("admin:write"))` 声明
- `AuthMiddleware` 向 `request.state` 注入 principal，并增加资源归属校验辅助函数
- 静态 Key 改为显式 `scopes=["*"]` 并在文档中标注其等同超管，或拆分为运维 Key 与只读 Key

### 9.3 P2：体验与能力增强（1-2 月）

**① 流式输出**（体验提升最大）

为 `chat` 增加 `astream` 路径，Web 与 QQ 均改为流式推送。QQ 侧可做分段发送。

**② 用户记忆改向量召回**

`person_memory_service.py:273-286` 的二元组重叠改为 pgvector 检索。这是"角色记得我"体验的直接决定因素。

**③ 输出侧过滤**

统一出站过滤中间件：敏感内容检测 + 内部字段剥离（action id、user_id、schema 名称）+ 长度控制。参考 AGENTS.md §4.3「LLM 不能暴露 Action/schema/字段名等工程概念」——该约束当前在入站侧有防护，出站侧未落实。

**④ Prompt token 预算**

引入全局 token 预算分配器，按优先级（当前情境 > 计划 > 反思 > 记忆 > 用户记忆 > 传闻）分配，超限截断低优先级层。

**⑤ 前端测试与导航改版**

- 为核心页面（chat、characters、memories、monitoring）补交互测试
- 33 项导航按「世界 / 角色 / 记忆 / 运维 / 管理」分组折叠

**⑥ 模块拆分**

- `tick.py`（1425 行）→ `TickOrchestrator` + 步骤策略对象
- `loops.py` 的 4 类保留任务 → `src/retention/` 独立模块

### 9.4 P3：架构演进（季度级）

**① 交互深度增强**

- 跨 Tick 持续对话：引入对话会话状态机，支持话题延续
- 基于关系的主动寻访：让角色因关系强度产生"去找某人"的移动意图
- 交互形态多样化：拒绝、沉默、打断、群内插话

**② 规划能力补齐**

- 计划幂等改用独立 `plan_date` 列 + 唯一约束
- 引入独立 replan 定时任务（每日 12:00 / 18:00 检视计划执行度并修订）
- 评估日计划 → 时段分解的二层递归（完整三层递归成本较高，建议先做二层）

**③ 文档与代码同步机制**

将关键文档断言纳入 CI 校验（如用测试断言 `docs/memory-system.md` 中提到的字段名实际存在），或建立文档随代码变更的 review checklist。

**④ 认知质量可观测**

将反思主题数、空 grounding 比例、检索相似度分布、计划完成率、ReAct 轮次分布接入 Prometheus + Grafana。已有离线评测管线（`ef676be`），可复用其指标定义。

---

## 十、总体评价

### 10.1 项目处于什么位置

ai_town 是一个**工程质量显著超过同类开源项目**的 LLM 多智能体系统。判断依据不是功能清单，而是那些"只有真正跑过生产才会做"的细节：

- `event_queue.py` 的 docstring 论证了 XACK 与 XDEL 的语义差异及为何必须 XDEL
- 时间衰减设 25% 下限，避免老记忆永久不可达
- 压缩失败整组跳过，**绝不未压缩先删**
- 向量化去重时同步置 `is_reflected=TRUE`，防止幻影计数
- Redis healthcheck 带认证，注释说明 `requirepass` 后不带认证会误报 unhealthy
- CI 的部署冒烟作业注释记录了它要防的两类真实事故
- 权限前缀从 `P0-8` 到 `R4-H2` 的持续收窄轨迹
- `reconcile` 用版本感知仲裁而非朴素 LWW

这些不是从教程抄来的，是**踩坑后沉淀的**。加上 775 个测试、mypy strict 零错误（146 文件）、双向 API 契约守卫、21 个迁移——**项目的基础设施成熟度已达到可长期演进的水平**。

### 10.2 核心矛盾

项目当前的**核心矛盾**是：

> **工程基础设施已按"长期生产运行"的标准建设，但容量规划与运行时保护的关键参数仍停留在"演示验证"的量级。**

具体表现：
- 建了完整的记忆治理机制（分级保留、压缩归档、批删、索引重建），但处理上限设为 300 条/日，而生成速率是 69,120 条/日
- 建了看门狗与 6 处失锁闸口，但第 5 步记忆沉淀绕过了它
- 建了原子预算 API，但生产路径用的是非原子的两步调用
- 建了 scopes 权限字段，但从未读取

**这不是设计能力的缺失，而是"最后一公里"的未完成。** 每一个问题的修复成本都远低于建设成本。

### 10.3 可信度评估

| 场景 | 评估 |
|---|---|
| **演示 / 短期运行（< 1 周）** | ✅ 完全可信，功能完整、体验良好 |
| **中期运行（1 周 - 1 月）** | ⚠️ 基本可信，需监控磁盘增长，可能遇到 embed 挂起 |
| **长期连续运行（> 1 月）** | ❌ 当前配置下不可行，须先完成 §9.1 的 P0 修复 |
| **多副本水平扩展** | ⚠️ 迁移竞态靠人工约定，锁无 fencing，需完成 §9.2 |
| **公开互联网部署** | ❌ 权限模型未接线、输出零过滤、预算无按用户配额，须先完成 §9.2 |

### 10.4 建议的最小可行上线清单

若要将项目投入长期运行，**建议至少完成以下 8 项**（预计 1-2 周）：

- [ ] **LLM-01**：`AsyncOpenAI` 补 `timeout=30.0`（2 行）
- [ ] **记忆-01a**：`memory_retention_interval_seconds` 可配置化 + 批次上限提升
- [ ] **记忆-01b**：引入记忆写入显著性门禁（源头减量一个数量级）
- [ ] **并发-02**：`_memorize` / gossip 补失锁闸口
- [ ] **成本-01**：预算改用 `check_and_record()`
- [ ] **成本-02**：增加按用户/角色预算配额
- [ ] **安全-01/02**：scopes 接线 + 静态 Key 权限收敛
- [ ] **OPS-01**：backend 补 healthcheck

完成这 8 项后，项目可达到"中期可信、长期可维护"的状态。

### 10.5 结语

用一句话概括本次审查的结论：

> **ai_town 已经把最难的部分（认知架构、并发一致性、可观测性、工程纪律）做对了，现在需要的是把最简单的部分（超时参数、批次上限、权限接线、原子调用）补齐。**

项目的地基是扎实的，架构是可演化的，团队展现的工程判断力是专业的。三个 P0 问题都是**参数与接线层面**的，不涉及架构重构。以团队此前从 `P0-8`、`R4-H2`、`round-3 H3`、`round-7 G1` 一路迭代的轨迹看，这些问题的修复是可预期的。

**综合评分：3.6 / 5 — 工程底座扎实，具备成为优秀长期项目的潜质，需补完"最后一公里"方能承载生产级长期运行。**

---

## 附录 A：审查覆盖的代码资产

| 类别 | 规模 |
|---|---|
| 后端源码 | `packages/backend/src/` 约 32,000 行（46,447 行含测试） |
| 后端测试 | 92 个文件 / 775 个测试用例（含 `tests/integration/`） |
| 前端源码 | `packages/frontend/src/` 16,690 行（含 4,285 行生成类型 + 722 行 routeTree） |
| 前端页面 | 33 个路由 |
| 数据库迁移 | 21 个（`0001_init` → `0021_embedding_dim_sync`） |
| 数据表 | 17 张 |
| 配置项 | `.env.example` 全量 |
| 角色卡 | 24 个 |
| 场景 | 12 个 |
| Prompt 模板 | 21 个 YAML |
| Action | 14 个 |
| 工具 | 18 个（5 个 state_mutating） |
| API 端点 | 62 个 REST + 1 Web WS + 1 OneBot WS |
| 编排服务 | 16 个（4 核心 / 2 备份 / 10 可观测） |
| 后台循环 | 9 个调度循环 + 4 类保留任务 |

## 附录 B：与历史评审的关系

仓库 `docs/` 下已有 12 份历史评审文档（`project-review-20260824.md`、`project-review-20260825-round4.md`、`project-review-20260826-round6.md`、`project-review-20260827-comprehensive.md`、`project-review-20260827-round7.md`、`review-2026-08-26-comprehensive.md` 等）。

本报告为**独立重新审查**，未以历史结论为前提，但关键发现与历史评审的修复轨迹一致（如 `round-3 H3` 的 XDEL 语义、`round-7 G1` 的结构化相遇、`P0-8`/`R4-H2` 的权限收窄），可交叉印证。

历史评审中已识别并修复的问题，本报告不再重复计入缺陷；本报告重点关注**尚未闭合**的项与**新增**的容量/并发/权限风险。

---

*报告完*
