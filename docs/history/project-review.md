# ai_town 全面审查报告

> 审查对象：stargaoyc/ai_town（本地 HEAD：`37e00c0`）
> 审查日期：2026-08-24
> 审查方法：全量代码走读（backend 146 文件 / frontend 32 路由）+ 依赖方向交叉验证 + 文档与实现抽查比对。所有结论均附文件路径证据；行号以审查时点代码为准。

---

## 一、执行摘要

ai_town 是一个 LLM 驱动的多智能体虚拟小镇：角色以 30s Tick 周期在 12 个场景中自主决策（Action 选择 + ReAct 工具调用），具备记忆流、两层反思、三层计划、传闻传播等认知机制，用户可通过 Web Dashboard 与 QQ（OneBot 协议）双端交互。

**总体判断：这是一个工程质量显著高于典型个人项目/毕设水准的系统，多处设计超出 Stanford Generative Agents 论文基线**（两层元反思、记忆生命周期治理、异步 Embedding Worker、分布式锁工程、双写对账协议）。边界纪律好：actions 层真正纯函数化、core→messaging 经回调解耦、LLM 无法绕过 precondition 或伪造资源状态。

但项目存在一条清晰的分界线：**「精巧机制」与「数据一致性」之间存在系统性裂缝**——场景数据存在三套并行表示且互相矛盾、API 移动路径绕过审计与领域规则、若干幻影数据源与死分支 bug 未被测试捕获。此外可观测性栈组件齐全但核心引擎链路实际无自定义 span，成本追踪单价表与实际模型错配。

---

## 修复进度（2026-08-25 更新）

本报告发布后已按 P0→P3 分波修复，**除三项结构性重构外全部完成**（467 后端测试全绿，mypy strict 0 错误）：

### ✅ 已修复

| 类别 | 内容 |
|---|---|
| P0 正确性/安全 | JWT_SECRET/API_KEY 生产 fail-fast；move/schedule 端点 world_time 字段名；场景演化器与 scenes.yaml 统一真相源；幻影 matrix 键与死代码清除；RelationGraph 升级日志死分支；is_open 全天场景恒关闭的存量 bug（修复中顺带发现） |
| P1 高价值 | trace_span 接入 world.tick/character.tick/embedding.batch；LLM 单价参数化（全局默认+按模型覆盖）+ Langfuse per-call cost 与 prompt/completion 拆分；工具记忆 importance 7→6 消除永久膨胀；Person Memory 注入决策 Prompt（[记得的用户]）；四处文档漂移修正 |
| P2 路线图 | world_events/snapshots 保留策略；API move 补 ActionRecord 审计 + record_movement 单一入口统一双账本；shutdown 补 engine.dispose/close；Alertmanager 服务 + nginx 非 root；REDIS 周期探活 + Embedding Worker/Streams 指标；_perceive 会话合并 8→5；事件中断重规划轻量注入（[近期世界动态] + planChanges 提示） |
| P3 | is_open 双实现收敛到 schema.is_open_hours；decision 输出格式由 JSON Schema 派生（单一真相源）；decision.yaml 补 few-shot 示例；QQ push_share 连接故障转移 + 群配置 TTL 缓存；playwright 空挂依赖移除；根目录空 scripts/ 清理 |

### ⏳ 遗留项（结构性重构，择机处理）

1. **tick.py 上帝类拆分**：感知/决策/执行等阶段类化、chat/group/move 特判分支 handler 化。测试安全网已就绪，建议独立分支专项进行。
2. **runtime.py 服务定位器 → 构造注入**：审查原议「逐步替换」，需全局性改动。
3. **api/admin.py 拆分**：纯机械搬移，宜与其他 admin 改动同批进行。
4. **前端路由页组件测试**：需先引入 jsdom + Provider 测试基建（@testing-library 已就位）。

---

### 评分卡

| 维度 | 评级 | 一句话评语 |
|---|---|---|
| 项目定位 | ★★★★☆ | 定位清晰，「陪伴+观察」差异化于纯模拟，但产品闭环有断层 |
| 分层架构 | ★★★★☆ | 依赖方向干净无循环，但文档声明的 Service 层实际不存在 |
| 认知机制完备性 | ★★★★☆ | 三件套齐全且多处超 GA；规划缺事件中断是最大短板 |
| 多智能体交互 | ★★★☆☆ | gossip 设计克制优秀；对话被动方无能动性是结构性折衷 |
| Action 系统 | ★★★★☆ | precondition/executor 约定真实落实，特判分支泄漏多态 |
| 数据持久化 | ★★★★★ | 分区/乐观锁/双写对账/HNSW 达到生产级成熟度 |
| 可观测性 | ★★★☆☆ | 组件齐全告警质量高，但引擎链路零 span + 成本失真 |
| 技术选型 | ★★★★☆ | 全现代栈且版本新，LangChain 用得克制不滥用 |
| 部署与前端 | ★★★★☆ | CI 契约守卫是亮点；nginx root 运行、e2e 空挂 |
| 长期运行风险 | ★★★★☆ | 并发防护成熟；工具记忆永久膨胀是真实盲点 |
| 用户体验 | ★★★☆☆ | 双通道触达完整；决策链路想不起用户是产品级缺陷 |

---

## 二、项目定位评估

**定位合理性：成立且有差异化。** 项目不是 Generative Agents 的复刻 demo，而是「小镇模拟 + 用户陪伴」的混合体：

1. **模拟侧**：世界引擎独立演化时间/天气/场景拥挤度/资源/事件，角色有资源约束（体力/饱腹/社交能量/金钱/手机电量）、作息类型（早鸟/夜猫）、场景开放时段——这些让行为有物理约束感，而非纯 LLM 幻想。
2. **陪伴侧**：Person Memory（用户画像主档 + append-only 事实条目）、主动分享推送、QQ 群聊三层智能回复——这是 GA 论文完全没有的方向。
3. **可观测侧**：32 个前端路由中约一半是监控/审计页面（memories/reflections/diaries/plans/events/cost/vector-search...），把「黑盒智能体」做成「白盒可审计」，这是工程自觉。

**定位层面的两个问题**：

- **两条产品线的认知割裂**（详见 §4.4）：角色在 QQ/Web 上「记得」用户的一切，但在镇内生活决策时完全想不起任何用户——Person Memory 不注入 decision 链路。陪伴体验与模拟体验互不相通，这是当前定位下最值得修的产品级缺口。
- **「可选沉淀」承诺未兑现**：`messaging/service.py:9` docstring 称用户消息「可选：沉淀为 memory_episodes」，但全文无任何写入代码。角色对用户的记忆只存在于 person_memories 独立管线，意味着用户对话永远不会进入角色的记忆流检索、反思与日记体系。文档与实现不符，且这个「不做」本身是否正确值得重新决策。

---

## 三、分层架构与模块边界

### 3.1 依赖方向验证（grep 全量 import 交叉验证）

| 层 | 实际依赖 | 判定 |
|---|---|---|
| `db/` | 仅 `src.config` | ✅ 干净 |
| `actions/` | 仅自身（纯 Pydantic 模型 + callable） | ✅ 真正的纯函数层 |
| `modules/` | `db` + 自身 | ✅ 干净 |
| `memory/` | `config`/`db`/`llm`/`runtime` | ✅ 干净 |
| `core/` | **不 import messaging/api/adapters** | ✅ 经回调解耦 |
| `api/` | 直连 `db.repositories`、`messaging`、`core.world.evolutions` | ⚠️ 见下 |

**未发现循环依赖。** 边界纪律的最佳示范：core 层需要触发主动分享（messaging 职责）时不直接依赖，而是经 `runtime.set_proactive_share_handler()` 回调注册（`main.py:226`、`tick.py:1593-1606` 注释明确说明设计意图）。

### 3.2 文档与实现的偏差

AGENTS.md 声明「API 层 → Service 层 → Core 层 → Infrastructure 层」，但**通用 Service 层实际不存在**：`api/characters.py`、`api/world.py`、`api/memory.py` 直接查询 Repository 并内嵌业务逻辑（如 move 端点在路由函数里手写 PG+Redis 双写，`api/characters.py:279-285`）。唯一的真 Service 层组件是 `MessageService`。要么补齐 Service 层，要么修正文档——当前状态会误导后续贡献者。

### 3.3 结构性坏味道（按严重度排序）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | **tick.py 上帝类（1634 行）** | `core/character/tick.py` | 单类承担感知/决策/ReAct/执行/对话/群活动/传闻/分享/记忆/计划 11 种职责，`_execute_action` 约 240 行。新增认知阶段必须改它，已成为演化瓶颈 |
| 2 | **runtime.py 服务定位器** | `runtime.py`（17 个全局单例） | 解决了对 main.py 的反向依赖（初衷正当），但引入隐藏耦合与遍布各处的 None 检查，可测性弱于构造注入 |
| 3 | **main.py lifespan 手工装配巨函数** | `main.py:100-500` | 约 400 行，步骤编号 0.5/1.2/3.5/5.55 式增长，shutdown 手工逐个 cancel 8 个任务 |
| 4 | **api/admin.py 上帝路由** | `api/admin.py`（852 行 34 端点） | 配置 CRUD、导入、向量搜索、日志混杂 |
| 5 | **每 Tick 串行开 7+ 个 DB 会话** | `tick.py:314,368,382,397,413,429,447` | `_perceive` 各数据源独立 session 顺序查询，50 角色 × 每 30s 的往返放大明显，可合并为单 session |
| 6 | **同一文件两种世界时间解析** | `tick.py:67-75` vs `877-886` | 手工 split vs fromisoformat；且 `_perceive:360` 用现实 UTC 小时做检索 time_band，作息却用虚拟小时——时间语义不一致 |

---

## 四、智能体认知机制完备性（对照 Stanford Generative Agents）

### 4.1 记忆流 — 完整度高，工程化超出 GA

- **写入**：每 Tick `_memorize` 写入 MemoryEpisode（`memory/episode_service.py`）；embedding 由独立 `EmbeddingWorker` 异步批量生成（`FOR UPDATE SKIP LOCKED` 多实例安全，失败指数退避 60s→1800s，5 次熔断）——GA 论文没有异步向量化，这是正确的工程化。
- **检索**：`MemoryRepository.search_hybrid`（`memory_repo.py:399-463`）实现 GA 的 recency×importance×relevance 三因子：
  ```
  final_score = (sim*0.6 + importance*0.05) * (0.25 + 0.75*exp(-天数/30))
  ```
  差异：GA 对三因子做逐查询 min-max 归一化后加权；此处权重硬编码且 importance 未归一化（贡献上限 0.5 vs 相似度上限 0.6），量纲不对齐。检索 query 动态构造（位置+时段+情绪+计划标题）优于 GA 的静态 query。
- **去重双层**：写入时精确去重（空白归一化+24h 窗口）+ 向量化时改写式去重（余弦 ≥0.95 判 is_duplicate，注释记录了 pg_trgm 中文失效的实测教训）。
- **重要性评分**：规则表 `_ACTION_IMPORTANCE`（情绪关键词 +2）+ 可选 LLM rubric 评分（`configs/prompts/memory_score.yaml` 四维度直接对应 GA poignancy 定义）。
- **生命周期治理（GA 没有）**：24h 循环按角色×月 LLM 压缩归档 + 分级删除（imp≤3@90d、4-6@180d、≥7 永久），不变量「未压缩成功绝不删除」（`scheduler/loops.py:467-651`）。

### 4.2 反思 — 层级超出 GA

- 触发：计数阈值（20 条未反思记忆）而非 GA 的重要性累加阈值；低重要性高频行为同样累积触发，可能稀释反思质量。
- 产物两层：tier-1 批次主题反思（30 条编号记忆归纳 2-4 主题，强制 memory_ids grounding 校验）+ **tier-2 跨期元反思**（≥6 条反思且 7 天冷却期满时归纳「长期倾向」）——GA 无元反思层，此为超出论文的设计。
- 回流：经 reflection_sources 中间表挂载来源（复合外键级联删除）；最近 5 条注入决策 Prompt `[高层认知]` 段。
- 缺失：GA 的「问题生成」步骤（先让 LLM 提 salient 问题再回答）未实现。

### 4.3 规划 — 完整度中等，缺事件中断是最大短板

- plans 表三层目标层级（long_term/short_term/daily）+ priority/deadline/progress；daily TTL 滚动过期。**无小时级日程层**（GA 有 hour-by-hour schedule）。
- 决策时可携带 planChanges/createPlanChanges 在同事务落库，character_id 服务端约束防跨角色篡改（`tick.py:1336-1437`）。
- **关键缺口：无事件中断重规划**。GA 中感知到的外部事件会打断计划并触发 replan；此处世界事件不会强制角色修订计划，计划只在角色自己的 Tick 内由 LLM 主动变更——计划是「被动参考物」而非「被执行的日程」。这直接削弱了「计划驱动行为」的真实感。

### 4.4 用户专属记忆归档 — 隔离清晰，但有产品级断层

- 两层结构正确：person_memories 主档 + person_memory_entries append-only 条目，LLM 只做增量抽取不做全文重写（消除 telephone-game 漂移）；热度衰减（14 天减半）+ 主档压缩（≥20 条合并）双循环维护。
- 隔离性确认：用户对话不写 memory_episodes，角色间对话才写（source_type=conversation，双方各一条第一人称记忆）。
- **断层 1（产品级）**：Person Memory 不注入小镇决策链路——decision.yaml 无 person_memory 变量。角色在镇内生活时「想不起」任何用户，陪伴关系无法影响其行为（如「答应过用户要去图书馆」不会发生）。
- **断层 2**：用户关系只有 heat 计数，无 relations 表那样的分档关系值系统（relations 仅限角色↔角色）。

### 4.5 ReAct 工具调用 — 中高完成度，GA 之外的扩展

- TOOL_REGISTRY 15 工具×5 命名空间；`injected_params` 由服务端从 state 注入 current_money/inventory/relation_strength——**LLM 无法伪造资源数值**，这是全项目最值得肯定的安全设计之一。
- ReAct 循环最多 3 轮，超限强制降级 wait；工具结果以 importance=7 写入记忆流（⚠️ 该值恰好落入「永久保留」区间，见 §11.2）。
- LangChain 集成方式：非 function-calling，而是 prompt 文本 ReAct + `with_structured_output` 动态 Pydantic 模型。自研轻量可控，但放弃了原生 tool-calling 的 token 效率与可靠性收益——当前规模合理，工具数增长后建议评估迁移。

### 4.6 Prompt 工程 — 纪律性强，few-shot 缺失

17 个 YAML 全外置，启动校验必需模板缺失即 fail-fast，支持热更新。decision.yaml 分区段清晰且有反谄媚人格指令（「状态先于计划」「不追求数值最优」）。chat.yaml 有 system_template 安全底线 + PromptGuard 注入包裹。**短板：全部模板零 few-shot 示例**，输出格式靠指令约束 + 解析容错兜底；decision schema 在 YAML 文本与 Pydantic 模型间存在双份定义（单一真相源轻微违例）。

### 4.7 可演化性评估

| 扩展类型 | 成本 | 说明 |
|---|---|---|
| 新增 Action | 低 | 注册一个 `Action(...)` 即自动进入候选过滤与 Prompt 渲染 |
| 新增工具 | 极低 | TOOL_REGISTRY 加一个字典条目，自动获得格式化/启停/参数注入——全项目最好扩展点 |
| 新增 Prompt | 低 | 放 YAML 即生效 |
| 情绪系统 | 中 | mood 只是字符串字段，需动 state_codec + 决策变量 + 可能新增 loop，无现成插槽但路径清楚 |
| 新认知阶段 | 高 | tick.py 无「认知流水线阶段」抽象，阶段顺序隐式存在于方法调用序列，必须直接改上帝类 |

---

## 五、多智能体交互合理性

### 5.1 已实现的三条交互通路

1. **对话 chat_with**：校验目标在同场景 → 双方资源锁（按 ID 排序防死锁）→ 单次 LLM 生成双方对话 → 关系双向 +5（陌生人破冰 +2）→ 双方各写第一人称记忆。
2. **群活动 group_activity**：同场景 ≥2 其他角色才进候选；单次 LLM 集体叙事，失败降级模板叙事；全体参与者写共同经历记忆并两两关系 +2。
3. **传闻 gossip**（设计最克制优秀）：沿 strength≥20 的既有关系流动；只传播一手经历（source_type=action，防二手八卦失真）；内容取源记忆原文拼接不经 LLM 编造；importance 减半递减；每好友每窗口去重。「事实同源、观感留给反思」的不变量表述清醒。

### 5.2 结构性局限

- **被动方无能动性**：B 的回应由发起方 A 的一次 LLM 调用代笔，B 只能在自己 Tick 里通过记忆「回忆起」。交互的双向性只体现在记忆和关系数据上，不体现在决策权上。这是计算成本的务实折衷（50 角色 × 事件驱动对话不可控），但意味着「对话质量」上限受单次调用限制，且 B 永远不会拒绝一场它不想参与的对话。
- **全部轮询驱动**：无事件驱动的智能体间消息，交互粒度被锁死在 30s Tick 上。当前规模合理，若追求「偶遇即聊」的自然感需要事件总线。

---

## 六、Action 系统与 LLM 边界

**约定真实落实**（多处有代码证据）：

- precondition 三重过滤（precondition(state) + 场景匹配 + 资源充足性，`actions/registry.py:73-85`）；LLM 输出二次校验，非法 action_id 回退 wait（`tick.py:665-670`）——**LLM 无法绕过 precondition**。
- executor 返回绝对值 new_state，apply_cost_fields clamp 到 [0,100]、money 下界 0 不产生负债；单一 PG 事务写 ActionRecord+状态+历史+计划变更，**提交后**才写 Redis——符合 AGENTS.md §4.1。
- move 决策经 MovementSystem 按连通矩阵校验，幻觉场景降级 wait；动态时长仅在 allow_dynamic_duration=True 时生效。

**两处抽象泄漏**：

1. `_execute_action` 对 chat_with/group_activity/move 硬编码特判分支（`tick.py:910-988`）——executor 多态在最复杂的三个 Action 上失效，业务逻辑上浮到引擎。
2. ReAct 工具 delta 直写 PG relations 发生在主事务之外（`tick.py:845-875`）；`_do_chat_with` 的关系更新与双方记忆写入是三个独立事务，部分失败仅告警不回滚——「单一事务」主张在这些路径上不成立（有意的可用性取舍，但应显式记录）。

---

## 七、数据持久化设计 — 生产级成熟度

### 7.1 PG Schema（16 表，Alembic 13 版本迁移链）

亮点密集：

- memory_episodes 按 character_id **HASH 分区×16** + 复合主键 + 父表 HNSW 索引自动传播子分区；action_records / character_state_history 按月 RANGE 分区，PartitionScheduler 每月 25 号预建未来 3 个月分区（修复了连续运行 >3 月漏建分区的隐患）。
- character_states 带 version 乐观锁列 + fillfactor=85 + autovacuum 调优。
- reflection_sources 用复合外键引用分区表——分区表外键的正确姿势。
- world_events 事件溯源差分表带 UNIQUE(tick_id, event_type, event_key) 幂等约束。
- 所有 downgrade raise RuntimeError（「只升级不降级」原则）。

### 7.2 Redis 使用模式

Key 设计清晰（char:{id}:state / world:state 六哈希分域 / 各类锁 / llm:cost 日预算 / onebot Streams+DLQ）；实时状态无 TTL（真相源语义正确）；序列化统一收敛 state_codec.py（注释明确禁止各处自行 json.dumps，历史 bug 教训）。Redis 启动 ping 失败即中断启动（fail-fast）。

### 7.3 双写一致性协议 — 同类项目中罕见的高完成度

PG 事务先提交 → 再写 Redis（`tick.py:1032-1130`）；崩溃窗口由三层兜底：下次 Tick 全量重写、启动 rehydrate 补键、**运行期 reconcile 每 600s 版本感知仲裁**（用 rec_ver 基线判断 PG version 是否前进，前进则 pg_to_redis 反向修复，避免回滚合法变更，`reconcile.py:120-157`）。current_action 明确排除在对账外（瞬态语义正确）。

### 7.4 向量检索

HALFVEC(2048) 半精度 + HNSW(m=16, ef_construction=128) + 余弦距离；`SET LOCAL hnsw.ef_search=100` 事务内生效防注入；HASH 分区裁剪使检索只扫单角色分区。embedding 独立 API Key/URL 支持多模态模型。**注意**：README/architecture.md 写 1536，代码与 .env.example 均为 2048——按旧文档配置会导致 pgvector 列维度不匹配启动失败（见 §14）。

---

## 八、可观测性覆盖度 — 组件齐全，两处硬伤

**已覆盖**：OTel（FastAPI+asyncpg 自动 instrument，OTLP 导出，采样 0.5）；Langfuse（Tick 根 trace + 每次 LLM 调用记录 tokens/latency + otel_trace_id 与 Jaeger 交叉引用）；Prometheus 指标清单完整（world/character tick、action、LLM calls/tokens/**cost_usd**、消息、DB query duration、reconcile drift、HTTP 中间件）；structlog JSON + trace_id 注入第三方日志；Grafana 3 个预置仪表盘 + 9 条分级告警规则（WorldTickStalled、LLM 预算预警、CircuitBreakerStuck 等，质量高）。

**硬伤 1：trace_span 装饰器零使用**。定义于 `observability/tracing.py:214-283` 并导出，但全库无一处调用。后果：World Tick / Character Tick / Embedding Worker / 对账循环均为后台 asyncio 任务而非 HTTP 请求，**核心引擎链路完全没有 OTel span**——Jaeger 里只有 FastAPI 入口和 asyncpg SQL 散 span，看不到「感知→决策→执行」五阶段拓扑。观测栈的最大投入点恰恰没有观测。

**硬伤 2：LLM 成本追踪系统性失真**。`estimate_cost` 硬编码单价表（agnes-2.0-flash 价格，`llm/client.py:42-45`），但 config 默认模型是 gpt-4o-mini/gpt-4o（价格差 5~20 倍）——配置 OpenAI 模型时 `llm_cost_total_usd` 与日预算控制均基于错误单价；embed 路径还内联重复了同一公式，违反其自身「唯一费用计算入口」约定。附带：Langfuse 实际调用点不记 per-call cost、usage 无 prompt/completion 拆分。

**其他盲区**：REDIS_CONNECTED 无周期探活（断连可能漏报）；Embedding Worker 吞吐/失败率只进日志无指标；Redis Streams 积压深度与 DLQ 数量无 gauge；compose 无 Alertmanager 服务（告警只能看 Grafana UI，无通知路由）。

---

## 九、技术选型合理性

| 选型 | 评价 |
|---|---|
| FastAPI + SQLAlchemy async + Alembic | ✅ 主流且用得规范 |
| Redis 8 作为实时状态真相源 | ✅ 正确的读写特征匹配（高频小状态 + 锁原语 + Streams） |
| pgvector HALFVEC + HNSW | ✅ 半精度省内存，2048 维在 HNSW 4000 维上限内；比外置向量库少一个运维单元，规模合适 |
| LangChain（仅 ChatOpenAI + structured_output） | ✅ 克制不滥用——没用 Agent 框架/LCEL 全家桶，自研 ReAct 可控性强 |
| React 19 + TanStack Router/Query + Zustand | ✅ 服务端状态与客户端状态分工明确 |
| Rolldown-Vite + React Compiler + oxlint/oxfmt | ✅ 相当激进的新工具链，说明维护者技术敏感度高 |
| OneBot v11/v12 反向 WebSocket | ✅ 后端做 WS 服务端、NapCat 反连——无需公网入站端口，聪明 |
| uv + pnpm monorepo | ✅ 与项目规模匹配 |

**选型层面唯一保留意见**：prompt-text ReAct vs 原生 function calling（见 §4.5），以及 structlog JSON 日志文件直接落 `data/logs/` 与数据目录混放（备份策略需注意区分）。

---

## 十、部署与前端工程化

### 10.1 Docker Compose — 高质量

10 服务三层编排（基础设施/应用/可选 observability+backup profile）；x-default-logging 锚点统一日志轮转防磁盘吃满；postgres/redis healthcheck + depends_on service_healthy；后端镜像多阶段构建 + 非 root 用户 + HEALTHCHECK + CMD 内嵌 alembic（注释自知多副本需拆 Job）；backup.sh 原子改名防半成品。**缺口**：前端 nginx 容器 root 运行；无镜像 digest 固定；`.env.example` 的 `OTEL_ENDPOINT=http://localhost:4318` 与 jaeger 容器映射 14318 不匹配——容器部署照抄模板 trace 会静默丢失；prometheus 抓 host.docker.internal 在原生 Linux 不可用。

### 10.2 CI/CD — 有突出亮点

后端 job 用真实 pgvector+redis service 容器跑集成测试；ruff/mypy strict/pytest 四道闸。**API 契约漂移守卫是同类项目罕见的亮点**（ci.yml:96-99）：`pnpm gen:api && git diff --exit-code` 使前后端契约漂移必然挂红。缺口：无镜像构建发布 job；devDependencies 挂着 @playwright/test 但零 e2e 测试（疑似烂尾）。

### 10.3 React 前端 — 中高

32 路由文件路由 + autoCodeSplitting（实测 71 chunk，recharts 独立拆包懒加载）；queryKeys 工厂 + TanStack Query 5 管服务端状态、Zustand 仅管认证态；WS 推送替代轮询 + 指数退避重连；loading/error/empty 四态处理完备。**缺口**：README 称 shadcn/ui 实际是自建单文件 ui.tsx（469 行）；手写 interface 与生成类型混合处于过渡期（有明确退出条件，CI 兜底）；路由页面零组件测试。

### 10.4 QQ 集成 — 超出个人项目平均水准

反向 WS + access-token 强校验（compare_digest 防时序攻击）+ Redis SETNX 消息幂等 + **Streams 兜底队列**（至少一次语义、崩溃恢复重放、投递 5 次进 DLQ）+ 群聊三层智能回复（概率闸门集中单点可审计）+ 多段回复模拟打字节奏。隐私权衡已在 README 明示。局限：push_share 取 conns[0] 任意连接；群配置 JSON 每条消息重新解析无缓存；纯文本 only。

---

## 十一、长期运行风险

### 11.1 并发冲突 — 防护成熟，残留窗口已收窄

多层防护齐全：每角色 Redis 锁（SET NX EX + Lua compare-and-delete + 看门狗 TTL/3 续租）→ Semaphore(10) 限流（支持热更新重建）→ 跨角色锁排序获取防死锁 → World Leader 选举 + fencing check 防 GC 停顿双写 → Embedding SKIP LOCKED → reconcile 对账。残留风险：fencing 自认 check-then-act 非原子（单实例无碍）；CircuitBreaker failure_count 读改写非原子（仅导致熔断稍晚，非正确性问题）。

### 11.2 记忆膨胀 — 治理体系完整，一个真实盲点

治理体系（分级删除/LLM 压缩归档/双层去重/反思有界不级联/Person Memory 热度衰减+压缩）在同类项目中属顶配。Token 成本随角色数线性、不随运行时长增长（检索 top_k 固定），日预算 $10 硬兜底。

**盲点：工具调用记忆线性膨胀**。每次 ReAct 工具调用固定写 importance=7 记忆（`tick.py:756`），而 retention 对 ≥7 永久保留——长期运行下这类记忆永不清理且持续占据 HNSW 索引。次要：world_events/world_snapshots 无清理策略（差分每 10 tick 写一批、快照每 1000 tick 一条，持续增长）。

### 11.3 LLM 容错 — 五层防御完备

超时 30s/重试 2 次 → 三态熔断器（Redis 共享 + HALF_OPEN Lua 原子试探名额）→ 多备用源 5 分钟冷却切换 → 429 指数退避（按异常类型判定，修复过字符串误判）→ 日预算硬拒。降级路径齐全（熔断跳周期、chat 失败降 wait、检索失败不阻断）。

### 11.4 资源泄漏与停机 — 两个小瑕疵

优雅停机完备（flush Langfuse → 清 fire-and-forget 注册表 → 停 7 循环 + WorldEngine 释放 leader 锁 → 关 Redis）。瑕疵：shutdown 未调 `db.engine.dispose()` 与 `LLMClient.close()`（进程退出兜底，轻微）。

### 11.5 安全 — 一处未缓解的重点

良好面：生产模式 admin123 fail-fast 拒启、RBAC 默认 viewer 最小权限、OneBot token 强校验、WS 握手校验 JWT sub==user_id、内部异常详情不外发、PromptGuard 注入拦截。**重点缺口：JWT_SECRET 与 API_KEY 的默认值没有生产 fail-fast 校验**（只有 ADMIN_PASSWORD 有）——生产忘改 JWT_SECRET 时任何人可伪造任意用户 token。修复成本一行 settings 校验。

---

## 十二、用户体验

- **双通道触达完整**：Web WS 同步问答（延迟≈1 次 LLM 调用，秒级）+ QQ 群聊三层回复/@ 必回/主动分享无需用户先发言。
- **前端状态处理完备**：四态渲染、发送中禁用、断线指数退避重连、Dashboard 5s 推送帧。
- **节奏感设计用心**：世界 20 倍速、角色 30s 行动粒度、QQ 多段回复 0.6s 打字间隔。
- **主要短板**：① 角色镇内决策想不起用户（§4.4 断层 1）——「我昨天跟 TA 说过的话」不影响 TA 今天做什么，陪伴沉浸感断裂；② 无 onboarding 流程；③ 角色主动行为的可预期性弱（用户无法预约互动或给角色留言影响其计划）。

---

## 十三、文档质量

整体高于平均（docs/ 15+ 篇专题文档，README 配置速查实用），但存在**会直接坑人的过时项**：

| 项 | 文档说 | 代码实际 | 后果 |
|---|---|---|---|
| EMBEDDING_DIM | README/architecture.md 写 1536 / HALFVEC(1536) | config.py 与 .env.example 均 2048 | 按文档配置启动失败 |
| OTEL_ENDPOINT | .env.example 写 localhost:4318 | jaeger 容器映射 14318 | 容器部署 trace 静默丢失 |
| UI 库 | README 称 shadcn/ui | 自建单文件 ui.tsx | 认知偏差 |
| 用户记忆沉淀 | messaging/service.py docstring「可选沉淀 memory_episodes」 | 无实现 | 设计意图失实 |
| 分层 | AGENTS.md「API→Service→Core→Infra」 | 无通用 Service 层 | 误导贡献者 |

正面：architecture.md 对 EmbeddingWorker 熔断退避等机制的描述与代码逐行吻合（抽查通过），说明大部分文档是跟着代码写的，上述是局部漂移。

---

## 十四、问题清单与改进建议

### P0 — 正确性/安全，建议立即修

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| P0-1 | JWT_SECRET/API_KEY 默认值无生产 fail-fast | `main.py:128-138`（仅 ADMIN_PASSWORD 有校验） | 参照 ADMIN_PASSWORD 模式补两行校验 |
| P0-2 | API move 端点读错 hash 字段名：`get("time")` 应为 `"world_time"` → hour 恒回退 8 点，开放时段判断失真 | `api/characters.py:267` vs `engine.py:488` | 修字段名 + 为该端点补测试（正是无测试藏 bug 的地方） |
| P0-3 | 场景数据三套并行表示互相矛盾：scene_evolution 硬编码 DEFAULT_SCENES 仅 6 场景（含 yaml 不存在的 shop/bar，遗漏 8 个）→ 世界拥挤度只覆盖半个小镇 | `scene_evolution.py:25-32` vs `configs/scenes.yaml` | 删硬编码，从 SceneLoader 注入；补一致性测试 |
| P0-4 | 幻影数据源：Redis `world:state:matrix` 全仓库只读不写；`compute_move_duration` 是死代码；world 工具返回的场景出口恒空 | `actions/move.py:23,36-65`、`tools/world.py:36` | 要么由 engine 写入该键，要么删死代码并把工具改为读 SceneLoader 内存矩阵 |
| P0-5 | RelationGraph 升级日志死分支：line 142 先覆写 relationship_type，line 151 再比较恒 False | `modules/relation/graph.py:142,151` | 先比较后赋值 |

### P1 — 高价值改进

1. **接入 trace_span 到引擎链路**：World Tick / Character Tick 五阶段 / Embedding Worker 加自定义 span——观测栈投入已经很大，差最后一步就能看到全链路拓扑。
2. **成本单价表参数化**：按 model 映射价格或从配置读取；embed 路径复用同一入口；Langfuse 补 per-call cost 与 prompt/completion 拆分。
3. **工具记忆膨胀**：use_tool 记忆 importance 降到 ≤6（进入 180 天删除档），或为 source_type=tool 类记忆单独设保留上限。
4. **Person Memory 注入决策链路**：decision.yaml 增加 person_memory 变量（近期交互摘要 top-N），打通「陪伴影响行为」的产品闭环——这是性价比最高的产品级改进。
5. **EMBEDDING_DIM 等四处文档漂移修正**（§13 表格）。
6. **tick.py 拆分**：优先抽出三个特判分支为独立 handler（chat/group/move 各自模块化），再考虑 perceive/decide/execute/memorize 四阶段类化。测试覆盖良好，重构有安全网。

### P2 — 应列入路线图

- world_events/world_snapshots 增加保留策略（复用 retention loop 模式）。
- API move 路径补 ActionRecord 审计 + 统一到 VISITORS_KEY 单轨记账（消除双轨）。
- shutdown 补 `db.engine.dispose()` 与 `LLMClient.close()`。
- compose 增加 Alertmanager；REDIS_CONNECTED 周期探活；补 Embedding Worker 吞吐指标与 Streams 积压 gauge。
- 规划系统补「事件中断重规划」：world_event 广播时给受影响角色的下一 Tick 注入 replan 提示（轻量实现即可显著提升真实感）。
- OTEL_ENDPOINT 模板改为 `http://jaeger:4318` 并注明本地开发差异；前端 nginx 非 root 化。
- `_perceive` 合并为单 DB session（性能：50 角色 × 每 30s 省 6 次往返）。
- Playwright e2e：要么落地最小冒烟（登录→发消息→收回复），要么移除依赖。

### P3 — 择机处理

runtime.py 服务定位器逐步替换构造注入；admin.py 按域拆分路由；Prompt 补 few-shot（尤其 decision 输出格式）；decision schema 收敛单一真相源；is_open 与世界时间解析各收敛为一处实现；QQ push_share 连接选择策略化 + 群配置缓存；根目录空 scripts/ 清理；前端路由页补关键组件测试；用户对话沉淀 memory_episodes 的「可选」承诺要么实现要么删 docstring。

---

## 十五、结语

ai_town 证明了个人项目也可以做到生产级的持久化设计（分区+乐观锁+版本仲裁对账）、严谨的并发工程（Lua CAS 锁+看门狗+fencing）和克制的 LLM 边界设计（precondition 不可绕过、资源状态服务端注入）。它的认知机制不是论文复刻而是有取舍的再设计——两层反思、记忆生命周期治理、gossip 不变量都体现了独立的工程判断。

它同时展示了复杂系统的典型病灶如何在一个高纪律项目里依然滋生：上帝类在功能演进中自然长成、三套场景表示在两次独立开发中分叉、观测装饰器定义了却没人用、成本单价表在换模型时忘了跟。这些问题没有一个源于能力不足，全部源于**缺少跨模块一致性的守护机制**（一致性测试、架构适配器检查、文档-代码同步流程）。

如果只做三件事：修 P0 的五个正确性问题、把 trace_span 接进引擎链路、让 Person Memory 进入决策 Prompt——项目的「可信度」「可观测性」「产品完整性」将各上一个台阶。
