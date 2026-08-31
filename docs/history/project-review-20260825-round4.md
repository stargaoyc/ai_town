# AI Town 全面审查报告·第四轮（2026-08-25，HEAD `900c73d`）

> **文档定位**：对[首轮](project-review-20260824.md)（基线 `dabecc9`）、[二轮](project-review-20260824-round2.md)（基线
> `0da5e79`）、[三轮](project-review-20260825.md)（基线 `1e8cc86`）之后的**修复批 + 认知特性批**
> （`1e8cc86..900c73d`，15 commits）的全面独立重审。本轮方法：六路并行深审（认知机制 / ReAct 决策链路 /
> 消息与多端触达 / 数据持久化 / 可观测性 / 部署与前端）+ 核心链路亲读 + 三轮 HIGH 修复逐项抽查 +
> 质量门禁本地实测。所有结论基于当前工作区源码直接阅读，关键结论附 file:line 证据。
>
> **严重度定义**（沿用前三轮）：P0=核心功能静默失效/发布阻断；P1=功能性 bug/承诺违约/结构性缺陷/隐私泄露；
> P2=纵深防御缺口/文档漂移/性能隐患；P3=瑕疵。

---

## 一、执行摘要

**一句话结论：三轮修复批全部属实且质量扎实（H1-H10 逐项抽查通过、门禁全绿），认知特性批五项战略能力
真实落地；但本轮以「宣称↔实现」为标尺重新下钻，发现了前三轮均未触及的两类根因级问题——
①ReAct 工具调用循环自诞生起就是死代码（P0，README 核心特性宣称失效）；②Person Memory 与记忆流的
读路径被全局鉴权中间件的公开前缀豁免击穿（P0 级隐私暴露）；同时部署验证深度问题被今天的两个 hotfix
自我证实——CI 至今不含任何镜像构建与运行时冒烟。**

本轮最重要的三个判断：

1. **ReAct 循环不可达是「出生缺陷」而非回归**。决策校验（`27f2d20` 引入）先于 ReAct 特性（`68ec290`）
   落地：`_decide` 把不在候选列表里的 action 一律改写为 `wait`（tick.py:827-832），而 `use_tool` 从未注册为
   Action——循环守卫（tick.py:321）永远在首轮 break。工具注册表、观察回注、delta 应用、媒体工具整条链路
   均为潜在代码，且零测试覆盖。这直接击穿 README 特性表第 5 行。
2. **隐私边界被「Dashboard 免登录」设计误伤**。AuthMiddleware 的 `PUBLIC_GET_PREFIXES` 豁免了
   `/api/v1/characters` 与 `/api/v1/memories` 两个前缀（middleware.py:131-138），而 Person Memory
   （角色对特定用户的偏好/互动史/情感连接）、记忆流、日记恰好全部挂在这两个前缀下且无路由级鉴权
   （memory.py:114,132 等）——中间件自己的注释写着「含用户隐私……必须登录后按归属校验访问」
   （middleware.py:130），实现与意图相反。
3. **部署验证深度被同日双 hotfix 自我证实不足**。`c387487` 自述「后端容器此前从未成功启动即崩于路径
   硬编码」「前端镜像此前不可构建」，`900c73d` 再修 nginx 静态 DNS 缓存导致的 502——两类故障都只能
   在运行时暴露，而 CI 至今没有 docker build、compose config 校验或健康探针冒烟中的任何一项。

### 维度评分对照

| 维度 | 首轮 | 二轮 | 三轮 | 四轮 | 变化主因 |
|---|:---:|:---:|:---:|:---:|---|
| 项目定位与演化 | 9 | 9 | 9 | **8.5** | README「ReAct 工具调用」宣称与实现断裂 |
| 分层架构与模块边界 | 7 | 8.5 | 8.5 | **8.5** | 无新结构性问题；runtime 回调解耦持续有效 |
| 多智能体交互与世界模拟 | 6 | 7 | 8 | **8.5** | agent-agent 多轮对话 + 关系质量化落地（战略项清偿） |
| 认知机制完备性 | 4.5 | 8 | 8 | **8** | 新特性批质量好；新增保留策略缺口与公式双写 |
| ReAct 工具调用 | 8 | 8.5 | 8.5 | **3** | **死代码实锤**：循环不可达，工具链整体休眠 |
| 数据持久化设计 | 8 | 8.5 | 7.5 | **7.5** | 核心扎实；文档漂移 ×7 + 备份短板 + 维度治理隐患 |
| 全链路可观测性 | 9 | 9.5 | 9 | **8** | 告警 receiver 为空、span 覆盖 3/17、预算绕过两处 |
| 部署与工程化 | 7.5 | 8 | 6.5 | **6.5** | 修复真实但 CI 验证深度仍为零；frontend 绑 0.0.0.0:80 |
| 前端工程化与 UX | 6.5 | 7.5 | 7.5 | **8** | a11y/toast/移动导航/IME 修复落地，32 页面完备 |
| 长期运行风险治理 | 3.5 | 7.5 | 7 | **7.5** | H9 分区保留/H10 失锁中止闭环验证通过 |
| **十维均值** | **6.9** | **8.1** | **7.85** | **7.4** | |
| 安全与隐私（本轮新增单列） | – | – | – | **4.5** | 匿名读 + 已认证伪造面，首次纳入评分 |

> 均值下降不是退步，而是**审查标尺从「语义层正确性」推进到「宣称↔实现↔安全边界」三层一致性**的结果。
> ReAct 死代码在前三轮以 8+ 分通过，是因为审查者（包括三轮）都默认「有代码=功能可用」；本轮把它拉回
> 3 分，恰恰说明项目需要的下一个杠杆是**端到端行为测试**，而不是更多单元断言。

---

## 二、三轮修复批核验：全部属实

对三轮报告 §附记所列 6 个修复提交，本轮按当前 HEAD 逐项抽查关键证据：

| 三轮问题 | 判定 | 本轮核验证据 |
|---|:---:|---|
| H1 日记世界时钟错位 | ✅ 已修 | diary_service.py:19-27 世界时钟窗口换算、:30-46 触发矩阵按世界日历幂等 |
| H2 幽灵反思计数 | ✅ 已修 | count_unreflected 过滤 is_duplicate + mark_duplicate 同步置位（双保险，含一致性测试） |
| H3 XACK no-op 二次投递 | ✅ 已修 | onebot.py:667 注释明确「必须用 remove()（XACK+XDEL）：该条目未经 XREADGROUP 投递」；流加 maxlen 裁剪 |
| H4 去重抢占时序 | ✅ 已修 | SETNX 移至回复发送前（onebot.py:878-886），失败路径清除去重键允许重放（:897-899, :922-929）；test_event_queue_semantics.py:344-377 锁定时序 |
| H5 自消息互撩 | ✅ 已修 | onebot.py:746-748 self_id 早退 + 问候层概率闸门 |
| H6 Redis 零认证/弱密码/0.0.0.0 | ✅ 已修 | compose:66 requirepass `${REDIS_PASSWORD:?}` + AOF；PG/Grafana 同改 `:?` 必填；基础设施端口全部 127.0.0.1 回环绑定 |
| H7 生产门禁永不武装 | ✅ 已修 | .env.example 补 `ENVIRONMENT=development` 与 CORS_ORIGINS |
| H8 Prometheus 抓取断链 | ✅ 已修 | prometheus.yml target 改 `backend:8000` |
| H9 分区只建不删 | ✅ 已修 | partition_scheduler.py:66-108 `drop_old_partitions` DETACH→DROP 两步走，保留期可配（action_records 12 月/state_history 6 月，config.py:51-52） |
| H10 失锁不中止 | ✅ 已修 | locks.py:167-187 lock_lost Event；tick.py:354-356、1204-1208、1283-1288 三处写入闸口自查中止 |

**结论：修复批执行力与诚实度延续前几轮水准，无虚报。** 但注意：这些修复的验证手段仍是单元/集成测试
与人工检查，见 §12 部署维度的元问题。

---

## 三、本轮新发现问题总览

### P0/P1（HIGH）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| R4-H1 | ReAct | **ReAct 工具调用循环是不可达死代码**：`_decide` 将不在候选列表的 action 强制改写为 `wait`（tick.py:827-832），而 `use_tool` 从未注册为 Action（actions/__init__.py 仅 move/life/work/social），循环守卫 tick.py:321 永远首轮 break。决策 schema 的 action 字段是无约束 string（tick.py:751），decision_tools.yaml:8 明确指示 LLM 输出 `"use_tool"`——指示必然落空。工具注册表、观察回注、`_apply_tool_deltas`、媒体工具全链休眠；tests/ 中 use_tool/react 零匹配。git 考古：校验逻辑 `27f2d20` 先于 ReAct 特性 `68ec290`，属出生缺陷 | tick.py:315-350, 827-832; actions/__init__.py; decision_tools.yaml:8 |
| R4-H2 | 安全 | **Person Memory / 记忆流 / 日记匿名可读**：AuthMiddleware 对 GET 放行 `/api/v1/characters` 与 `/api/v1/memories` 前缀（middleware.py:131-138），而 `GET /characters/{id}/person-memory?user_id=X`（读取角色对指定用户的偏好/互动史/情感连接）、`GET /characters/{id}/person-memory/list`（全体用户按热度倒序）、`GET /memories/{character_id}`（近期情节记忆，含用户对话衍生内容）、日记列表均无路由级鉴权（memory.py:114-139 及 get_memories 定义）。与 middleware.py:130 自述意图直接相悖 | middleware.py:130-138; memory.py:114,132; api/memory.py get_memories |
| R4-H3 | 消息 | **已认证用户可伪造任意 user_id 发言**：`POST /messages/send` 无 CurrentUser 依赖，user_id 取自请求体且不与 JWT sub 比对（messages.py:38-44）——全局中间件保证了「必须登录」，但登录后可以任何人身份写消息、污染任意用户的会话上下文与 Person Memory 归档 | messages.py:38-44; middleware.py:158-182 |
| R4-H4 | 部署 | **CI 零部署验证**：无镜像构建、无 `docker compose config` 校验、无运行时冒烟。`c387487` 自述后端容器此前从未成功启动（parents[N] 路径硬编码即崩）、前端镜像因 workspace context 不可构建；`900c73d` 再修 nginx 静态 DNS 502——三类故障均只能在运行时暴露，同类风险至今无门禁拦截 | ci.yml 全文; c387487/900c73d 提交信息 |
| R4-H5 | 部署 | **frontend 发布 0.0.0.0:80**：唯一未回环绑定的端口（compose:128），与其余全部服务 127.0.0.1 策略相悖；叠加 R4-H2 即构成公网匿名读取用户记忆的完整链路 | docker-compose.yml:128 |
| R4-H6 | 数据 | **备份策略与架构宣称不匹配**：pg_dump\|gzip 每 6h 落同主机 `./data/backups`，无 WAL 归档/PITR（RPO≤6h）、无异地副本、无恢复演练；Redis 作为实时真相源完全不在备份内（仅同机 AOF）；plain-SQL 格式无并行恢复、无 pg_dumpall 角色、无校验和 | backup.sh 全文; compose:134-157 |
| R4-H7 | 数据 | **EMBEDDING_DIM 以运行时环境变量治理 schema 维度**：ORM 声明 `HALFVEC(settings.embedding_dim)`（memory_episode.py:57、reflection.py:44），DDL 却硬编码 halfvec(2048)（0005:30、0015:32）——改 env 不配套迁移即在插入时报维度错配，配置非单一真相源 | memory_episode.py:57 vs alembic 0005:30 |

### P2（MEDIUM）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| R4-M1 | 数据 | data-model.md 七处漂移：`module_configs` 表文档存在但代码零实现（幽灵表）；`idx_plans_char_status`、`idx_refl_char_time` 文档声称存在实际从未创建（plans 每 Tick 全表扫）；person_memories 唯一键组成、world_events 唯一键、related_characters 类型、vector(1536) 均过期；character_state_history 与 person_memory_entries 两表反向缺失 | data-model.md:173,190,254,294,375-393,412,475,504 |
| R4-M2 | 可观测 | Alertmanager 默认 receiver 为空——11 条告警规则全部只进 UI，无人收到通知；docs 承诺飞书/email 渠道未实现 | alertmanager.yml:13-14; observability.md:301-307 |
| R4-M3 | 可观测 | 手动 span 仅 3 个（world.tick/character.tick/embedding.batch），docs 宣称 17 类；决策链内部（perceive/decide/execute/memorize）、消息处理、工具调用、Redis 全部无 span，「按 trace 重放 Tick」的工作流不成立 | observability.md:71-88 vs grep 结果 |
| R4-M4 | 可观测 | HTTP path 标签用原始路径（metrics.py:204-207），参数化路由 UUID 与 404 探测各自生成新序列——高基数风险 | metrics.py:204-207 |
| R4-M5 | 成本 | multimodal_chat 与 embed() 绕过 `_check_cost_control`/预算记录（client.py:330-408、164-200 无调用），embedding token 消耗既不入预算也不入指标——日预算账本系统性低估 | client.py:164-200, 330-408 |
| R4-M6 | 可观测 | Langfuse 不记录失败调用：trace_llm_call 仅挂在成功路径，except 分支只有指标+熔断计数；错误记录装饰器存在但从未接线（langfuse_integration.py:186-268 未使用）；版本钉 2.x 与 docs 宣称 3.x 漂移 | client.py:323-328,404-408; pyproject.toml:37 |
| R4-M7 | 认知 | reflections/diaries/person_memory_entries(compacted 行)/archive 行四类数据无任何保留策略——记忆治理把无界增长挪了科目而非消除 | loops.py:502-528 未覆盖上述表; person_memory_entry.py:5 |
| R4-M8 | 认知 | 混合检索评分公式 `(sim*0.6+imp*0.05)*(0.25+0.75*exp(-days/30))` 在 search_hybrid 与 search_hybrid_global 双处 SQL 硬编码——公式演进需同步两处否则不变量静默破坏 | memory_repo.py:456-463 vs :518-521 |
| R4-M9 | 决策 | structured_output 解析失败抛 RuntimeError 且无重试——一次畸形响应报废整个 Tick（仅计为批量失败） | client.py:661-664; loops.py:109-115 |
| R4-M10 | 决策 | move 的 target_scene 参数契约未进 Prompt（候选文本只展示 id/name/duration/energy，社交提示只覆盖 chat_with）——move 决策靠 LLM 猜字段名，缺参 fail-safe 到 wait 白白浪费决策 | tick.py:684-686; decision.yaml:49-59 |
| R4-M11 | 决策 | 工具关系增量即时写 PG（tick.py:1007-1037）在主事务之外——后续 Action 事务失败时关系变更残留（部分提交窗口） | tick.py:1007-1037 vs :1210-1281 |
| R4-M12 | 消息 | Web WS 发送无超时/背压（半开浏览器连接可无限挂起 send_json），与 OneBot 侧 10s 超时+驱逐+故障转移形成成熟度断层；分享扇出用裸 create_task 无强引用注册（GC 丢失风险），与 MessageService 的 spawn_background 模式不一致 | websocket.py:139-141,544-549; proactive_sharing.py:493-494 |
| R4-M13 | 消息 | 分享扇出一次 commit 收尾：中途崩溃丢失全部 message 行而 QQ/WS 推送可能已发出——无投递台账/重试 | proactive_sharing.py:444-505,607-608 |
| R4-M14 | 消息 | 群聊上下文割裂：群消息按发送者建独立会话，其他成员消息不进角色上下文——多方对话答非所问的结构性根源 | onebot.py:832-849 |
| R4-M15 | 认知 | 反思 embedding 失败永久 NULL（无重试通道），与 episode 的退避重试体系不对称；日记固定取最近 20 条记忆，「年记」与「日报」素材深度相同 | reflection_service.py:206-220; diary_service.py:143-147 |

### P3（LOW，择要）

| # | 问题 | 证据 |
|---|------|------|
| R4-L1 | chat_with.yaml 在 REQUIRED_TEMPLATES 中但从未渲染——fail-fast 保护着一个没人用的模板 | prompts.py:22; grep render("chat_with") 零命中 |
| R4-M16→L | 群活动对陌生人两两 +2 关系无质量门控，反复同场即机械刷好感 | group_activity_service.py:85-110 |
| R4-L3 | chat_quality 评审与对话生成共用同一 chat 模型——自评相关性偏差 | tick.py:1636 |
| R4-L4 | duration 字段无上限钳制（依赖 allow_dynamic_duration 门控，当前无 Action 启用——潜伏） | tick.py:754,1167 |
| R4-L5 | rec_ver 基线键删除角色时不清理；Jaeger all-in-one 无卷重启丢 trace；compose 无任何资源限额 | character_repo.py:183-184; compose:208-221 |
| R4-L6 | vitest 全局 environment:'node' 注释称 DOM 测试待 jsdom，但 jsdom 已装且存在两个 .test.tsx——配置与注释漂移 | vite.config.ts:39-42; package.json:38 |
| R4-L7 | 登录页展示默认凭据提示 + .env.example 附带 admin123——开发便利与生产习惯的边界未文档化强制 | login.tsx:110-118; .env.example:114 |
| R4-L8 | WS JWT 走 URL query param——访问日志泄漏面 | useDashboardSocket.ts:76 |

---

## 四、分维度详评

### 4.1 项目定位与差异化（8.5）

「世界驱动的陪伴」定位继续成立且护城河加深：多轮 agent-agent 对话让角色间关系第一次由**对话内容**
而非固定公式驱动；反思向量检索让「想起自己曾经的感悟」成为可能。但 README 特性表第 5 行「ReAct 工具
调用：LLM 决策→执行工具→观察结果→再次决策」当前与代码事实断裂（R4-H1）——这是三轮建立起来的
「宣称↔实现零失配」纪录的首次破功。定位本身无需调整，需要的是把这条宣称修活或诚实降级。

### 4.2 分层架构与模块边界（8.5）

main.py 组装层 + runtime 回调解耦的格局经受住了认知特性批五个 feature 的考验：cognition 注入走
messaging 服务层、跨角色检索收在 admin API、反思向量挂在 memory 服务——新增能力全部落在既有分层
缝隙里，无一例跨层直调。两点保留意见：(a) tick.py 已达 1941 行，`_perceive`（230+ 行装配函数）与
chat_with/group_activity 两大特化处理器继续内联会让核心引擎变成「上帝文件」，建议下一批重构拆出
PerceptionBuilder 与 SocialActionHandler；(b) PersonMemoryService/DiaryService 依赖注入用 Any 类型，
与 EpisodeService 的具体类型风格分裂（person_memory_service.py:30、diary_service.py:80），新机制复制
哪套风格全凭运气。

### 4.3 多智能体交互与世界模拟（8.5）

本轮最大加分项：**agent-agent 多轮对话落地**（chat_with 最多 `CHAT_WITH_MAX_ROUNDS` 轮、硬上限 3，
每轮双方各一句、transcript 尾窗 800 字符控 token，tick.py:1426,155,1584-1590）+ **关系质量化**
（LLM 结构化评审对话产出 [-10,+10] 关系增量钳制，替代固定 +5/+2，chat_quality.yaml 评审 rubric +
fallback 保底，tick.py:1474-1491,1609-1641）。三轮遗留的两个战略项就此清偿一个半（QQ 免打扰仍欠）。
剩余差距：群聊智能回复仍是单层启发式+LLM 评审、群内多方上下文割裂（R4-M14）、关系类型升级阈值
(20/40/70/90) 仍是静态公式。

### 4.4 认知机制完备性与可演化性（8）

**设计完备度维持本项目历史高位**：经验 → 情节记忆（写时精确去重 + 向量化改写去重 + LLM/rule 双轨
重要性评分）→ 阈值触发两层反思（编号溯源 + tier-2 元反思 + 反思向量化）→ 检索三角（recency×importance×
relevance + 时钟回拨钳制）→ 日记/压缩归档 → 分级删除，闭环完整且占位符逐一核对零失配。新特性批四件
（反思向量、认知注入聊天、跨角色全局检索、多轮对话记忆沉淀）全部按既有扩展模式（service + tick 钩子 +
lifespan 接线）零手术接入——可演化性的正面证明。

**本轮明确的四个演化债**：
1. importance 写时冻结，无 last_accessed/retrieval-count 通道——被频繁回忆的记忆与从未被召回者按同一
   曲线衰减，遗忘纯靠时间+分数；
2. 情节流无矛盾处理（改写去重只删近重复，「喜欢X」与「讨厌X」并存共检），矛盾消解只存在于 PM 压缩
   prompt 一隅；
3. 规划仍是软引导（plan.py:4-6 自述契约）：createPlanChanges 机会式创建 + TTL 过期 + 世界事件重规划
   提示，无专职 planner、progress 字段无人更新、无 deadline 感知排序；
4. 情绪影响浅表：mood 进检索 query 与评分输入，但 MemoryEpisode 无 mood 列——经历的情感着色只在
   LLM 恰好写进 content 时幸存。

Prompt 工程质量：八个认知 prompt 全部外置 YAML 合规，reflection 的编号溯源+服务端校验钳制是全栈最强
模式；但 person_memory/memory_compress 走手写括号剥离 JSON 解析而兄弟模块用 structured_output——两套
解析范式并存，且较弱的一套恰用在永久档案的追加路径上。

### 4.5 ReAct 工具调用（3）

除 R4-H1 死代码这一根本问题外，工具子系统本身的**潜在设计质量其实不低**：服务端注入 money/inventory/
relation 参数防 LLM 伪造（registry.py:405-419）、状态变更类工具返回 delta 由执行层统一应用并有「零 Redis
直写」回归测试（test_tick_tool_deltas.py:50-54）、工具开关热生效带 5s TTL 缓存。但不可达就是不可达：
- 修复本身是一行豁免 + 候选外 action 白名单（如 `if action_id == "use_tool": pass`），真正的成本在补齐
  端到端集成测试（stub LLM 返回 use_tool → 断言工具执行 → 断言观察回注 → 断言最终 action 落地）；
- 复活前还需补：per-tool JSON schema 校验（当前 args 是自由 dict）、重复调用环检测（现仅 3 轮硬上限）、
  媒体工具占用 Tick 槽位最长 ~10 分钟的并发预算评估（media.py:48-50）。

**给项目的诚实建议**：若短期不打算修活，应将 README 该行降级为「规划中」，并删除 decision_tools.yaml
的注入——让 LLM 持续输出注定被丢弃的 use_tool 是纯粹的 token 浪费与行为噪声。

### 4.6 数据持久化设计（7.5）

核心盘依旧扎实：HASH 分区裁剪 + 双表 halfvec(2048) HNSW（m=16/ef_construction=128，查询期 ef_search
可配）+ PG-first-then-Redis 双写 + 版本感知 reconcile 仲裁（方向翻转 + 写前新鲜度复检）+ 失锁三闸口，
这套组合在同类项目中属于少见严谨。分区生命周期（DETACH→DROP 两步、default 分区残留行同周期直删）
是三轮以来最佳工程细节的延续。

扣分集中在三处：
1. **文档↔schema 七处漂移**（R4-M1），其中「文档声称存在的索引实际从未创建」（plans/reflections）说明
   文档-迁移一致性没有 CI 守卫；
2. **备份短板**（R4-H6）：架构把 PG 当审计/恢复骨架、Redis 当实时真相源，但前者 RPO≤6h 且同机存放，
   后者干脆不备份——与 ADR-0001 的自我定位不匹配；
3. **维度治理**（R4-H7）：embedding 维度由 env 治理而非迁移链。

另记两条正向细节：fetch_unmaterialized 的 FOR UPDATE SKIP LOCKED worker 队列 + 指数退避 + fail_count≥5
熔断是标准作业；tick 感知阶段对只读 session 显式 rollback 提前归还连接（tick.py:516,537,550…）是少见的
连接池礼貌。

### 4.7 全链路可观测性（8）

真实覆盖：Tick 根 trace + Langfuse ContextVar 父子 generation 树、DB_QUERY_DURATION 真实埋点、11 条
告警规则命名与指标一一对应、structlog JSON → 文件 → Alloy → Loki 管道端到端可用、Grafana/Jaeger 双向
跳转链接配置正确、前端 metrics/monitoring 双页原生消费 admin 端点。CircuitBreakerStuckSuspect 用「零
LLM 调用 15 分钟 AND 有活跃角色」复合推断熔断卡死，是被观测对象自身无指标时的聪明补偿。

但「埋点即契约」的项目原则当前只兑现了一半：
- **告警无人接收**（receiver 空，R4-M2）——整套告警体系的最后一公里缺失，实战价值归零；
- span 覆盖 3/17（R4-M3），决策链内部黑盒；
- 采样默认 0.5 头部采样使未采样请求日志无 trace_id——Logs→Trace 跳转对一半流量死亡；
- 预算绕过两处 + Langfuse 不记失败（R4-M5/M6）；
- HTTP path 高基数（R4-M4）在角色导入功能上线后会加速恶化。

### 4.8 部署与工程化（6.5）

**好的方面**：compose 密钥全部 `:?` fail-fast、基础设施端口回环绑定、healthcheck 带 NOAUTH 防伪、
x-default-logging 日志轮转锚点、双 Dockerfile 教科书式多阶段（非 root、锁文件层缓存、清华镜像加速、
nginx 非 root pid/tmp 重定向）、CI 后端跑真 PG18+Redis 集成测试、前端 OpenAPI 契约守卫是同类项目罕见
的好门禁。

**硬伤**：R4-H4/H5——验证深度停留在「代码质量门禁 + 集成测试」，部署正确性完全靠人肉；frontend 绑
0.0.0.0:80 与全局回环策略相悖；alembic 内嵌 CMD 阻塞横向扩展且失败即 crash-loop；无资源限额；Jaeger
内存存储无卷。今天两个 hotfix 证明了这类故障的真实发生频率——**下一个 CI job 应该就是
`docker build × 2 + compose config -q + 起栈 curl /health 冒烟`**，预计一天工作量，能关闭整整一类事故。

### 4.9 前端工程化与 UX（8）

32 个路由页面覆盖运营全景（总览/角色/对话/记忆/向量检索/反思/计划/关系/PM/日记/成本/监控/QQ 监视/
通知/分享/动作/设置/导入导出），三轮的移动导航断裂、IME 误发、WS 重试不清零、静默失败族全部确认修复
（汉堡菜单 aria-expanded、isComposing 检查、指数退避成功重置、toast aria-live + 破坏性操作确认）。
架构面：TanStack Query 承载全部服务端状态 + Zustand 仅剩 auth/toast 的干净二分、queryKeys 集中契约、
openapi-typescript 生成类型 + CI 契约守卫、React Compiler 真实启用。

剩余欠账：聊天区 256px 小窗 limit-20 无分页/虚拟化；adminStatus/logs/metrics/onebot 四处残余高频轮询
（5-10s）；无路由级 error/not-found 组件；i18n 缺位；localStorage token 的 XSS 常规权衡未文档化。

### 4.10 消息服务与多端触达（7.5）

OneBot 反向 WS 接入的成熟度显著高于 Web 侧：token 鉴权（常时比较）、自消息早退、@检测三层降级、
多段回复拟人节奏、retcode 回包解析与指标、跨连接故障转移、10s 发送超时+死连接驱逐、持久化优先队列
（XADD→inline→XACK+XDEL）+ PEL 恢复循环 + DLQ 五次投递上限——崩溃语义经测试锁定。主动分享的概率
触发矩阵 + 双冷却（1800s/日 8 条）+ 平台扇出设计合理。

不对称与缺口：Web WS 无超时背压（R4-M12）、分享扇出无台账（R4-M13）、群聊上下文割裂（R4-M14）、
OneBot access_token 可选配置（未设则任意客户端可伪造事件接入）、QQ 入站无速率限制（仅预算熔断兜底）、
跨渠道身份不合并（web user_xxx 与 qq_yyy 是两个人）。

### 4.11 长期运行风险治理（7.5）

并发域收尾良好：失锁三闸口（R4-H10 核验）、move CAS 结果检查、reconcile redis_to_pg 走 update_state
保版本单调、admin 双端点接 leader 围栏——三轮 M8-M11 清单全部落实。记忆膨胀主战场（episodes 分级
保留 + 压缩归档）闭环运转，action_records/state_history 分区按月丢弃。

剩余无界清单（R4-M7）：reflections（稀疏但严格递增）、diaries（每角色每世界日至少一行）、PM compacted
条目（软归档永不清理）、archive 行（豁免一切删除路径）。量级都不大，但「长期运行记忆不膨胀」的 README
宣称对这四类并不成立。建议统一挂进现有 memory_retention_loop 的分级框架即可。

### 4.12 用户体验专项

QQ 端：三层回复决策 + 多段节奏 + 主动分享底座良好；免打扰时段缺位（世界时间的早安可能落在现实凌晨
3 点）与按用户节流缺位仍是两个最影响真实体验的战略欠账。Web 端：信息架构完整、加载/错误/空态纪律
执行到位、a11y 高于社区平均；痛点集中在聊天体验（小窗、无历史翻页）与运维页面的轮询功耗。

---

## 五、技术选型评价

| 选型 | 评价 |
|------|------|
| LangChain 1.x | ✅ 维持三轮结论：使用面收敛于 client/fallback 两文件，替换无功能收益；触发重评条件不变 |
| PostgreSQL 18 + pgvector(halfvec/HNSW) | ✅ 正确；分区生命周期已闭环；维度治理需从 env 移交迁移链 |
| Redis 8（锁/Streams/状态/预算） | ✅ 用法克制；Streams 语义修正后已达设计意图；建议补 maxmemory 告警（noeviction 下 OOM 即真相源写入失败） |
| OneBot v11/v12 反向 WS | ✅ 架构正确；access_token 应改必填或文档强制 |
| React 19 + React Compiler + TanStack + openapi-typescript | ✅ 自洽且契约守卫优秀 |
| OTel + Langfuse + LGTM 栈 | ⚠️ 组件齐全但「最后一公里」普遍缺失（receiver/span/采样联动）；建议先接通 receiver 再谈扩面 |

---

## 六、修复路线图（优先级排序）

### 立即（P0/P1，预计 4-5 人日）

1. **R4-H1 ReAct 复活或诚实降级**：推荐复活——`_decide` 校验豁免 `use_tool`（一行）+ stub-LLM 端到端
   集成测试（返回 use_tool → 断言工具执行/观察回注/最终落地）+ per-tool args schema 校验；若暂缓，
   README 降级该特性行并停注 decision_tools.yaml；
2. **R4-H2 隐私边界修复**：PUBLIC_GET_PREFIXES 收窄为精确路由白名单（world/actions/town/scenes 保持
   公开），person-memory/memories/diaries 全部要求登录 + 归属校验；补一条「匿名 GET 不得触达用户衍生
   内容」的中间件测试；
3. **R4-H3 send 接口身份绑定**：CurrentUser 注入 + body.user_id 必须等于 JWT sub（internal platform
   豁免走 API Key 专用头）；
4. **R4-H4 CI deploy-smoke job**：`docker build` 双镜像 + `docker compose config -q` + 起核心栈 curl
   `/health`（经 nginx）冒烟——关闭 c387487/900c73d 两类事故的复发通道；
5. **R4-H5 frontend 端口回环绑定**（或 profile 化）；
6. **R4-H6 备份补课**：pg_dump 改 `-Fc` + 异地/异卷副本 + 首次恢复演练入 CHANGELOG；Redis AOF 目录
   纳入备份范围；
7. **R4-H7 EMBEDDING_DIM 迁移链化**：ORM 固定 HALFVEC(2048)，env 仅作启动校验（atttypmod 比对
   fail-fast）。

### 两周内（P2）

8. R4-M2 Alertmanager 接真实 receiver（哪怕先是 webhook→QQ 机器人）；R4-M4 path 标签模板化；
   R4-M5 multimodal/embed 接预算与指标；R4-M6 Langfuse 失败调用接线；
9. R4-M1 data-model.md 七处漂移校正 + 幽灵索引迁移补建（plans/reflections 各一条）；
10. R4-M7 四类无界数据挂入 retention 分级框架；R4-M8 评分公式收敛单一来源（SQL 片段常量或 DB 函数）;
11. R4-M9 structured_output 失败重试一次；R4-M10 move 参数契约进 Prompt；
12. R4-M12/M13 Web WS 发送超时 + 分享扇出 spawn_background 化与投递台账；
13. R4-M14 群聊共享上下文（group 会话窗口聚合最近 N 条群消息）设计评审后实施。

### 战略级

14. QQ 免打扰时段 + 按用户/群节流（三轮遗留，连续两轮未动）；
15. 跨渠道身份合并（web/qq 同一自然人归档到同一 Person Memory 主档）；
16. 认知演化债四项：importance rehearsal 信号、情节矛盾消解、planner 一等化、episode mood 列；
17. 「无界增长巡检」指标族：stream length、分区行数、compacted/archive 行数纳入 Prometheus + 告警。

---

## 七、总评

第四轮审查的发现模式发生了质变：**前三轮的问题是「写了但没写对」，本轮的问题是「对了但没接上」**——
ReAct 的每个零件都合格，唯独装配线上少了一行豁免；隐私中间件的意图写在注释里，却被自己的前缀列表
推翻；告警规则十一条条条在理，最后一步的 receiver 是空的。这类「最后一公里断裂」比算法 bug 更值得
警惕，因为它全部位于**跨组件接缝**上，恰好是单元测试与单文件审查的盲区。

同时必须公正记录：三轮修复批 10/10 属实、认知特性批 5 项战略能力全部真实落地、门禁实测全绿
（ruff/format/mypy 140 文件零错误/pytest 单测 454 通过/前端 lint+typecheck 通过）、180 commits 七周的
演进速度下文档-代码同步率依然可观。这个项目的工程文化——CHANGELOG 与代码零失配、ADR 沉淀、
fail-fast 强迫症——在同类项目中处于第一梯队。

**四轮总评：7.4 / 10（十维）；安全与隐私单列 4.5。**
下一个杠杆点已经从「不变量的测试化」升级为「**接缝的端到端验证化**」：ReAct 一条链、鉴权一张网、
部署一根管——把三条接缝各自补上一个端到端断言，项目就能从「功能基本盘扎实」迈入「宣称可信」。

---

## 附：本轮审查方法与覆盖声明

- 六路并行深审各产出独立引用报告（认知机制 21 机制清单 / ReAct 十步决策流 / 消息双通道全 hop /
  16 表 ERD+索引审计 / 六组件×三信号覆盖矩阵 / 11 服务编排+32 页面清单），关键 HIGH 结论
  （R4-H1/H2/H3）由主审在源码二次亲读确认后才定性；
- 三轮 H1-H10 修复逐项抽查通过；质量门禁在 HEAD `900c73d` 本地实测（结果见 §七）；
- 未覆盖声明：LLM 实际生成质量（需真实 key 的长程观察）、Windows 宿主外的 OS 兼容性、
  多实例水平扩展演练（当前架构单机假设明确）、QQ 真实账号端到端（需 NapCat 环境）。
