# AI Town 全面复审报告·第三轮（2026-08-25，HEAD `1e8cc86`）

> **文档定位**：对[首轮报告](project-review-20260824.md)（基线 `dabecc9`）与[二轮复核](project-review-20260824-round2.md)
> （基线 `0da5e79`）之后全部整改批（`0da5e79..1e8cc86`，20 commits）的**全面重审**。本轮不是逐项核对，
> 而是按用户要求对十大维度重新独立审查：六路并行深审（数据层/认知闭环/并发一致性/消息服务/部署安全/前端 UX）
> + 核心链路亲读 + 全量质量门禁本地实测。所有结论基于当前工作区源码直接阅读，关键结论附 file:line 证据。
>
> **严重度定义**（沿用前两轮）：P0=核心功能静默失效/发布阻断；P1=功能性 bug/承诺违约/结构性缺陷；
> P2=纵深防御缺口/文档漂移/性能隐患；P3=瑕疵。

---

## 一、执行摘要

**一句话结论：战略项几乎全部落地且质量真实——传闻传播、群活动、主题化+元反思、记忆压缩归档、Person Memory
两层结构、冷启动演练、备份服务全部有代码与测试实证；但深挖发现了前两轮未触及的四类新裂缝：①日记系统被
「世界时钟 20 倍速」击穿（幂等键用现实日期、记忆窗口用现实 timedelta）；②入站消息队列的 XACK 语义错误
使每条消息被处理两次、正确性完全悬在 600s 去重 TTL 上；③部署面 Redis 零认证 + PG 弱默认密码绑定
0.0.0.0、生产密钥门禁因 `.env.example` 缺 `ENVIRONMENT` 而永不武装；④RANGE 分区表只建不删，
action_records/state_history 以 ~175 万行/年速度无界膨胀。**

本轮最重要的三个判断：

1. **认知闭环的「设计」已经完整**——反思（批次主题化 + tier-2 元反思跨期归纳）、日记、Person Memory
   （append-only 条目 + 后台压缩主档）三条产物全部回流决策/对话 Prompt，占位符逐一核对零失配。
   但「实现」存在两个 HIGH 级缺陷：日记节奏被时钟错位击穿（N3-A）、改写式去重的幽灵计数让反思每 Tick
   触发（N3-B）。设计分与实现分需要分开给。
2. **消息队列的「持久化」是名义上的**——`XACK` 对从未投递的条目是 no-op，内联路径 ack 无效，
   每条消息都会被恢复循环二次投递；去重 SETNX 在处理开始时抢占，崩溃后重放被去重挡住 → 消息静默丢失。
   「崩溃不丢消息」的宣称当前只在特定崩溃窗口内成立。
3. **部署安全出现回退**——二轮修复了凭据参数化，但本轮发现：Redis 完全无密码且端口发布到 0.0.0.0、
   PG/Grafana 密码默认值兜底（`:?` 必填语法未用）、`.env.example` 缺 `ENVIRONMENT` 使生产门禁形同虚设、
   Prometheus 抓取目标指向 `host.docker.internal` 而非 `backend:8000`（Linux 上整个告警栈失明）。

### 维度评分对照

| 维度 | 首轮 | 二轮 | 三轮 | 变化主因 |
|---|:---:|:---:|:---:|---|
| 项目定位与演化 | 9 | 9 | **9** | 定位未变；README 特性宣称与代码事实首次完全对齐 |
| 分层架构与模块边界 | 7 | 8.5 | **8.5** | 无新结构性问题；runtime 回调/循环下沉持续有效 |
| 多智能体交互与世界模拟 | 6 | 7 | **8** | 传闻传播+群活动落地、related_characters 激活、世界事件中断重规划 |
| 认知机制完备性 | 4.5 | 8 | **8** | 设计满分（元反思/两层PM/压缩归档），扣日记时钟错位与幽灵计数两个实现缺陷 |
| ReAct 工具调用 | 8 | 8.5 | **8.5** | 媒体工具链补全；新发现媒体成本不入账、群判模型漂移 |
| 数据持久化设计 | 8 | 8.5 | **7.5** | 压缩归档落地加分；但分区只建不删 + Redis 流无界是新发现的账单 |
| 全链路可观测性 | 9 | 9.5 | **9** | Langfuse 父子 trace 树落地；Prometheus 抓取目标配置错误拉低实战价值 |
| 部署与工程化 | 7.5 | 8 | **6.5** | 备份服务/日志轮转/演练脚本加分；Redis/PG 暴露 + 死门禁 + 抓取断链是硬伤 |
| 前端工程化与 UX | 6.5 | 7.5 | **7.5** | 测试增至 38 用例、类型收敛完成；移动导航断裂/IME bug/WS 重试缺陷 |
| 长期运行风险治理 | 3.5 | 7.5 | **7** | 记忆压缩归档闭环；但流/分区/消息三处无界增长是新账单 |
| **均值** | **6.9** | **8.1** | **7.85** | |

> 均值较二轮微降的原因不是退步，而是**审查深度增加**：本轮六路深审覆盖了前两轮未下钻的消息队列语义、
> 时钟体系一致性、分区生命周期、部署暴露面。问题从「已知清单」变成了「全量清单」，这是好事。

---

## 二、整改批核实：战略项清偿情况

二轮 §五列出的战略级清单，本轮逐一以代码证据核实：

| # | 战略项 | 判定 | 证据 |
|---|---|:---:|---|
| 7 | 群体动力学实验 | ✅ 已落地 | `GossipService`（gossip_service.py，好友显著经历第二手传播，importance 减半递减，5 个配置项）；`group_activity` Action（同场景 ≥3 人临时小聚，单次 LLM 集体叙事，related_characters 互指 + 两两关系 +2）；传闻注入决策 Prompt 作社交话题（tick.py `_perceive` fetch_recent_gossip）；测试 test_gossip_service.py / test_gossip_it.py / test_group_activity_it.py |
| 8 | reflection 跨期主题归纳 | ✅ 已落地 | 批次反思升级为多主题结构化输出（memory_ids 编号溯源 + ReflectionSource 外键 + ID 收敛去重，reflection_service.py:69-142）；tier-2 元反思跨期归纳（7 天冷却 + 最少 6 条门槛，:144-202） |
| 9 | 低价值记忆压缩归档 | ✅ 已落地 | retention 两阶段：按角色×月份 LLM 压缩为 `[归档]` 行（source_type='archive' 豁免再删除），压缩失败整组保留绝不先删；小组直删（loops.py:579-704）；test_retention_compression_it.py |
| 10 | LangChain 依赖去留评估 | ✅ 本轮给出结论 | 见 §十一：**建议保留**，理由附后 |
| 11 | Redis 清空冷启动恢复演练 | ✅ 已落地 | `scripts/cold_start_drill.py`：清空 Redis 后执行与启动路径一致的 rehydrate_states()，校验 tick_id/weather 回滚与角色状态字段；CHANGELOG 记录实测 5/5 通过 |

二轮遗留项核实：

| 项 | 判定 | 说明 |
|---|:---:|---|
| N7 改写式重复去重 | ✅ 已修 | `find_paraphrase_duplicate`（memory_repo.py:130-168）：embedding worker 落向量后同角色 24h 窗口余弦比对 ≥0.95 判重，命中打 `is_duplicate` 不落向量；pg_trgm 中文无效的教训已写入 docstring |
| N5 PM 单槽全文重写 | ✅ 已修 | 新增 `person_memory_entries` append-only 事实条目层（0011 迁移），交互时 LLM 只抽取增量事实追加；后台每 6h 按 `PERSON_MEMORY_COMPACT_THRESHOLD` 将条目合并进主档（loops.py:381-453）；对话上下文 = 主档 + 未压缩条目 |
| 二轮「立即项」1-3 | ✅ 均已修 | 探针 SELECT 1 握手 / 凭据参数化 / toggle 缓存失效，上轮附录已记录 |

**结论：整改批的执行力和诚实度延续了两轮以来的水准——CHANGELOG 与代码事实一致，无虚报。**

---

## 三、本轮新发现问题总览

### P0/P1（HIGH）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| H1 | 认知 | **日记系统被世界时钟击穿**：世界时钟 30s 现实 = 10 分钟虚拟（≈1 现实天 = 20 世界天），但日记幂等键用**现实日期**（`diary_date::date = now(UTC)::date`）、记忆窗口用**现实 timedelta**——一天最多产出 1 篇日报（应约 20 篇），「今天」日记实际汇总 20 个世界日的记忆，「这一周」跨度 140 世界日；决策注入的 `[最近日记]` 数小时内即陈旧 | diary_service.py:189,207,30-35 vs loops.py:186-204, config.py:113-114 |
| H2 | 认知 | **幽灵未反思计数使反思每 Tick 触发**：`count_unreflected` 不过滤 `is_duplicate`，而 `fetch_unreflected` 过滤、`mark_duplicate` 又不置 `is_reflected`——改写式重复永久滞留计数器；累计 ≥20 后 check_and_reflect 每 Tick 跑一次 LLM 反思，主题池仅 1-2 条，违反「跨越至少两条记忆」约束 | memory_repo.py:99-110 vs :112-128,:170-179; embedding_worker.py:130 |
| H3 | 消息 | **XACK 对未投递条目是 no-op → 每条消息处理两次**：内联路径 enqueue→处理→ack，但条目从未经 XREADGROUP 投递，ack 无效；恢复循环 15s 后经 `>` 二次投递，防重复完全依赖 600s SETNX TTL——积压超时或 Redis 重启即产生**重复回复 + 重复落库**；且流无 maxlen/XTRIM，永久增长 | onebot.py:558-562,643; event_queue.py:54,91 |
| H4 | 消息 | **去重抢占时序使崩溃恢复变成静默丢消息**：SETNX 在处理开始抢占，若进程在「已去重、未回复」窗口崩溃，重放被去重挡住后照常 ack——用户消息永久无回复，恰与队列「崩溃不丢」的设计目的相反 | onebot.py:643 + event_queue.py:124-135 |
| H5 | 消息 | **无自消息排除 → 机器人互撩死循环风险**：全库无 `user_id == self_id` 检查；称呼命中/问候关键词层零概率门控——任何回显自身消息的实现或第二个关键词机器人都会形成无限对答 | onebot.py:614-618; service.py:188-195 |
| H6 | 部署 | **Redis 零认证 + PG 弱默认密码 + 全部端口绑 0.0.0.0**：Redis 无 requirepass 且 6379 发布公网；PG `${POSTGRES_PASSWORD:-password}` 兜底默认值（未用 `:?` 必填语法）同样喂给 DATABASE_URL 与备份容器；Grafana `-admin123` 同类；startup_checks 生产门禁只覆盖 backend 进程内三个变量 | docker-compose.yml:36,42-43,52-57,87,130,236,246 |
| H7 | 部署 | **生产密钥门禁永不武装**：`.env.example` 缺 `ENVIRONMENT`（config 默认 development），照模板配置的用户永远触发不了 `check_default_secrets()` fail-fast | .env.example 全文 vs config.py:75, startup_checks.py:31-36, main.py:130 |
| H8 | 部署 | **Prometheus 抓取目标错误 → Linux 上告警栈全盲**：target 写死 `host.docker.internal:8000`，backend 实为 aitown-net 内容器；Linux 无 extra_hosts 配置必然解析失败，alerts.yml 全部 ai_town_* 告警失效 | prometheus.yml:26 vs compose:146-161 |
| H9 | 数据 | **RANGE 分区表只建不删**：partition_scheduler 仅预创建未来分区，全库无任何 drop/detach 路径——action_records 与 character_state_history 各 ~87.6 万行/年（5 角色 × 每 3 分钟一动作）无限累积，正是选择 RANGE 分区想避免的局面 | partition_scheduler.py:34-48; 0001_init.py:82-89; 0007:43-46 |
| H10 | 并发 | **看门狗续租失败不中止 Tick**：renew_lock 失败仅记 warning 日志，docstring 明言「持有者应立即停止受锁保护的工作」但无任何调用方实现——锁丢失后 Tick 继续写状态，跨实例双 Tick 窗口存在 | locks.py:156-159; tick.py:196-198 |

### P2（MEDIUM）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| M1 | 数据 | messages 表无界增长（刻意不分区却无保留任务） | 0003:72-91; loops.py 仅 memory+world 两周期 |
| M2 | 数据 | EMBEDDING_DIM 与物理列 halfvec(2048) 无启动校验，错配表现为 worker/检索运行时报错而非启动失败 | config.py:42 注释而已; memory_episode.py:57 |
| M3 | 数据 | find_paraphrase_duplicate 用距离过滤无 ORDER BY，HNSW 无法加速（pgvector 只加速有序 Top-K） | memory_repo.py:150-167 |
| M4 | 数据 | world_events 保留 DELETE 按 created_at 过滤但该列无单列索引 → 每日全表扫描 | loops.py:551 vs 0002:217-218 |
| M5 | 数据 | ORM 元数据漂移：ActionRecord 单列 PK vs 物理复合 PK；MemoryEpisode 缺 idx_mem_related 声明；ActionRecord docstring 仍称存在已被 0002 删除的 default 分区——autogenerate 地雷 | action_record.py:28,33; memory_episode.py:80-97 |
| M6 | 认知 | preferences 整体替换非合并：LLM 抽取返回非空 dict 即覆盖整个 JSONB，历史偏好键静默丢失，违背模块自述「增量合并语义」 | person_memory_service.py:198-203 |
| M7 | 认知 | 决策 Prompt 无聚合 token 预算：reflections（5×不限长）与 memories（10×全文）是仅有的两个未截断注入源，叠加最坏情形无上限 | tick.py:464,625-629 |
| M8 | 并发 | API move 的 CAS 结果被忽略（失败仍写 Redis）+ PG 提交到 Redis hset 之间无版本护栏 → 双向 last-write-wins 倒挂窗口 | characters.py:294-309 |
| M9 | 并发 | reconcile 快照一次全程使用：pg_to_redis 修复可覆盖快照后落入的新写；redis_to_pg 修复绕过 update_state 不增 version，破坏「每次写必增版本」不变量 | reconcile.py:101,138,152-153 |
| M10 | 并发 | admin force_world_tick 只查内存 is_leader 旗标（最长 10s 陈旧）不走 _is_still_leader 围栏；reset_world_time 完全不咨询 leader | admin.py:163-171,206-231 |
| M11 | 并发 | importer 直改 ORM 属性不增 version 不加锁，破坏 pg_advanced 仲裁前提 | importer.py:156-208 |
| M12 | 消息 | 动作响应（retcode）从不读取：发送结果回包落入 unknown_event 分支，push_share 故障转移只能感知 ws 级错误，QQ 层发送失败不可检测 | onebot.py:595-596,995-1003 |
| M13 | 消息 | 回复路径无跨连接故障转移（push_share 有）：LLM 秒级延迟期间连接重建 → RuntimeError 吞掉 → 回复丢失 | onebot.py:777-783,1004-1011 |
| M14 | 消息 | 恢复重放绑定任意 `_any_ws()`：多账号接入时重放回复可能从错误账号发出 | onebot.py:429,447-453 |
| M15 | 消息 | 群聊判模型漂移：README/config 宣称 MODEL_FLASH，structured_output 强制 chat 模型——每条非 @ 群消息都可能跑满血 chat 模型 | client.py:605,620-621; service.py:230-241 |
| M16 | 消息 | `chat()` 绕过预算检查与熔断器（仅 chat_with_usage/structured_output_with_usage 有 _check_cost_control），主动分享文案生成与上下文压缩恰好走此路径 | client.py:223-236; proactive_sharing.py:385,429; service.py:720 |
| M17 | 消息 | 心跳仅记日志：无 last-seen 追踪/陈旧连接驱逐/发送超时（send_text 无 wait_for），半开连接拖住 push_share | onebot.py:792-811,994 |
| M18 | 消息 | 媒体 URL 升级要求文件扩展名：签名式无扩展名生成链接降级为裸文本；视频轮询上限 10 分钟（120×5s）与 media.py 文档「1-3 分钟」不符；图片/视频生成成本不入预算账 | onebot.py:272-289; client.py:39-41 vs media.py:36 |
| M19 | 前端 | WS 重试计数成功后不清零：无 onopen 处理器，抖动 9 次后再断线只剩 1 次机会然后永久放弃 | useDashboardSocket.ts:39,88-95 |
| M20 | 前端 | 中文 IME 回车误发送：两处 keydown 未检查 `isComposing`，拼音确认回车把半截输入发出——中文产品核心流 bug | characters.$characterId.tsx:297; vector-search.tsx:160-162 |
| M21 | 前端 | 移动端导航不可达：导航链接 `hidden md:flex` 且无汉堡菜单替代，md 断点以下七个板块全部无法到达 | ui.tsx:90 |
| M22 | 前端 | 聊天发送失败静默吞没 + 全站无 toast 系统：onError 移除乐观消息但不提示；MCP 开关失败、通知已读失败同样无声 | characters.$characterId.tsx:91-94; settings.tsx:430-437 |
| M23 | 前端 | queryKeys 契约仅 10/~35 个 hook 遵守，其余内联数组键靠 TanStack 前缀匹配侥幸工作 | queries.ts:106-441 多处 |
| M24 | 压缩归档 | `_summarize_group` 把 UUID 字符串当角色名传给 memory_compress.yaml（`[角色] {character_name}`），归档摘要质量受损 | loops.py:721 vs memory_compress.yaml:4 |

### P3（LOW，择要）

| # | 问题 | 证据 |
|---|------|------|
| L1 | search_hybrid 未来时间戳无钳制：exp(+x) 使得分放大 >1.0 | memory_repo.py:442 |
| L2 | chat 面只有 person_memory 注入，reflections/diary 不进对话（若属设计意图应文档化） | chat.yaml 占位符核对 |
| L3 | user_id 工程标识符泄漏进决策 Prompt（「用户 qq_12345」），违反 AGENTS.md §4.3 LLM 边界 | person_memory_service.py:273 |
| L4 | person_memory_entries.id 无服务端默认值（其余表均 DEFAULT uuidv7()）；platform 无 CHECK | 0011:23 |
| L5 | character_state_history 重新引入 DEFAULT 分区，与 0002 删除策略相悖；action_records 反之无 default，预创建失败时跨月插入硬失败而启动仅告警 | 0007:43-46; main.py:196-198 |
| L6 | pg_uuidv7 扩展创建被注释但所有默认值依赖 uuidv7()——非 Docker 环境迁移即败 | 0001:24 vs :31 |
| L7 | JWT 测试密钥 30 字节 <32 下限，全量测试刷 InsecureKeyLengthWarning | tests/test_jwt_handler.py:18 |
| L8 | 前端 zod/@radix-ui/* 三个声明依赖零导入（安装膨胀）；formatRelativeTime 在 ≥3 文件重复实现 | package.json:17-18,27 |
| L9 | 前端全库零 aria-label；开关按钮无 role="switch"/aria-checked；删除弹窗无焦点陷阱/Esc；登录页 placeholder 当 label | settings.tsx:430-448 等 |
| L10 | 「清除全部通知」「配置重置」无确认对话框（与删除角色的谨慎弹窗形成反差） | notifications.tsx:168-178; settings.tsx:558-568 |
| L11 | nginx 零安全头（CSP/XFO/XCTO/HSTS）；client_max_body_size 未设（默认 1MB）；/metrics 经前端代理公开且后端豁免鉴权 | nginx.conf 全文; middleware.py:122 |
| L12 | CI：action 按 tag 非 SHA 锁定；无 concurrency 取消；uv 无缓存；无 gitleaks/hadolint/compose config 校验 | ci.yml |
| L13 | alloy 挂载 docker.sock 但配置只用文件源——纯多余逃逸面 | compose:216 |
| L14 | docker/postgres/Dockerfile 是死代码（compose 直用官方 pgvector:pg18），且钉 pg17 与运行时 pg18 矛盾 | compose:29 |
| L15 | docker-deployment.md 大面积漂移：教用户设 `DB_PASSWORD`（实际变量 POSTGRES_PASSWORD，设错静默回落 password 默认值！）、GRAFANA_PASSWORD、pg17 自构镜像、5432 端口等 | docker-deployment.md:41,98,120,291,596,599 |
| L16 | deployment.md 组件表仍列 PgBouncer/Langfuse/Lark/OTel Collector 等不存在组件；EMBEDDING_DIM 写 1536 | deployment.md:40-54,246 |
| L17 | oxfmt 无配置文件告警（用默认值） | pnpm lint 输出 |
| L18 | 通知 Redis list 无 TTL/上限；mark_embedding_failed 读改写无 FOR UPDATE；迁移 downgrade 策略混杂（0001-0008 raise vs 0009+ 实现） | api/notifications.py; memory_repo.py:364-387 |

---

## 四、分维度详评

### 4.1 项目定位与差异化（9）

「世界驱动的陪伴」叙事在本轮新增能力后更加成立：角色现在会**传闲话**（好友经历二手传播）、**组局**
（三人临时小聚）、**顺应天气/节日调整计划**（世界事件注入决策触发 planChanges 重规划）——小镇的
「社会感」从单轮寒暄进化到了有舆论、有共同记忆的层次。README 特性表逐条对照代码全部属实，
两轮审查推动的「宣称↔实现」一致性已经建立。

### 4.2 分层架构与模块边界（8.5）

main.py 组装层定位稳固；core→messaging 经 runtime 回调解耦经受住了群活动/传闻/分享三个新特性的考验
（tick.py:1604-1618 延迟 import + session_factory 注入模式一致）。本轮唯一架构级建议：`_perceive`
已膨胀至 230+ 行、12 类感知源的装配函数，建议拆分为 PerceptionBuilder（纯读聚合）+ 感知源注册表，
否则下一个注入源会继续线性增长。

### 4.3 多智能体交互与世界模拟（8）

+1 的构成：传闻传播（含防骚扰设计：每好友每窗口一条、importance 递减、heard 去重）、群活动（≥3 人门槛
+ 集体叙事 + related_characters 互指激活预留字段）、对话承接式四句、世界事件中断重规划。
剩余差距：关系强度仍是公式化数值累积（chat_with +5 / 群活动 +2），对话质量不影响关系曲线；
agent-agent 对话仍是单次生成而非真正多轮往返。这两项维持「可接受的简化」评价，但应在 roadmap 明示。

### 4.4 认知机制完备性（8）

**设计层面已达本项目历史最高水平**：

- 反思：编号记忆 → 多主题结构化归纳 → ReflectionSource 溯源外键 → tier-2 元反思跨期归纳（7 天冷却）
  ——Generative Agents 论文的 reflection 循环完整实现且有工程增强；
- Person Memory：append-only 条目层根治 telephone game，后台 LLM 压缩合并主档，热度衰减，
  对话上下文 = 主档 + 未压缩条目；
- 记忆生命周期：写入精确去重 + 向量化改写去重 + 分级保留 + 压缩归档 + 归档行豁免，闭环完整；
- 规划：daily 类型 + TTL 过期 + createPlanChanges 白名单类型/优先级钳制/≤3 条 + ScheduleSystem 桥接。

**实现层面的两个 HIGH 缺陷**（H1 日记时钟错位、H2 幽灵反思计数）说明：新链路缺测试的问题（二轮 N2）
仍未还清——这两个 bug 都是「一个断言就能拦住」的类型。DiaryService 至今零测试覆盖。

### 4.5 ReAct 工具调用（8.5）

媒体工具链（draw_image/video）补全了「角色能看能听还能创作」的能力版图；CQ 出站清洗顺序正确
（先剥全部 CQ 再升级白名单媒体 URL）。扣分点：媒体生成成本不入 BudgetManager（真实花费被低估）、
群聊判模型漂移（M15）、视频轮询窗口与文档不符（M18）。

### 4.6 数据持久化设计（7.5）

亮点延续：HASH 分区裁剪 + halfvec HNSW + 部分索引与查询模式逐一匹配 + FOR UPDATE SKIP LOCKED
worker 队列 + 0013 的 add→backfill→drop→rename 安全转换。压缩归档的「绝不未压缩先删」不变量
（loops.py:662-673）是本轮最佳工程细节。

下调 1 分的原因是本轮发现了**三类无界增长**（H9 分区只建不删、H3/M1 Redis 流与 messages 表）
和 EMBEDDING_DIM 无启动校验（M2）——「为未来裸奔」的账单没有付清，只是换了科目。

### 4.7 全链路可观测性（9）

Langfuse Tick 根 trace + ContextVar 父子 generation 树落地（二轮 0.5 分缺口关闭）；DB_QUERY_DURATION
真实埋点；11 条告警规则命名与 metrics.py 一致。但 Prometheus 抓取目标配置错误（H8）使整套体系在
Linux 生产环境**实际不可用**——观测性设计与观测性可用性是两回事，后者才是分数的意义。

### 4.8 部署与工程化（6.5）

加分项：db-backup profile（原子写入 + 保留期清理 + PGPASSWORD 必填 fail-fast）、全服务 json-file
日志轮转锚点、冷启动演练脚本、CI 前端契约守卫。减分项集中在**暴露面**：Redis 零认证、PG/Grafana
弱默认值兜底、全端口 0.0.0.0、生产门禁缺 ENVIRONMENT 无法武装、抓取目标断链。这组问题的共性是
「开发机视角的合理默认」直接暴露给了「生产部署视角」——需要一次系统的 compose 加固 pass。

### 4.9 前端工程化与 UX（7.5）

类型收敛完成（手写接口仅剩文档化的临时契约）；测试增至 6 文件 38 用例；React Compiler 真实启用；
401 流与 WS 清理逻辑严谨。新发现的核心问题：WS 重试计数不清零（M19）、IME 回车误发（M20）、
移动导航断裂（M21）、无 toast 导致的静默失败族（M22）。a11y 全库零 aria-label 是系统性欠账。

### 4.10 长期运行风险治理（7）

记忆侧治理闭环（压缩归档）值得肯定；量化更新：memory_episodes 稳态 ≈65-220K 行受控，
world_events 90 天保留受控（但删除走全表扫描，M4）。**新的无界清单**：action_records/
character_state_history（~175 万行/年合计）、messages、onebot:events 流 + DLQ、通知 list。
并发侧：CAS/围栏/版本仲裁框架到位，剩余窗口见 M8-M11 与 H10。

### 4.11 用户体验（专项）

QQ 端：三层回复决策 + 概率门控 + 多段拟人节奏 + 主动分享构成良好底座；缺口是**免打扰时段缺失**
（世界时间的「早安」可能落在现实凌晨 3 点）、无按用户/群节流（刷屏即烧钱）、群回复无引用/@ 定位
（智能回复模式下用户不知道 bot 在答谁）。
Web 端：移动导航断裂是最大悬崖；虚拟时间与现实时间混排无标注；破坏性操作确认不一致。

---

## 五、技术选型评价（含 LangChain 结论）

| 选型 | 评价 |
|------|------|
| **LangChain 1.x** | ✅ **建议保留**（本轮结论）。实测使用面收敛于 llm/client.py + llm/fallback.py 两文件的 ChatOpenAI 包装与 langchain_core.messages 消息构造；structured_output、多源 failover、mypy strict 全部工作正常。移除需重写 ~700 行客户端核心并重新验证 fallback/熔断/预算矩阵——纯 refactor 风险无功能收益。触发重评的条件：langchain 2.x 大版本升级或需要切换非 OpenAI 兼容协议 |
| PostgreSQL 18 + pgvector(halfvec/HNSW) | ✅ 千万行规模前的正确选择；分区生命周期治理是欠账不是选型错误 |
| Redis 8（锁/Streams/状态/预算） | ✅ 用法克制；Streams 语义需按 §三 H3/H4 修正 |
| OneBot v11/v12 反向 WS | ✅ 正确架构选择；动作响应回包处理是补课项 |
| React 19 + React Compiler + TanStack | ✅ 自洽；zod/radix 死依赖应清理 |
| OTel + Langfuse + LGTM | ✅ 完整；抓取目标修复后即达设计价值 |

---

## 六、修复路线图（优先级排序）

### 立即（P0/P1，预计 3-4 人日）

1. **H2 幽灵反思计数**：`count_unreflected` 增加 `is_duplicate.is_(False)` 过滤 + `mark_duplicate`
   同步置 `is_reflected=True`（双保险）+ 补一致性测试；
2. **H1 日记时钟对齐**：调度器把 world_time 传入 DiaryService 作为 target_date，幂等键与 PERIOD_DAYS
   窗口全部改为世界日历语义 + 补加速时钟下的回归测试；
3. **H3+H4 消息队列语义修正**：内联成功后 XDEL（配合 XACK），恢复循环保留 `>` 投递作为唯一补偿通道；
   去重 SETNX 移至回复发送前（生成成功后），失败路径清除去重键允许重试；两流加 maxlen≈10000 approximate；
4. **H5 自消息排除**：`user_id == self_id` 早退 + 问候层加概率门控；
5. **H6+H7+H8 部署加固 pass**：基础设施端口绑 127.0.0.1、Redis requirepass + REDIS_URL 带凭据、
   PG/Grafana 密码改 `:?` 必填、.env.example 补 ENVIRONMENT/CORS_ORIGINS 及 18 个缺失变量、
   Prometheus target 改 backend:8000、nginx 安全头 + body size；
6. **H9 分区丢弃策略**：partition_scheduler 增加「删除超过 N 月的历史分区」（action_records 默认 12 个月、
   state_history 默认 6 个月，可配置），处理 default 分区残留行；
7. **H10 看门狗失效中止**：locks.py 增加 lock-lost Event，tick 主循环在 await 点检查并优雅终止。

### 两周内（P2）

8. M6 preferences 深合并；M7 reflections/memories 截断上限（如各 500 字符）；M24 压缩归档传真实角色名；
   L1 未来时间戳钳制；
9. M3 paraphrase 去重改 ORDER BY ... LIMIT 1 形态；M4 world_events(created_at) 索引迁移；
   M1 messages 保留任务（>180 天删）；M2 启动时 atttypmod 校验 embedding 维度；
10. M5 ORM 元数据对齐（复合 PK 声明/补 GIN 索引声明/修 docstring）；
11. M8-M11 并发收尾：move 端点检查 CAS 结果、reconcile redis_to_pg 走 update_state 增版本、
    admin 双端点接围栏、importer 增版本；
12. M12-M18 消息域：retcode 解析与日志、回复路径复用 push_share 故障转移、重放按 self_id 选连接、
    群判模型恢复 flash 可配、chat() 接预算检查、心跳 last-seen + 发送超时、媒体文档对齐；
13. M19-M23 前端：WS onopen 重置、isComposing 检查、移动端汉堡菜单、轻量 toast 层 +
    清除全部/配置重置确认、queryKeys 契约全量迁移；
14. L3-L18 长尾：文档校准（docker-deployment.md 变量名/镜像/端口全面重写对齐）、死依赖清理、
    a11y 快赢（icon 按钮 aria-label、开关 role=switch）、JWT 测试密钥加长、oxfmt 配置或注记。

### 战略级（沿袭）

15. agent-agent 多轮对话与关系质量化（对话内容驱动关系强度）；
16. QQ 免打扰时段与按用户节流；
17. 建立分区/流/表三级「无界增长巡检」指标（已有 retention 指标基础上补 stream length 与分区行数）。

---

## 七、总评

第三轮审查最大的发现是**这个项目的「问题曲线」正在健康地右移**：首轮的问题是「承诺未兑现」（认知断流），
二轮的问题是「兑现了但没人敢改」（测试债），本轮的问题是「兑现得很好，但更深的语义层没人验证过」
（时钟体系、队列语义、分区生命周期、部署暴露面）。每一轮问题的隐蔽性都在上升，而这只有在功能基本盘
扎实之后才可能暴露——这是项目成熟的标志。

整改批的执行力依旧出色：五个战略项四个落地一个给出评估结论，CHANGELOG 与代码零失配。
但本轮也暴露了一个规律：**新特性落地速度 > 新特性验证深度**。日记系统、消息队列、分区调度都是
「写完即对」的假设在支撑，缺的是把不变量写成测试的那一步。按 §六 路线图执行，优先把两个认知 HIGH
和队列语义修正掉，项目将真正进入「可放心持续演进」状态。

**三轮总评：7.85 / 10。设计已到位，语义待验证；下一个杠杆点是「不变量的测试化」，不是新功能。**

---

## 附记：本报告问题清单的修复状态

（随修复批推进更新）

| 批次 | 覆盖问题 | 状态 |
|---|---|---|
| Phase A | H1 H2 H3 H4 H5 H9 M24 M6 M7 L1 | ⏳ 待修 |
| Phase B | H10 M8-M18 | ⏳ 待修 |
| Phase C | H6 H7 H8 M19-M23 L11 | ⏳ 待修 |
| Phase D | M1-M5 L2-L18 文档校准 + 测试补齐 | ⏳ 待修 |
