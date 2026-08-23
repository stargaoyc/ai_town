# AI Town 项目全面审查报告

> 审查对象：stargaoyc/ai_town @ commit `dabecc9`（v0.2.0 之后，2026-08-24）
> 审查方式：全量源码审读（后端核心链路逐行、外围模块定向抽查）+ 配置/CI/编排文件核对 + 与项目自述文档交叉验证
> 本报告为独立第三方视角的工程审查，所有结论均附代码位置证据；未覆盖项见附录。

---

## 一、执行摘要

AI Town 定位为「LLM 驱动的多智能体虚拟小镇 + 长期陪伴智能体」，核心叙事是"角色在用户不在时也在生活"。经过对全部关键链路的审读，**总体评价：这是一个原型期工程质量显著高于平均水平的早期项目（v0.2.0），可靠性工程与数据层设计是亮点；但认知架构存在系统性断流——反思、Person Memory、日记三类认知产物均"只写不读"，未回流到决策与对话上下文；同时长期运行的记忆膨胀治理完全缺位**。

### 评分卡（1-5 分）

| 维度 | 评分 | 一句话结论 |
|------|:----:|------------|
| 项目定位 | **5.0** | 差异化清晰，"世界驱动陪伴"叙事在代码中真实落地 |
| 分层架构与模块边界 | 3.5 | 主干清晰，但 core→messaging 反向依赖、main.py 上帝模块 |
| 世界引擎与多智能体交互 | 3.0 | 引擎健壮，但角色间交互深度浅（单轮对话） |
| 认知机制完备性 | **2.5** | 各机制单独实现尚可，但闭环断裂（详见 §五） |
| ReAct 工具调用 | 4.0 | 设计克制、注入式参数安全，轮数上限合理 |
| 数据持久化设计 | 4.0 | 分区/HNSW/混合检索精细，缺 retention 治理 |
| 全链路可观测性 | 4.5 | 指标/告警/追踪/日志矩阵完整，实打实埋点 |
| 部署与工程化 | 4.0 | CI 含真实依赖集成测试；compose 有硬编码瑕疵 |
| 前端工程化与 UX | 3.5 | 30 页运维台覆盖全面，零测试 |
| 长期运行风险治理 | **2.0** | 并发防护到位，但记忆膨胀无任何对策 |
| **综合** | **3.6** | 骨架优秀、闭环待接、治理缺位 |

---

## 二、项目定位评估

**结论：定位成立且稀缺，是本项目最大的资产。**

- "不做随叫随到的 AI 助手，做有自己生活的'人'"不是宣传语：World Tick（`core/world/engine.py`）独立于用户消息推进虚拟时间/天气/场景拥挤度，Character Tick（`core/character/tick.py`）每 30 秒驱动角色自主决策，用户的每次对话确实锚定在角色经历上。
- 双端触达（Web Dashboard 运维台 + QQ OneBot 陪伴端）分工合理：Dashboard 是"上帝视角"，QQ 是"角色视角"，主动分享（`proactiveShareIntent` → `OneBotAdapter.push_share`，tick.py:1316-1389）打通了"角色主动找你"的关键体验。
- 对标 Generative Agents（斯坦福小镇）：本项目补齐了论文没有的产品化要素（多端触达、成本控制、可观测性），但认知内核（见 §五）反而比论文简化。

**风险提示**：当前"多智能体"交互深度不足以支撑"小镇社会"的想象——角色间只有单轮寒暄级交互（§四），长期看定位兑现取决于此处的深化。

---

## 三、分层架构与模块边界

### 做得好的

1. **runtime.py 服务定位器**（src/runtime.py）：以 `set_*/get_*` 全局容器消除业务模块对 main.py 的反向依赖，TYPE_CHECKING 隔离类型导入，干净务实。
2. **LLM 边界纪律严格**（这是全项目最值得肯定的设计）：
   - Action precondition 由代码过滤（`actions/base.py:34-66`），LLM 只能在候选中选择；
   - executor 返回状态字典而非直接写库（`apply_cost_fields`，base.py:100-121），写入由执行层单事务完成（tick.py:874-961）；
   - 工具的状态参数（money/inventory/relation_strength）由服务端注入（tools/registry.py:296-373），LLM 无法伪造资源。
3. **配置真相源统一**：12 个 Prompt 全部外置 `configs/prompts/*.yaml`，缺失即 fail-fast；compose 以只读卷挂载（docker-compose.yml:89）。

### 问题

1. **Core → Messaging 反向依赖**：`core/character/tick.py:45` 直接 `from src.messaging.proactive_sharing import ProactiveSharingService`，并经 runtime 取 `OneBotAdapter`。按项目自订分层（API→Service→Core→Infra），Core 层不应知晓消息服务。建议：分享意图作为事件发出，由 messaging 层订阅执行。
2. **main.py 上帝模块**（844 行）：350 行 lifespan 装配 + 3 个业务后台循环（`_character_tick_loop`/`_diary_scheduler_loop`/`_reconciliation_loop`，main.py:462-679）+ 内联 AuthMiddleware（main.py:703）。三个循环是纯业务逻辑，应下沉到 scheduler/core 包；middleware 应归 auth/。
3. **API 层直连 Repository**：api/world.py、messages.py、characters.py、memory.py、admin.py 共 5 处直接 import db models/repositories。FastAPI 语境下可接受，但与 AGENTS.md "禁止在 API 层写业务逻辑"的自订规则张力较大，admin.py 尤其需要收敛。
4. **循环依赖已用延迟 import 规避**：tick.py:1261 残留注释、registry.py:172-176 `_get_redis()` 函数内导入——说明模块图存在环，靠约定而非结构解决。

---

## 四、世界引擎与多智能体交互

### World Engine（core/world/engine.py）——健壮

- Redis 锁选主（Leader Election）+ compare-and-expire 安全续租（engine.py:150-194），支持多副本部署时单实例推进世界；
- 演化器管线按依赖排序（时间→天气→场景→资源→事件），单个演化器失败不中断 Tick（engine.py:261-269）；
- 差分事件持久化带内存去重基线（engine.py:369-462），每 1000 Tick 全量快照保证冷启动恢复时间恒定——设计意识好。

### Character Tick 并发模型——成熟

- 分布式锁（唯一 token + Lua CAS 释放 + 看门狗续租防 LLM 长调用过期易主，tick.py:102-131）；
- Semaphore 并发上限支持热更新（tick.py:133-146）；
- 批次级 429 限流退避（指数倍增至 10×，按异常类型判定而非字符串匹配，main.py:448-459, 514-539）；
- 熔断开启时整批跳过（tick.py:121-123）。

### 多智能体交互——机制存在但深度不足

**已有的真实机制**（值得肯定，很多同类项目根本没有）：
1. 同场景感知注入决策 Prompt：附近角色的名字/性格/关系强度/情绪/当前动作（tick.py:330-385, 451-465）；
2. `chat_with` Action：同场景校验 → 跨角色资源锁（防 A→B/B→A 竞争，locks.py `acquire_resource_locks`）→ 单次 LLM 生成双方各一句对话 → 双向关系更新（陌生人破冰 +2 / 其他 +5）→ 为双方各写一条第一人称记忆（tick.py:963-1165）；
3. 关系图谱模块（modules/relation/graph.py）维护 strength/relationship_type；
4. 场景在场人数计数（Redis `world:scene:visitors`，tick.py:942-948）支撑拥挤度演化。

**深度缺陷**：
1. **对话是一次性的**：一次 LLM 调用生成"A 说一句 + B 回一句"即结束（tick.py:1061-1085），没有多轮往返、没有话题延续、没有发起方对回应的反应。两个角色的"友谊"由 +5 数值累积，而非对话质量驱动。
2. **对话内容不进入对方的实时上下文**：B 的记忆要等下次 Tick 向量检索命中才可能被想起，交互的即时性丢失。
3. **无群体动力学**：没有三人以上共同活动、没有传闻传播（A 的事件通过对话影响 B 的认知）、没有关系三角。`related_characters` 字段（memory_episode.py:62）已为此预留，但无消费方。
4. nearby_characters 查询存在 N+1：注释写"批量读取，避免 N+1"（tick.py:343），实际循环内每个角色开独立 session 查关系（tick.py:345-358）——注释与实现相反。

---

## 五、认知机制完备性（本报告最重要章节）

### 5.1 总体诊断：三条断流

认知产物的生产端全部实现，消费端系统性缺失：

| 认知产物 | 生产（写入） | 消费（回流决策/对话） | 判定 |
|----------|------------|---------------------|------|
| 记忆流检索 | ✅ 每 Tick 混合检索 top10 注入决策 | ✅ 仅 Tick 决策链路 | 通 |
| 反思 Reflection | ✅ 阈值 20 条触发，LLM 归纳落表（reflection_service.py） | ❌ tick.py 仅在 `_memorize` 末尾调用 `check_and_reflect`（tick.py:1235），`_perceive`/decision prompt 均无 reflections；检索 SQL 只查 memory_episodes | **断流** |
| Person Memory | ✅ 每次用户交互后异步更新（messaging/service.py:427-449） | ❌ `get_relevant_context()`（person_memory_service.py:180）**全库零调用**；chat.yaml 无对应占位符 | **断流** |
| 日记 Diary | ✅ 四周期幂等生成（main.py:557-645, diary_service.py） | ❌ 无任何 prompt 组装读取日记 | **断流** |

**后果**：README 宣称的「让陪伴更'懂你'」「体现'我记得你'」目前只在数据库和前端页面里成立，不在模型的上下文窗口里成立。用户对话链路（chat.yaml:29-64）的实际上下文 = 角色档案 + 世界状态 + 会话摘要 + 会话历史，**没有任何跨会话长期认知注入**。这是"长期陪伴"定位的最大缺口。

### 5.2 记忆流：检索可用但质量受限

- 混合检索公式 `sim*0.6 + importance*0.05 - 天数*0.05`（memory_repo.py:287-302）：向量召回候选（top_k×2）后重排，HNSW ef_search=100，分区裁剪下 <10ms——工程实现优秀。
- **线性时间衰减过重**：正分上限 1.1（0.6+0.5），每天扣 0.05 → **22 天前的记忆 final_score 必为负**，在重排中永远输给近期记忆。Generative Agents 用指数衰减（乘法），本项目用线性减法且系数过大，"长期记忆"名存实亡。
- **检索 query 过弱**：固定模板 `"角色{X}当前在{Y}，最近在做什么"`（tick.py:302），不含当前计划、情绪、时间段的语义信号，向量检索的区分度主要靠 query 里那点信息——基本退化为"该角色最近的记忆"。
- **无去重**：同一行为重复执行会产生近似重复 episode（无 content hash / 相似度去重），加剧膨胀并稀释检索结果。

### 5.3 反思：触发机制对，消化机制错

阈值触发（20 条未反思）符合论文精神；但每次只取最近 20 条（reflection_service.py:75），反思粒度=恰好一批，无法形成跨期主题归纳；产出为纯文本拼接（reflection_service.py:112），无结构化 tag/置信度；最致命的是 §5.1 所述不回流。

### 5.4 规划系统：两层扁平，与作息脱节

Plan 只有 long_term/short_term 两型（plan.py:21-31），无日/小时计划层级；进度百分比由 LLM 决策附带 `planChanges` 更新，但该字段**解析后被丢弃**：`_decide` 解析 plan_changes 存入 DecisionResult（tick.py:536-547）后，全库无任何消费路径——`PlanRepository.update_plan`（plan_repo.py:50）仅被 api/characters.py 的用户侧 CRUD 调用。即 LLM 的计划变更建议从未落库，决策 schema 中的 `planChanges` 是死功能；真正的日程感来自独立的 ScheduleSystem（modules/schedule），与 Plan 表互不相通。"计划影响 precondition"的注释（plan.py:5）未找到实现。

### 5.5 Person Memory：单槽位覆盖式更新的漂移风险

- 每角色对每用户仅一行（unique index，person_memory.py:64），每次交互让 LLM 基于"旧内容+本轮对话"**全文重写**（person_memory_service.py:99-113）——典型 telephone game：早期细节会被逐步稀释遗忘，且不可追溯。
- `preferences` JSONB 结构化字段（person_memory.py:49）**从未被写入**；heat 只增不减，注释承诺的"后台衰减任务"不存在（全库 grep 证实）。
- 实现用裸 `text()` SQL 绕过 ORM 模型，与 repository 规范不一致，且 read-upsert 无并发保护（唯一索引兜底会抛异常被吞）。

### 5.6 ReAct 工具调用：克制且安全（4.0 分）

- 16 个工具 / 5 命名空间（shop/knowledge/social/world/self_info），进程内 async 直调替代 MCP，消除网络开销（registry.py:1-14）；
- 状态变更类工具参数服务端注入，LLM 无法越权伪造 money/inventory/关系强度——安全设计正确；
- 轮数上限 3，超限强制降级 wait（tick.py:174-204）；工具结果自动沉淀为记忆（importance=7，tick.py:604-625）；
- 工具开关持久化 Redis hash，前端可热切换，Redis 故障时 fail-open 全启用（registry.py:179-205）。

小问题：`get_enabled_tools()` 每次 `hgetall` 无缓存，单次 Tick（含最多 4 轮决策+工具调用）产生 5-8 次 Redis 往返；ToolRegistry 在 `_decide`/`_execute_tool` 内反复实例化，实例字段 `_current_character_id` 是死代码。

---

## 六、数据层设计（4.0 分）

### Schema 全景（14 张表）

UUIDv7 主键（时间有序）、JSONB 灵活字段、外键 CASCADE 清理，基础素养高：

| 表 | 分区 | 关键索引 |
|----|------|---------|
| memory_episodes | **HASH(character_id) ×16**（0002） | HNSW(halfvec)；部分索引 unreflected / unmaterialized(含熔断过滤) |
| action_records | RANGE(timestamp)（0001） | 月度分区预创建 |
| character_state_history | RANGE(recorded_at)（0007） | 同上 |
| messages | RANGE（0003） | keyset 双游标分页 `(created_at,id)` |
| person_memories / plans / reflections(+sources) / character_diaries / relations / conversations / world_events / world_snapshots | 无 | 常规复合索引 |

### 亮点

1. **halfvec(2048)** 半精度向量（memory_episode.py:55-57）：同等维度下索引体积减半，PG18 + pgvector 的正确用法；
2. embedding 异步 Worker：`FOR UPDATE SKIP LOCKED` 批拉取 + 指数退避重试（60/180/600/1800s）+ 5 次熔断（memory_repo.py:136-257）——队列语义正确，多 worker 安全；
3. 月度分区预创建 APScheduler 化（partition_scheduler.py），修复了"连续运行超 3 个月月初写入失败"的真实事故模式（#68）；
4. 混合检索用原生 asyncpg 绕开 ORM 类型转换冲突，`SET LOCAL hnsw.ef_search` 事务内生效（memory_repo.py:277-310）。

### 缺陷

1. **无 retention/archival**：全库 grep 无任何 `DELETE FROM memory_episodes` / 归档任务。HASH 分区又导致无法像 RANGE 分区那样直接 drop 老数据——膨胀只能靠删库重建解决（详见 §九量化）。
2. **乐观锁字段存在但未用于并发控制**：CharacterState.version（前端 api.ts:73 可见）在后端 update_state 路径未见 version 校验；Tick 与 API 对话线程并发写同一角色状态时，靠分布式锁（仅护 Tick）+ 最后写入 wins，API 侧写入可能被 Tick 覆盖或反之。10 分钟对账循环（reconcile.py）能修复漂移但以"Redis 为准"仲裁，API 侧刚写入的合法变更可能被回滚。
3. PgBouncer：README 技术栈表宣称"连接池 PgBouncer"，docker-compose.yml 中**无此服务**——文档失真。
4. world_events 去重基线存内存（engine.py:73），重启后首轮可能重复写入少量事件（无害但不严谨）。

---

## 七、可观测性（4.5 分，全项目最强项）

- **指标矩阵完整且真实埋点**：20+ 自定义指标覆盖 World/Character Tick、Action、LLM（token/cost/耗时分模型）、消息、HTTP（纯 ASGI middleware 兼容 WebSocket，metrics.py:155-189）、对账漂移（RECONCILE_DRIFT/REPAIR）；
- **11 条 Prometheus 告警规则**（docker/observability/alerts.yml）覆盖 Tick 停摆、失败率、LLM 预算/熔断、状态漂移、Redis 断连、5xx——多数同类项目只有指标没有告警；
- OTel 全家桶（fastapi/asyncpg instrumentation）+ Jaeger；Langfuse 手动轻量埋点记录 LLM 输入输出/token/耗时，未配置时静默 no-op（langfuse_tracing.py）；
- structlog 结构化日志 + Loki 采集（Alloy 读容器日志）+ Grafana 预置仪表盘；
- `/ws/dashboard` 实时推送（5s 帧）替代轮询。

差距：Langfuse trace 与 OTel trace id 未关联（两套平行体系）；`DB_QUERY_DURATION` 指标定义后在业务路径未见 observe 点；trace_character_tick 只记 action/duration，未串联同 Tick 内多次 LLM 调用的父子关系。

---

## 八、部署与工程化

### CI（.github/workflows/ci.yml）——高于平均

真实 pgvector:pg18 + Redis service 容器跑集成测试（testcontainers 本地亦可用）、ruff check + format --check、**mypy strict**（pyproject.toml 显式登记豁免及原因，0 宽容）、前端 lint+typecheck+build。Conventional Commits + CHANGELOG + ADR 目录齐全。

### Docker Compose——可用但有硬伤

优点：pgvector 官方镜像统一、healthcheck + depends_on condition、observability profile 按需启动、configs 只读挂载、AOF 持久化。

问题：
1. PG 凭据硬编码（`ai_town:password`，compose:24-33）且 `environment:` 优先级高于 env_file，`.env` 中的 DATABASE_URL 在 compose 下**失效**；
2. Grafana `admin/admin123` 硬编码（compose:180-181）；
3. Redis 版本三处不一致：README 称 8.0 / compose 用浮动 `redis:alpine` / CI 用 redis:7——浮动 tag + 三方漂移，复现性受损；
4. backend 容器未见 alembic 自动迁移步骤，首次 `docker compose up` 后需手动进容器迁移，与"一键部署"承诺有落差。

### 安全

JWT + RBAC 三级（admin/operator/viewer，rbac.py）、WebSocket 握手鉴权（websocket.py:301,511）、Lua 原子限流、prompt_guard、OneBot 消息 SETNX 幂等去重、群聊回复四层策略带概率上限（service.py:57-204，含 CQ 码清理防误判）——安全意识在线。遗留：python-jose（维护停滞，建议 PyJWT）与 passlib[bcrypt]（与 bcrypt 4.x 兼容性问题频发，建议 argon2-cffi）。

---

## 九、长期运行风险清单

### R1 记忆膨胀（最高风险，无任何对策）

量化：`character_tick_seconds=30` → 每角色每天 ≥2,880 条 episode（每 Tick 至少 1 条 + 工具调用/聊天额外条目）。5 角色 ≈ **530 万行/年**；halfvec(2048) 每条约 4KB → 仅向量 **≈20GB/年**，外加 HNSW 索引放大。HASH 分区不能按时间 drop，无归档任务，PG 磁盘与检索延迟将随月线性恶化。**必须在膨胀到来前建立生命周期治理**（见 §十 P0-1）。

### R2 时间衰减使长期记忆失效

22 天阈值（§5.2）意味着"三个月前的重要事件"在决策中不可达——与陪伴定位直接冲突。

### R3 并发写冲突窗口

Tick 锁只保护 Tick 之间；API 对话线程（更新状态/PersonMemory/conversation）与 Tick 并发写同一角色时无版本控制（§六-2）。低负载下概率低，但主动分享、QQ 高频群聊会放大窗口。

### R4 LLM 成本失控面

预算 Lua 原子控制 + 熔断 + 429 退避已做得很好；剩余敞口：群聊智能回复默认读所有消息（`ONEBOT_GROUP_AT_ONLY=false` 默认值），大群场景 flash 判断调用量可观；LLM 评分开关若开启，每条记忆多一次 chat 调用（≈Tick 频率同量级）。成本指标已可观测，建议加"每角色每小时调用次数"告警。

### R5 单点与恢复

Leader 锁 + 快照/增量事件恢复设计完整；但 `_last_persisted_state` 内存态、world:state 无 TTL 无版本号，Redis 数据丢失时依赖快照回放——建议演练一次"Redis 清空冷启动"流程验证 rehydration.py 闭环。

---

## 十、改进建议路线图

### P0（直接影响定位兑现，1-2 个迭代）

1. **接通认知回流**（三处断流，§5.1）：
   - `_perceive` 增加 reflections 注入：取该角色最近 N 条 Reflection 拼入 decision prompt（新增 `{reflections}` 占位符）；
   - 用户对话链路调用 `get_relevant_context()` 注入 chat system prompt（一行接线，收益立竿见影）；
   - 日记摘要注入对应周期的决策上下文（或至少周报）。
2. **记忆生命周期治理**：
   - retention 策略：按 importance 分级保留（如 ≥7 永久 / 4-6 保 90 天 / ≤3 保 30 天），低重要性老记忆压缩为一条摘要 episode 后删除原行；
   - 修复时间衰减：改指数衰减（如 `-max(0, days-3)^0.5 * k`）或对高重要性记忆豁免衰减；
   - 写入去重：content 归一化 hash 唯一约束，或写入前 sim>0.95 近邻检查。
3. **compose 参数化凭据**（`${POSTGRES_PASSWORD:?}` 语法强制外部注入），移除硬编码 Grafana 密码，Redis/镜像版本 pin + 三处对齐。

### P1（架构健康度，2-4 个迭代）

4. `_perceive` 性能重构：合并为 1-2 个 session；nearby 关系批量查询（一条 IN 查询替代循环内逐个 session）；目标：单 Tick DB 往返 ≤4 次。
5. 拆分 main.py：三个后台循环迁往 `src/scheduler/loops.py`（或各自归属包），AuthMiddleware 迁往 auth/。
6. PersonMemory 升级：增量合并 prompt（要求 LLM 输出"保留事实+新增事实"的结构化 diff）、启用 preferences JSONB、补热度衰减定时任务、改走 ORM repository。
7. chat_with 多轮化：2-3 轮往返生成（轮流以双方视角续写），对话文本同时写入双方 conversation 上下文提升即时性。
8. 检索 query 动态化：拼入当前计划标题、情绪、时段（如"周末上午，计划交朋友，心情愉快"），提升向量区分度。
9. 补齐 PgBouncer 或从 README 技术栈表移除；接通 `planChanges` 落库路径（DecisionResult.plan_changes 当前解析后被丢弃，见 §5.4）。

### P2（锦上添花）

10. 前端引入 vitest + Testing Library 冒烟测试（当前 0 测试）；统一 api.ts 手写类型与 openapi-typescript 生成管道的边界。
11. Langfuse 与 OTel trace id 互通；`trace_character_tick` 记录子 generation 层级。
12. 依赖换血：python-jose→PyJWT、passlib→argon2-cffi。
13. 群体动力学实验：基于 `related_characters` 做传闻传播（A 的经历经 B 之口进入 C 的记忆），低成本提升"小镇感"。
14. Redis 冷启动恢复演练脚本 + 文档。

---

## 十一、技术选型评价

| 选型 | 评价 |
|------|------|
| LangChain 1.x + 原生 openai SDK 并存 | ⚠️ 实际只用 ChatOpenAI 包装与 structured_output，LangChain 抽象收益低、升级破坏面大（1.x 大版本刚稳定）；可评估直接依赖 openai SDK 减一层 |
| PostgreSQL 18 + pgvector (halfvec/HNSW) + HASH/RANGE 分区 | ✅ 单库统一结构化+向量，避免额外向量库运维，规模到千万行前都是正确选择 |
| Redis 8（锁/Streams/实时状态/预算/限流） | ✅ 用法克制且每处都有 Lua 原子性意识；注意 README/compose/CI 版本漂移 |
| FastAPI + SQLAlchemy 2.0 async + asyncpg | ✅ 主流稳妥；repository 模式基本贯彻（PersonMemory 除外） |
| React 19 + React Compiler + TanStack 全家桶 + oxlint | ✅ 前端栈激进但自洽；openapi 类型生成管道方向正确 |
| OTel + Langfuse + LGTM 栈 | ✅ 覆盖度罕见地完整；两套 trace 体系待融合 |
| apscheduler（分区预创建） | ✅ 轻量合适 |
| python-jose / passlib | ❌ 均处维护停滞，建议替换（§八） |

---

## 十二、用户体验

- **Web Dashboard**：30 个路由页覆盖角色/世界/记忆/反思/日记/计划/关系/向量搜索/QQ 监控/成本/告警，是完整的"上帝视角"运维台；Glassmorphism + Framer Motion 视觉投入明显；`/ws/dashboard` 实时帧。短板：零自动化测试；错误/空态覆盖未知；移动端适配未验证。
- **QQ 端**：三层回复决策避免打扰、多段回复模拟打字节奏（0.6s 间隔、500 字截断）、主动分享无需用户先开口——陪伴感细节用心。短板：角色间单薄的社会互动（§四）最终会限制 QQ 侧"我的角色有自己的生活"的可感知性；群聊默认读全部消息的隐私观感需文档明示。
- **开发者体验**：AGENTS.md + docs/rules + ADR + CONTRIBUTING + fail-fast 配置 + testcontainers，DX 是同类项目前列。

---

## 附录：审查覆盖范围

**深读（逐行）**：core/character/tick.py（1417 行全量）、core/world/engine.py、memory/{reflection,retrieval,episode,person_memory}_service、db/models/{memory_episode,person_memory,plan,diary}、db/repositories/memory_repo、tools/registry、llm/fallback、cost_control/budget_manager、scheduler/partition_scheduler、observability/{metrics,langfuse_tracing}、runtime.py、main.py（循环段）、auth/rbac、actions/base、configs/prompts/{decision,chat}.yaml、docker-compose.yml、ci.yml、pyproject.toml、README、CHANGELOG。

**定向核查（grep/抽查）**：分层 import 方向、retention 任务存在性、heat 衰减实现、get_relevant_context 调用点、反思回流路径、分区 DDL、群聊回复策略、WebSocket 鉴权、前端路由清单与测试存在性。

**未覆盖**：messaging/service.py 全文（仅抽查）、adapters/onebot.py 协议细节、modules/{movement,schedule,town,duration} 实现、alembic 各迁移全文、前端组件实现质量、性能压测、安全渗透视角的系统性审计。上述结论涉及这些区域时均已标注推断属性。
