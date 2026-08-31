# AI Town 全面审查报告·第六轮（2026-08-26，HEAD `8e81266`）

> **文档定位**：对[五轮](project-review-20260826.md)（基线 `871965a`）之后的**第五轮修复批 + 认知/世界引擎特性批**
> （`871965a..8e81266`，约 30 commits：R5 全部 P1/P2 修复、tick 上帝文件拆分、围栏原子状态写、容量准入、
> 认知升级（反思/PersonMemory/传闻）、HNSW 重索引循环、OneBot 限流、前端 WS 聊天与错误页等）的全面独立重审。
>
> **本轮方法**：六路并行深审（世界引擎与多智能体交互 / 认知机制 / 数据持久化与并发 / 消息多端与 ReAct /
> API 安全与测试 / 可观测性·部署·前端）+ 主审对全部 P1 结论源码二次亲读确认 + 质量门禁本地实测。
> 所有结论基于当前工作区源码直接阅读，关键结论附 file:line 证据。
>
> **严重度定义**（沿用前五轮）：P0=核心功能静默失效/发布阻断；P1=功能性 bug/承诺违约/结构性缺陷/隐私泄露；
> P2=纵深防御缺口/文档漂移/性能隐患；P3=瑕疵。

---

## 一、执行摘要

**一句话结论：这是六轮以来修复执行力最强的一批——R5 五个 P1 全部真实修复（主审逐一亲验）、门禁实测全绿
（pytest 545 通过较上轮净增 64）、span 契约与日志 trace_id 两大可观测性欠账一次性清偿；但特性批的规模
（约 30 commits）再次伴生新接缝缺陷，且本轮以「跨组件授权边界」为标尺下钻，发现了两个自 P0-8 修复以来
一直存在的跨用户隐私泄露面——它们不在上轮清单里，因为此前从未有人以「普通用户视角」走查过读接口。**

本轮最重要的三个判断：

1. **「消息历史有归属校验」是一个半成品承诺。** `/messages/history` 的 `conv.user_id != user["user_id"] → 403`
   检查（messages.py:163-167）确实存在，但同仓库的 `GET /characters/{id}/messages`（characters.py:600-645）
   以「跨会话聚合」的名义把同一角色的**所有用户**私聊一锅端出，无任何归属过滤；`person-memory` 读接口
   （memory.py:114-129）接受任意 `user_id` 查询参数。任何登录用户可枚举角色对任意其他用户的记忆画像。
2. **深度集成 ≠ 部署完成。** Langfuse 埋点已做到 tick 根追踪、生成级 prompt/response 捕获、成本与会话绑定，
   但 docker-compose.yml 中 langfuse 零命中，`.env.example:83` 指向无人监听的 :3001——开箱即静默 no-op。
   同类问题：头采样 0.5 在链路进入 collector 之前就丢弃一半 trace，「错误必采」尾采样策略只能看到采样幸存者。
3. **世界模拟层出现「装饰化」倾向。** 天气演化每 tick 产出 `move_multiplier/outdoor_fail_bonus` 影响矩阵，
   但全库零消费者；DurationCalculator（天气/拥挤/体力/心情修正）只被 API demo 端点调用；`workday_only`
   场景限制因 `is_workday` 从未被传入而是死逻辑。文档宣称的模拟深度与行为现实之间存在系统性落差。

### 维度评分对照

| 维度 | 首轮 | 二轮 | 三轮 | 四轮 | 五轮 | 六轮 | 变化主因 |
|---|---:|---:|---:|---:|---:|---:|---|
| 项目定位与演化 | 9 | 9 | 9 | 8.5 | 8 | **8** | 主动分享复活、宣称↔实现对账大幅改善；但天气/耗时模拟装饰化是新形式的宣称落差 |
| 分层架构与模块边界 | 7 | 8.5 | 8.5 | 8.5 | 8.5 | **8** | tick.py 拆分后回涨至 1484 行；服务层仍近乎缺失；两个 HIGH 授权缺口暴露 API 层纪律松懈 |
| 多智能体交互与世界模拟 | 6 | 7 | 8 | 8.5 | 8.5 | **8** | 群聊上下文/传闻/群聚运转正常；天气影响零消费、workday 死逻辑拉低模拟深度 |
| 认知机制完备性 | 4.5 | 8 | 8 | 8 | 8 | **7.5** | 主链路扎实，但 imp≥7 永久类无界增长(H)、PM upsert 竞态、日记窗口错过、对话记忆绕过管线 |
| ReAct 工具调用 | 8 | 8.5 | 8.5 | 3 | 8 | **8** | 循环健壮+事务暂存+测试锁定；残余：工具无超时、观察注入面 |
| 数据持久化设计 | 8 | 8.5 | 7.5 | 7.5 | 8 | **8** | 真相源架构经查属实且三层恢复闭环；ORM 索引元数据漂移、pg_uuidv7 文档过时 |
| 全链路可观测性 | 9 | 9.5 | 9 | 8 | 8 | **7.5** | span 扩容+日志 trace_id 全注入是实质进步；但 Langfuse 开箱即死 + 头采样废掉尾采样 = 两处接线断裂 |
| 部署与工程化 | 7.5 | 8 | 6.5 | 6.5 | 6 | **7.5** | deploy-smoke 真修复（CI 覆盖层注入凭据）、openapi 漂移守卫、迁移门禁落地；Langfuse 缺席扣减 |
| 前端工程化与 UX | 6.5 | 7.5 | 7.5 | 8 | 8 | **8** | error/not-found 页、轮询放宽、聊天分页、WS 聊天全部落地；i18n/a11y 依旧缺位 |
| 长期运行风险治理 | 3.5 | 7.5 | 7 | 7.5 | 7.5 | **7.5** | autovacuum/HNSW 重索引/plans 清理落地；imp≥7 无界类与备份 RPO≤6h 未解 |
| **十维均值** | **6.9** | **8.1** | **7.85** | **7.4** | **7.85** | **7.8** | |
| 安全与隐私 | – | – | – | 4.5 | 7 | **6.5** | prompt guard 强化+公开读收紧加分；两个跨用户读取 HIGH 缺口是实打实的隐私回归面 |

> 均值持平（7.85→7.8）掩盖了内部剧烈换血：部署从 6 修复到 7.5（R5-H1 真修），可观测性从 8 掉到 7.5
> （两处「集成好了但没通电」），认知机制从 8 掉到 7.5（增长治理出现新漏洞）。本轮发现的主题词是
> **「最后一公里的第二段」**——上一轮修好了「零件到装配线」，这一轮暴露的是「装配线到用户」之间的
> 授权边界、部署拓扑与统计口径断层。

---

## 二、五轮修复批核验：5/5 P1 属实 + 特性批抽查

主审对 R5 全部五个 P1 逐项亲读当前源码确认：

| R5 问题 | 判定 | 本轮核验证据 |
|---|---|---|
| R5-H1 deploy-smoke 红到货 | ✅ 已修 | 新增 `docker-compose.ci.yml` 覆盖层注入 Settings 必填占位凭据；ci.yml:126 job env 补 `GRAFANA_ADMIN_PASSWORD`；ci.yml:130-132 注释明确引用 R5-H1 并声明以 CI 同环境实证 `config -q` 通过 |
| R5-H2 proactiveShareIntent 死字段 | ✅ 已修 | tick.py:469 决策 schema 已声明 `"proactiveShareIntent": {"type": "boolean"}`，tick.py:556 正常读取——主动分享全链路首次可达 |
| R5-H3 日记幂等世界时错配 | ✅ 已修 | diary_service.py:315 批量路径转发 `world_now=world_now`，diary_date 与幂等键统一到世界时钟 |
| R5-H4 multimodal 成本契约游离 | ✅ 已修 | client.py:790 `multimodal_structured_output` 已接入 `_check_cost_control`（预算检查+熔断），失败路径接 Langfuse |
| R5-H5 OneBot token 可选 | ✅ 已修 | onebot.py:464-466 启动期调用 `check_onebot_access_token()`，production 且未配置时拒绝启动 |

P2 抽查（代理深审中顺带核验）：日记素材采样改 DESC 取最新（R5-M1 ✅）、WS 驱逐身份校验（R5-M4 ✅）、
扇出 rollback（R5-M5 ✅）、失锁不变量补闸（R5-M6/L11/L12 ✅）、工具指令门控（R5-M3 ✅）、归档行按
created_at 计龄（R5-M2 ✅，迁移 0017）、群环写入 bot 回复（R5-L9 ✅）、采样联动与日志 trace_id 全注入
（R5-M17/M18 ✅，span 扩至 8 类关键路径）、error/not-found 页与轮询放宽与聊天分页（R5-M13/14/15 ✅）、
告警阈值随预算 gauge 联动+投递自监控（R5-L15 ✅）、tick.py 拆分 PerceptionMixin/SocialMixin（R5-L14 部分 ✅）。

**结论：修复执行力维持最高水准，CHANGELOG 与代码零失配纪录延续。**

---

## 三、本轮新发现问题总览

### P1（HIGH）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| R6-H1 | 安全/隐私 | **跨用户私聊泄露**：`GET /characters/{id}/messages` 聚合该角色全部会话消息，无任何用户归属过滤——任何登录用户可读取所有用户与角色的私聊内容，完全绕过 P0-8 在 `/messages/history` 上建立的归属校验。端点甚至未声明 CurrentUser 依赖 | characters.py:600-645（`conv_repo.list_by_character(limit=100)` 后逐会话取消息直接返回）；对照 messages.py:163-167 的正确范式 |
| R6-H2 | 安全/隐私 | **Person Memory 读取无归属校验**：`GET /characters/{id}/person-memory?user_id=X` 接受任意 user_id 且不与调用者比对；`/person-memory/list` 列出角色对全部用户的记忆画像（偏好/互动史/情感连接）。任何登录用户可枚举角色对任意用户的认知 | memory.py:114-129（`user_id: str = Query(...)` 无比对）；memory.py:132-164 |
| R6-H3 | 可观测/部署 | **Langfuse 深度集成但从未部署**：埋点含 tick 根追踪、生成级 prompt/response（2000 字符截断）、成本、session/user 绑定、otel_trace_id 交叉引用，但 docker-compose.yml 中 langfuse 零命中，`.env.example:83` 指向无人监听的 `localhost:3001`——开箱即静默 no-op，README 可观测性栈宣称缺一角 | grep compose = 0 命中；.env.example:81-83；langfuse_integration.py:75-80（未配置即跳过） |
| R6-H4 | 可观测 | **头采样 0.5 废掉尾采样「错误必采」**：SDK 端 `TraceIdRatioBased(0.5)` 在 trace 进入 collector 之前丢弃一半，collector 尾采样的 errors-always 策略只能作用于幸存的一半——compose 注释宣称的「错误链路必采」只对 50% 流量为真 | tracing.py:114 vs otel-collector.yml:13-29；docker-compose.yml:115 宣称注释 |
| R6-H5 | 认知/长期运行 | **importance≥7 永久保留类无界增长**：explore/adventure 基础分 7/8（tick.py:1422-1423）叠加「importance>=7 永久保留」（config.py:127），探索型小镇的高分记忆及其 4KB halfvec 向量永久累积，且被排除在压缩候选（≤6）之外——长期运行的最大存储项 | tick.py:1422-1423; config.py:126-127; memory_repo.py:226-258（候选上限 importance<=6）; loops.py:884-899 |
| R6-H6 | 消息 | **OneBot 连接队头阻塞**：WS 接收循环内联 `await handle_event`（含完整 LLM 回合），一条慢回复阻塞后续事件与心跳帧处理——NapCat 侧发送缓冲堆积，长时间停顿可能被判死连接 | onebot.py:678-679 |

### P2（MEDIUM）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| R6-M1 | 消息 | 群聊问候语子串误判：`"hi" in text_lower` 命中 this/which/history，`"hey"` 命中 they——混合语言群里 0.9 概率问候回复层成为误触发动机 | service.py:207-208 |
| R6-M2 | 消息 | LLM 故障时群聊 fail-open（p=0.3 仍回复）：LLM 中断期间每条未命中消息独立掷 30% 骰子→无上下文刷屏；docs 宣称 fail-safe 不回复，行为相反 | service.py:277-280 vs messaging-service.md:411,436-437 |
| R6-M3 | 并发 | 跨实例去重键裸 message_id：两后端实例服务同群不同角色时互相抑制回复（先到者占 SETNX 槽）；单实例假设未文档化 | onebot.py:89-91,956-978 |
| R6-M4 | 消息 | Web 聊天缺世界状态注入：ws_chat 构造 MessageService 未传 redis，world_time/weather 上下文对 Web 用户静默缺席（QQ 路径正常）——跨渠道人格不一致 | websocket.py:436-441 vs onebot.py:884-891; service.py:528-542 |
| R6-M5 | 认知 | PersonMemory upsert 竞态：UPDATE-then-INSERT 无 ON CONFLICT，并发首条消息双双 rowcount=0 后 INSERT，败者撞唯一索引被吞——该次互动的偏好/热度更新丢失 | person_memory_service.py:214-241,135-141 |
| R6-M6 | 认知 | 日记夜窗口窄于轮询间隔：默认倍速下 22:00–06:00 世界夜窗≈24 真实分钟 < 30 分钟轮询周期，相位错开时部分世界日整天日记被跳过（周/月/年触发日跨多轮询不受影响） | diary_service.py:42,61-77 vs loops.py:174 |
| R6-M7 | 认知 | 对话记忆绕过 EpisodeService：chat_with 双方记忆 raw session.add——无近邻去重、无 LLM 评分、硬编码 importance=6，重复话题在向量化去重（仅 24h 窗）前无限累积 | social.py:316-346; config.py:139 |
| R6-M8 | 认知 | 反思洞察无跨批语义去重：`_parse_themes` 仅批内去字面重，跨批近似主题持续新增；tier-2 元反思永久保留使近似重复同样永久累积 | reflection_service.py:238,246-247; config.py:67 |
| R6-M9 | 世界模拟 | 模拟层装饰化三连：①天气影响矩阵（move_multiplier/outdoor_fail_bonus）产出后全库零消费；②DurationCalculator（天气/拥挤/体力/心情）只在 API demo 端点存活，Tick 路径不用；③`is_workday` 从未被计算传入，workday_only 场景限制为死逻辑 | weather_evolution.py:38-44; duration/calculator.py:58-182 vs api/system.py:286-309; movement/system.py:58,91 vs tick.py:951-955 |
| R6-M10 | 架构 | tick.py 拆分后回涨至 1484 行（PerceptionMixin/SocialMixin 迁出又因叙事记忆/计划自动推进等新特性长回）；服务层仍只有 CharacterService 一个实例，move_character 在路由层执行移动+审计+CAS 重试全套业务逻辑 | tick.py 全文; services/ 目录; characters.py:232-300 |
| R6-M11 | 成本 | 默认模型/价格失真：`.env.example` 默认 MODEL_CHAT=gpt-4o-mini 而 LLM_MODEL_PRICES 缺省按 agnes 价（$0.5/$1.5/Mtok）计——实际 $0.15/$0.6，预算约 3 倍提前耗尽（保守方向但静默失真） | .env.example:36 vs client.py:63-64 |
| R6-M12 | 可观测 | 告警盲区：redis_stream_messages gauge 存在但无任何规则消费（DLQ 积压无人知）；无 DB 连接池饱和、备份新鲜度、磁盘压力告警 | metrics.py:167-171 vs alerts.yml 全文 |
| R6-M13 | 安全 | 明文单管理员凭据无哈希（compare_digest 对 .env 明文）；JWT 无吊销/黑名单/刷新机制（jti 生成但从不校验），泄露即有效至过期 | config.py:100-101; system.py:119-121; jwt_handler.py:38-61 |
| R6-M14 | 并发 | API move 裸 hset 不持 tick 锁：PG CAS 通过后直接写 Redis location，可能被进行中 Tick 以陈旧 new_state 覆盖——对账最终收敛但窗口（最长 600s）内用户可见位置回退 | characters.py:286-292; reconcile.py:235-246 |
| R6-M15 | 测试 | 零 HTTP 层路由测试：全测试库无 TestClient/httpx ASGI 调用，路由函数以假 session 直调——序列化/状态码/依赖注入/中间件集成未被端到端验证；admin 路由/login/WS 消息循环/tools invoke 全部未测 | tests/ 全库 grep TestClient = 0 |
| R6-M16 | 多实例 | 维护循环无 leader 锁：diary/retention/compaction/分区调度循环在 worker>1 时每个进程各跑一份（日记幂等可挡，retention/压缩并发交错风险真实）；当前单进程部署下为潜伏债 | main.py:320-373 对照 engine.py:277-326 |

### P3（LOW，择要）

| # | 问题 | 证据 |
|---|------|------|
| R6-L1 | EmbeddingWorker 名为批量实为逐条串行调用（未用 embeddings 数组入参），吞吐上限 ≈20×RTT/5s | embedding_worker.py:38,117-119 |
| R6-L2 | 召回池 top_k*2=20 无重排/MMR，HNSW 召回偏差直接决定最终质量 | memory_repo.py:482 |
| R6-L3 | LLM 评分失败返回常量 5 而非规则分（含情绪加成），与 create_episode docstring「回退传入 importance」冲突 | episode_service.py:98,105 vs :127-128 |
| R6-L4 | 嵌入维度仅启动期校验 DDL 列宽，不校验活模型输出维度——配错模型=批量 5 连败熔断，行级不可自愈 | startup_checks.py:105-138; memory_repo.py:361-426 |
| R6-L5 | 工具调用无独立超时（信任一切工具自律）；观察文本无分隔符回注 prompt（KB/记忆内容可间接引导决策） | registry.py:363,454; tick.py:530-540 |
| R6-L6 | OneBot 协议缺口四件套：v12 出站动作缺失（仅 v11 send_*_msg）、动作无 echo 关联、无陈旧连接驱逐、无出站限速 | onebot.py:1250-1274,562-565 |
| R6-L7 | Alloy 孤儿 prometheus.remote_write（无 scrape 供数，Prometheus 亦未开 receiver flag） | alloy.config.alloy:58-62 |
| R6-L8 | 文档/schema 漂移组：README 与 development-guide 仍要求 pg_uuidv7 扩展（实际用 PG18 内建 uuidv7()，0001:24 已注释）；pg_trgm 安装后全库零使用；idx_mem_unmaterialized ORM 声明 next_retry_at vs 物理列 timestamp | README.md:61; 0001_init.py:24-26; memory_episode.py:105-109 vs 0002:143 |
| R6-L9 | fetch_retention_candidates 按 importance+timestamp OR 组合过滤无匹配索引——每日保留周期顺序扫描 | memory_repo.py:238-258 |
| R6-L10 | 备份 RPO≤6h：pg_dump/Redis 快照均 6h 且同主机，无 WAL 归档/PITR、无异机同步自动化 | backup.sh:12-36 |
| R6-L11 | python-multipart 声明但无 multipart 路由（死依赖） | pyproject.toml |
| R6-L12 | 错误兜底回复 DEFAULT_ERROR_REPLY 以 character 身份入库并回流后续 prompt 历史；JSON 提取兜底可能把原始 JSON 吐给用户 | service.py:418-425,767-768 |
| R6-L13 | str(e) 泄漏进 500 细节（messages.py:111、admin.py:186,690,782,936）违背 exceptions.py 自身策略；ValueError/TypeError→400 映射可能把服务端 bug 化装成客户端错误 | exceptions.py:16-68 |
| R6-L14 | i18n 缺位（32 页硬编码中文）；a11y 无 aria 佐证；localStorage token XSS 权衡仍未文档化 | routes/*.tsx; auth.ts:13-15 |
| R6-L15 | 场景 ID 硬编码于 Action 定义（home/cafe/school/library/bookstore...），换镇需改代码，违背配置真相源原则 | actions/life.py:19,38,59,75; work.py:19,29,41 |
| R6-L16 | find_shortest_path 死代码；_SOLO_RECOVERY_ACTIONS/重要性表/群聚人数上限等魔法数散落 | movement/system.py:167-209; tick.py:999-1002,1406-1430; social.py:483 |
| R6-L17 | 事件日历 import 时加载（模块不可脱离 configs 导入）；ResourceEvolution 商品表 Python 硬编码 | event_evolution.py:79; resource_evolution.py:22-27 |

---

## 四、分维度详评

### 4.1 项目定位与差异化（8）

「世界驱动的陪伴」定位继续成立且宣称纪律显著改善：主动分享自诞生起首次真正可达（schema 补字段后全链贯通），
README 特性表连续两轮的「宣称↔实现断裂」模式被打破。但落差以新形式回归——不是缺特性，而是**特性的深度
低于叙述**：天气系统每 tick 辛勤产出影响矩阵却无一处消费（R6-M9），「角色感知天气绕路/户外失手」的画面
只存在于文档里。定位无需调整；需要的是把「模拟深度」当作与「特性有无」同级的对账对象。

### 4.2 分层架构与模块边界（8）

runtime 回调解耦与 Mixin 拆分的方向正确，但 tick.py 在特性批后回涨至 1484 行——上帝文件治理跑输了特性
交付速度（R6-M10）。更值得警惕的是 API 层：两个 HIGH 授权缺口（R6-H1/H2）本质都是「读接口没有过一遍
归属设计」——写路径经过五轮打磨已有 P0-8 范式，读路径仍在裸奔。服务层依旧只有 CharacterService 孤例，
move_character 把移动执行+审计+CAS 重试塞在路由函数里（characters.py:232-300）。分层纪律呈现明显的
「核心厚、边缘薄」梯度。

### 4.3 多智能体交互与世界模拟（8）

交互机械面扎实：chat_with 跨角色排序锁防死锁、关系增量 ±10 钳制、双向记忆；group_activity 双闸门+
每日配额；传闻沿关系边传播（重要性减半、每友每窗一条）；群聊共享上下文环（20 条/24h）续用。交互是
涌现式的（LLM 基于注入的邻近名单自主选择），效果是确定性的（关系/记忆由代码写）。扣分在世界模拟深度：
天气只写不读、耗时修正器游离于 Tick 外、工作日限制死逻辑（R6-M9）——角色活在一个有天气播报但淋不到雨的小镇。
另有 chat_with 关系/记忆写在主事务之外的局部不一致窗（社交结果已落库而 ActionRecord 可能回滚）。

### 4.4 认知机制完备性与可演化性（7.5）

**记忆流**：写入同步落行+异步向量化（SKIP LOCKED/退避/熔断）成熟；混合检索单一 SQL 来源+时钟回拨钳制；
但召回池仅 top_k*2=20 且无重排（R6-L2），对话记忆绕过去重/评分管线自成体系（R6-M7）。
**反思**：两级结构（主题+元）带编号溯源与服务端钳制仍是全栈最强模式；但洞察无跨批语义去重，元反思永久
保留使近似主题永生（R6-M8）。
**规划**：LLM 涌现式 createPlanChanges + 字符级归属防护 + bigram 自动推进 + deadline 排序渲染——真实但
反应式，无独立 planner，属有意为之且有文档背书。
**Person Memory**：双层（追加条目+LLM 压缩主档）设计优秀，热度衰减/压缩/双通道注入齐备；败笔是 upsert
竞态（R6-M5）与读接口裸奔（R6-H2）。
**日记**：世界时钟快照+幂等键统一（R5-H3 修复后）正确；但夜窗口窄于轮询间隔的相位错过（R6-M6）说明
「触发条件在离散采样下是否必然被观察到」这类时序推理仍是盲区。
**总体**：写入侧质量持续高于生命周期侧——imp≥7 永久类无界增长（R6-H5）是治理版图上第一块真正的洞。

### 4.5 ReAct 工具调用（8）

循环本体维持八轮打磨后的高水位：use_tool 保留字豁免、range(3) 硬上限+强制 wait 降级、观察 800 字符截断、
缺失 tool_name 合成失败观察防盲轮、状态变更 delta 内存暂存随主事务原子落库、工具全停时不注入 use_tool
指令。18 个工具六命名空间，state_mutating 工具由上下文注入资金/库存/关系强度——LLM 无法伪造资源。
启用开关 Redis hash+5s TTL 缓存+管理端 RBAC。残余：工具无独立超时（一个挂起的工具拖住整个 Tick）、
观察文本无分隔符回注构成间接注入面（数据内部可信故降级为 LOW）（R6-L5）。

### 4.6 数据库设计与数据持久化（8）

16 表盘面健康：UUIDv7 双轨生成（客户端 uuid6 + PG18 内建 server_default）、分区差异化正确（action_records/
state_history 按月 RANGE 可整分区 DETACH+DROP；memory_episodes 按角色 HASH 16 分区匹配恒定谓词）、
JSONB 克制、CHECK 约束替代枚举。「Redis 真相源 + PG 镜像」声明经查**属实**且有完整三层恢复闭环
（即时重试+优先修复队列 / 600s 版本感知对账 / 启动回灌+场景占用重建）；noeviction+512MB 是把 Redis 当
真相源的正确姿势。autovacuum 精细调优（character_states fillfactor=85 vs memory_episodes scale 0.05）
动机区分清晰。扣分：ORM 索引元数据与物理 schema 漂移（R6-L8）、retention 候选查询缺索引（R6-L9）、
API move 绕过 tick 锁的裸 hset（R6-M14，对账可收敛但窗口用户可见）。

### 4.7 全链路可观测性（7.5）

进步是真实的：span 从 3 个扩到 8 类关键路径（perceive/decide/action.execute/memory.write/tool.call/
message.process/push/llm.generate），日志 trace_id 对未采样流量也注入（Logs→Trace 跳转全量恢复），
告警投递自监控闭环，预算 gauge 锚定告警阈值。但本轮两个 HIGH 暴露「集成≠通电」：Langfuse 埋点深度
全项目之最却没有容器可发（R6-H3）；头采样在 collector 之前扔掉一半 trace 使「错误必采」名存实亡
（R6-H4）。PII 面：用户聊天内容进 span args（200 字符截断）与 Langfuse prompt/response 字段，sanitizer
只按键名脱敏——自托管单租户可接受但应显式声明。Grafana 三仪表盘与指标清单映射良好。

### 4.8 部署与工程化（7.5）

deploy-smoke 从「红到货」修复为「CI 覆盖层注入凭据+job 补 GRAFANA_ADMIN_PASSWORD」，并以 CI 同环境
实证 config -q 通过——R5 最危险的门禁回到守护位。新增 openapi.json 再生 diff 守卫（契约不再守手工导出的
过期真相）、alembic 迁移门禁、可选 CN 镜像构建参数。拓扑面保持高位：全端口回环、12 服务 mem_limit、
AOF+noeviction、备份原子改名+恢复演练脚本。扣减：Langfuse 服务缺席（R6-H3，可观测 profile 名不副实的
一角）、备份同主机+RPO≤6h 无 PITR（R6-L10）、Alloy 孤儿 remote_write（R6-L7）。

### 4.9 前端工程化与 UX（8）

上轮三项 UX 欠账全部清偿：路由级 errorComponent/notFoundComponent 落地、运维页轮询 10s→30s/5s→15s 放宽
且 notifications 改 WS 失效驱动、聊天区分页加载（末尾消息 id 游标+invalidate 同 key）。底盘继续同类项目
罕见水准：28 文件路由+自动分割、React Compiler 1.0、tsconfig 极限严格（exactOptionalPropertyTypes/
noUncheckedIndexedAccess）、queryKeys 集中契约+前缀失效锚点注释、Zustand 干净二分、WS 子协议传 token。
遗留：i18n 零基建、a11y 无 aria 佐证、api-types.ts 手写 interface 与生成类型并存的漂移面、recharts/
framer-motion 无 bundle 预算护栏。

### 4.10 消息服务与多端触达（7.5）

QQ 通道机械成熟度仍为全项目最佳：Streams 崩溃恢复日志+PEL 重放+DLQ 五次上限、发送时才认领去重槽
（缩小崩溃丢窗）、跨连接故障转移、心跳新鲜度选路、入站限流 20/min/chat、CQ 码全剥离防注入、多段拟人节奏。
ReAct 与消息侧的事务暂存纪律一致。但本轮在此域发现密度最高的新缺陷：队头阻塞（R6-H6，LLM 延迟阻塞
心跳）、问候语子串误判（R6-M1）、LLM 故障 fail-open 与文档相反（R6-M2）、Web 通道缺世界状态注入
（R6-M4）、跨实例去重键碰撞（R6-M3）。战略层欠账不变：「多渠道」实为 1.5 渠道（lark/internal 枚举残留、
ChannelAdapter 协议不存在、扇出硬编码 qq_ 前缀）、跨平台身份不合并（同一人 QQ/Web 是两个陌生人）。

### 4.11 长期运行风险治理（7.5）

并发域：世界引擎 fencing epoch CAS 教科书级、角色 Tick 四闸口失锁保护+看门狗续租、对账版本仲裁+优先
修复队列、embedding worker SKIP LOCKED——单机正确性收尾良好。残余不对称：角色状态写无 fencing token
（与世界引擎防护等级不对齐）、API move 裸 hset（R6-M14）、维护循环无 leader 锁（R6-M16，多实例潜伏债）。
记忆膨胀域：retention 两阶段（压缩成功才删）+分批 DELETE+plans 终态清理+HNSW 月度在线重建全部落地，
但 imp≥7 永久类是治理版图第一块真洞（R6-H5）——探索型世界的存储主项恰好落在所有清理规则的例外区。
备份域：RPO≤6h 且同主机，对「记忆是不可再生数据」的项目定位而言偏弱（R6-L10）。

### 4.12 安全与隐私（6.5，单列）

加分项：PromptGuard 三层纵深（检测拒绝/消毒/包裹+反注入后缀）20 测试锁定；公开读前缀收紧+精确豁免；
production 启动 fail-fast 家族扩员（默认凭据/CORS 缺失/OneBot token/嵌入维度）；限流 Lua 原子化覆盖
login/msg/public-GET/OneBot 四面；CSP/XFO/HSTS 全套安全头。减分项：两个跨用户读取 HIGH（R6-H1/H2）
——这不是新引入的漏洞，而是 P0-8 修复时就存在的并行盲区，五轮安全审查（含专门的 R4-H2 前缀收紧）
均未以「普通用户视角」走查读接口，直到本轮才暴露；明文凭据+JWT 无吊销（R6-M13）属已知取舍但应入档。

### 4.13 用户体验专项

QQ 端：三层回复决策+群共享上下文+多段节奏+主动分享（现已真正可达）构成完整体验闭环；痛点转为统计面
——问候语误判（hi⊂this）和 LLM 故障期的 30% 盲回复会直接产生「这机器人有病吧」时刻。Web 端：信息架构/
三态纪律/移动端维持高位，聊天分页落地后长对话可用性质变；但 Web 用户看不到天气/世界时间（R6-M4），
同一角色在两个渠道人格不连贯。免打扰时段连续四轮缺位——凌晨三点早安仍是单个最影响真实感的欠账。

---

## 五、技术选型评价

| 选型 | 评价 |
|------|------|
| LangChain 1.x | ✅ 使用面收敛于 client/fallback 两文件的判断六轮未变；langchain>=1.3 新大版本线需盯 churn |
| PostgreSQL 18 + pgvector(halfvec/HNSW) | ✅ 正确且红利兑现：PG18 内建 uuidv7() 使第三方扩展退役（文档未跟上）；月度 REINDEX CONCURRENTLY 解决 HNSW 死元组 |
| Redis 8（锁/Streams/状态/预算/限流） | ✅ 用法克制且 noeviction 姿势正确；建议显式化 maxmemory 告警联动 |
| OneBot v11/v12 反向 WS | ⚠️ 入站兼容好，出站仅 v11 动作、无 echo 关联——协议完整性半成品 |
| React 19 + React Compiler + TanStack + openapi-typescript | ✅ 自洽；手写 interface 应继续向生成类型收敛 |
| OTel + Langfuse + LGTM 栈 | ⚠️ 组件齐全、埋点深，但 Langfuse 未部署+头采样废尾采样——「买了灯泡没接电」 |
| Docker Compose + CI | ✅ smoke 门禁真绿、openapi 漂移守卫、迁移门禁；CI 就差 coverage 工具 |

---

## 六、修复路线图（优先级排序）

### 立即（P1，预计 3-4 人日）

1. **R6-H1/H2 授权补齐**：`get_character_messages` 加 CurrentUser+归属过滤（非本人会话仅 admin/operator
   可聚合，或按 conv.user_id 过滤）；person-memory 读接口强制 `user_id == token.sub` 或 admin RBAC——
   直接复用 messages.py:163 范式 + test_conversations_auth.py 测试模式；
2. **R6-H3 Langfuse 落地或诚实降级**：observability profile 加 langfuse 服务（或 langfuse-web+worker+pg），
   `.env.example` 对齐；若暂缓则 README 可观测性行注明「需自行部署 Langfuse」；
3. **R6-H4 采样权移交**：head sampler 改 `parentbased_always_on`，让 collector tail_sampling 独占预算
   （errors-always/>2s/20% baseline 已配好，num_traces 适当上调）；
4. **R6-H5 高分记忆治理**：给 imp≥7 增加「N 天后压缩保档」路径（归档行豁免 365d 清除），或将 explore/
   adventure 基础分降至 6 并以情绪/稀缺性加成通道补偿；
5. **R6-H6 OneBot 异步派发**：handle_event 改托管后台任务+按 chat 串行化（复用 rate limit 的 chat_key），
   心跳帧不再排在 LLM 回合之后。

### 两周内（P2 择要）

6. R6-M1 词边界匹配（CJK 分词或 `\b`）+ R6-M2 故障期 fail-closed 或全局冷却；
7. R6-M4 ws_chat 补传 redis；R6-M3 去重键掺 character_id/self_id；
8. R6-M5 PersonMemory upsert 改 `INSERT ... ON CONFLICT DO UPDATE` 单语句；
9. R6-M6 日记触发改世界小时沿检测（存上次运行世界时钟做边沿触发）或轮询缩至 10min；
10. R6-M7 对话记忆改走 EpisodeService；R6-M8 反思插入前 cosine 近邻查重（复用 find_paraphrase_duplicate 模式）；
11. R6-M9 三选一：天气接入 calculate_move/DurationCalculator 进 Tick/删除死矩阵并在文档如实标注模拟深度；
12. R6-M11 LLM_MODEL_PRICES 内置常见模型价目预设 + 预算 warning 阈值告警；R6-M12 补 DLQ 积压/池饱和/备份新鲜度三条告警。

### 战略级

13. **读接口授权走查制度化**：以「普通用户 JWT 走遍全部 GET」作为安全审查固定科目（本轮 H1/H2 均可由此
    在五轮内任何一轮被发现）；补 httpx ASGI 层路由测试（R6-M15）一并解决；
14. ChannelAdapter 协议抽取或 README 去「多渠道」措辞；跨平台身份合并方案入 roadmap；
15. WAL 归档/pgBackRest + 异机同步（记忆数据不可再生的定位要求 RPO 进入分钟级）；
16. tick.py 二次拆分（计划簿记 ~170 行迁 PlanBookkeepingMixin/服务）；维护循环 leader 锁（复用 core/locks.py）；
17. 免打扰时段（连续四轮催办）：世界时间→接收者本地时段映射或全局 DNT 窗口。

---

## 七、总评

第六轮的发现模式是五轮「接缝」主题的自然延伸：**修复批证明了工程文化的自我修复能力（5/5 P1 清偿、
门禁净增 64 测试、CHANGELOG 零失配），但特性批的规模让三类「第二段最后一公里」浮出水面——授权边界
（读接口从未被当回事）、部署拓扑（埋点最深的部分没通电）、统计口径（采样策略互相打架、价格表与模型
默认值失配）**。它们共同指向一个元教训的细化：前五轮的「宣称↔实现↔验证」三层对账需要加上第四层——
**「集成↔通电」**：一个被完整实现、完美测试、忠实记录在 CHANGELOG 里的特性，如果部署拓扑里没有它的
位置，它就不存在。

同时必须公正记录：这是六轮中代码质量绝对值最高的 HEAD——mypy strict 144 文件零错误、pytest 545 通过、
分布式锁/fencing/对账三件套达到生产数据库中间件水准、认知栈九个 prompt 全部 YAML 外置、前端 TS 严格度
top-decile。项目的短板已经从「会不会做」彻底转移到「有没有人从用户和运维的视角把做完的东西走一遍」。

**六轮总评：7.8 / 10（十维）；安全与隐私单列 6.5。**
下一步的最高杠杆动作有两个：**把读接口的授权走查变成制度**（R6-H1/H2 是五轮安全审查的集体盲区，
证明单点修复不等于面覆盖）；**给 observability profile 里缺的那盏灯通电**（Langfuse + 采样权移交，
一天工作量，可观测性维度即可回到 9 分区间）。

---

## 附：本轮审查方法与覆盖声明

- 六路并行深审各自产出独立引用报告：世界引擎/Tick/Action 全链（1484 行 tick.py 通读+并发模型推演）/
  认知机制 21 项清单+治理验证 / 16 表 ERD+19 迁移+全 Redis 键族地图+双写顺序实证 / 消息双通道全 hop+
  ReAct 十步流+18 工具盘点 / 12 路由器端点清点+安全面走查+75 测试文件评估 / 六组件×三信号覆盖矩阵+
  12 服务拓扑+28 页面前端走查；
- 全部 6 个 P1 结论由主审源码二次亲读确认后才定性（characters.py:600-645、memory.py:114-164、
  compose/.env.example langfuse 零命中、tracing.py:114 vs otel-collector.yml、tick.py:1422-1423 +
  config.py:126-127、onebot.py:678-679）；
- 质量门禁在 HEAD `8e81266` 本地实测：ruff check 通过、ruff format 219 文件合规、mypy strict 144 文件
  零错误、pytest **545 通过 98 跳过**（51.5s，较五轮净增 64）、前端 oxlint+oxfmt+tsc 全部通过；
- 未覆盖声明：LLM 实际生成质量（需真实 key 长程观察）、GitHub Actions 真实运行日志（smoke 修复以
  CHANGELOG 声明的本地实证为准，未独立复核 Actions 日志）、QQ 真实账号端到端（需 NapCat 环境）、
  多实例水平扩展演练（架构单机假设明确）、Windows 宿主外 OS 兼容性。
