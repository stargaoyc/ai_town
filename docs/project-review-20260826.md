# AI Town 全面审查报告·第五轮（2026-08-26，HEAD `871965a`）

> **文档定位**：对[首轮](project-review-20260824.md)（基线 `dabecc9`）、[二轮](project-review-20260824-round2.md)（基线
> `0da5e79`）、[三轮](project-review-20260825.md)（基线 `1e8cc86`）、[四轮](project-review-20260825-round4.md)（基线
> `900c73d`）之后的**第四轮修复批**（`900c73d..871965a`，9 commits，含 2 P0 + 5 HIGH + 全部 P2/P3 的整改）
> 的全面独立重审。本轮方法：六路并行深审（认知机制 / ReAct 决策链路 / 消息与多端触达 / 数据持久化 /
> 可观测性 / 部署与前端 UX）+ 主审对全部 P0/P1 结论源码二次亲读 + 质量门禁本地实测。所有结论基于当前
> 工作区源码直接阅读，关键结论附 file:line 证据。
>
> **严重度定义**（沿用前四轮）：P0=核心功能静默失效/发布阻断；P1=功能性 bug/承诺违约/结构性缺陷/隐私泄露；
> P2=纵深防御缺口/文档漂移/性能隐患；P3=瑕疵。

---

## 一、执行摘要

**一句话结论：四轮修复批 16 项核验 15 项属实且质量扎实（ReAct 死代码真复活、隐私边界真收紧、备份/维度/
保留策略真闭环，门禁实测全绿），但本轮以「接缝的端到端验证」为标尺下钻，发现了修复批自身引入或暴露的
五类新断裂——①CI 部署冒烟作业红到货（两层确定性失败，本机实证复现）；②主动分享的决策闸门字段从未进入
schema，整条特性链路死代码；③日记幂等键世界时/真实时错配导致重复生成；④multimodal_structured_output
完全游离于预算/熔断/可观测体系之外；⑤OneBot 接入令牌默认可选。**

本轮最重要的三个判断：

1. **「修好了」与「能跑通」之间还隔着一层部署真相**。R4-H4 要求的 deploy-smoke 作业确实加了
   （ci.yml:105-140），但在干净检出上必然两连败：`docker compose config -q` 阶段即因 profile 门控的
   grafana 服务要求 `${GRAFANA_ADMIN_PASSWORD:?}` 而插值报错（本机以与 CI 完全相同的环境变量集复现）；
   即使过了这关，backend 容器的 environment 只注入 DATABASE_URL/REDIS_URL/OTEL_ENDPOINT/TZ 四项，
   而 `config.py:18,76` 将 `openai_api_key`/`jwt_secret` 声明为必填——容器在 import 期即崩，健康等待
   必然超时。该门禁只在带未跟踪 `.env` 的开发机上能绿——**它守护的事故类别至今未被关闭**。
2. **README 特性表再次出现宣称↔实现断裂，且这次藏在 schema 里**。tick.py:864 读取
   `proactiveShareIntent`、tick.py:400 以其触发主动分享，但决策 schema（tick.py:774-808）从未声明该
   字段——structured_output 按 schema 建 pydantic 模型会静默丢弃额外键，示例输出也不会包含它。
   该布尔恒为 False，「角色在 Tick 中产生分享意图时主动推送」（README 特性表第 8 行）自诞生起不可达。
   讽刺的是：分享的概率矩阵、双冷却、平台扇出、投递顺序修复全都真实存在——它们只是永远等不到那个信号。
3. **认知闭环补上了治理，却漏了自家回归**。四类无界数据纳入 retention、评分公式收敛单一来源、维度守卫
   fail-fast 全部属实；但 H1 世界时钟修复在批量路径漏转发 `world_now`（diary_service.py:273-277），
   幂等 EXISTS 用世界日期比对而落库 diary_date 是真实时间——两套日历必然失配，同一触发窗口内每个轮询
   周期都会再生成一篇重复日记。

### 维度评分对照

| 维度 | 首轮 | 二轮 | 三轮 | 四轮 | 五轮 | 变化主因 |
|---|:---:|:---:|:---:|:---:|:---:|---|
| 项目定位与演化 | 9 | 9 | 9 | 8.5 | **8** | ReAct 宣称修复，但「主动分享」宣称被实锤为死代码 |
| 分层架构与模块边界 | 7 | 8.5 | 8.5 | 8.5 | **8.5** | 无新结构性问题；tick.py 2016 行上帝文件延续 |
| 多智能体交互与世界模拟 | 6 | 7 | 8 | 8.5 | **8.5** | 群聊共享上下文落地；免打扰/按用户节流连续三轮欠账 |
| 认知机制完备性 | 4.5 | 8 | 8 | 8 | **8** | 治理闭环补全；日记幂等/素材采样两处新缺陷抵消增益 |
| ReAct 工具调用 | 8 | 8.5 | 8.5 | 3 | **8** | 复活属实+回归测试锁定；残余无条件注入与媒体占槽 |
| 数据持久化设计 | 8 | 8.5 | 7.5 | 7.5 | **8** | 备份/维度/幽灵索引/文档漂移修复；HNSW churn 新隐患 |
| 全链路可观测性 | 9 | 9.5 | 9 | 8 | **8** | receiver/预算/失败记录接线；span 3/16 与采样缺口原样 |
| 部署与工程化 | 7.5 | 8 | 6.5 | 6.5 | **6** | 冒烟作业红到货——比没有更危险（红灯疲劳风险） |
| 前端工程化与 UX | 6.5 | 7.5 | 7.5 | 8 | **8** | 维持高位；聊天小窗/轮询残留/error 组件未动 |
| 长期运行风险治理 | 3.5 | 7.5 | 7 | 7.5 | **7.5** | retention 扩容；归档行「出生即待死」交互缺陷 |
| **十维均值** | **6.9** | **8.1** | **7.85** | **7.4** | **7.85** | |
| 安全与隐私 | – | – | – | 4.5 | **7** | R4-H2/H3 修复扎实+测试锁定；OneBot token 可选残留 |

> 均值回升不是偶然：这是五轮以来第一次「上一轮全部 HIGH 在一轮内清偿」的记录。扣分点集中在**新引入的
> 接缝缺陷**（smoke 红到货、share intent 死字段、日记时区错配）——它们共同指向同一个根因：**修复的验证
> 手段仍停留在单元层，跨组件组合行为没有端到端断言**。

---

## 二、四轮修复批核验：15/16 属实

对四轮报告所列问题的修复提交逐项抽查（关键 HIGH 由主审亲读 diff 二次确认）：

| 四轮问题 | 判定 | 本轮核验证据 |
|---|:---:|---|
| R4-H1 ReAct 死代码 | ✅ 已修 | `_resolve_action_id`（tick.py:72-88）将 `use_tool` 作为保留字豁免候选校验；循环提取为 `_run_react_loop`（tick.py:879-923，硬上限 3 轮+强制 wait）；ToolRegistry 新增必填参数校验；test_react_loop.py 锁定豁免逻辑+循环编排+真实 registry 校验路径。主审亲读 diff 确认全链贯通 |
| R4-H2 匿名读记忆 | ✅ 已修 | PUBLIC_GET_PREFIXES 移除 characters/memories 前缀；新增 PUBLIC_EXACT_PATHS 精确豁免 alerts webhook（自带 bearer 鉴权）；test_auth_middleware.py 79 行锁定边界含前缀泄漏负例 |
| R4-H3 发送身份伪造 | ✅ 已修 | messages.py:64-65 JWT sub ≡ body.user_id 强制一致，API Key 豁免走机器桥接；test_messages_send_identity.py 锁定三态 |
| R4-H4 CI 零部署验证 | ❌ **红到货** | ci.yml:105-140 作业存在但两层确定性失败，见 §三 R5-H1 |
| R4-H5 frontend 0.0.0.0:80 | ✅ 已修 | compose `"127.0.0.1:80:8080"`；全部 12 服务端口回环绑定 |
| R4-H6 备份短板 | ✅ 大部修复 | pg_dump 改 `-Fc`+原子改名+14d 清理；新增 redis_backup.sh（`--rdb` 服务端一致性快照）；restore_drill.sh 真 pg_restore 进一次性容器+退出码门禁；同主机限制诚实文档化但未解决 |
| R4-H7 EMBEDDING_DIM env 治理 | ✅ 已修 | ORM 钉死 HALFVEC(2048)；startup_checks.py:47-72 以 format_type(atttypmod) 解析物理列维度，失配 RuntimeError fail-fast，main.py lifespan 接线 |
| R4-M1 文档↔schema 七处漂移 | ✅ 大部修复 | 迁移 0016 补建 idx_plans_char_status/idx_refl_char_time 且 ORM 同步声明；data-model.md 校正；残留 3 处小漂移见 §三 R5-L 组 |
| R4-M2 Alertmanager receiver 空 | ✅ 已修（带孔） | webhook→backend `/system/alerts/webhook`（bearer 常时比较+403 兜底）；但空 token 开箱即静默丢失，见 R5-P2 |
| R4-M3 span 覆盖 3/17 | ⛔ 未动 | 手动 span 仍恰 3 个（world.tick/character.tick/embedding.batch），决策链内部黑盒依旧 |
| R4-M4 HTTP path 高基数 | ✅ 已修 | metrics.py:212-217 改用 scope["route"] 模板名，未匹配路由归入 "unmatched" 单序列 |
| R4-M5 预算绕过两处 | ✅ 大部修复 | embed/embed_multimodal/multimodal_chat 全部接入检查+记账+指标；**但 multimodal_structured_output 整体游离在外（R5-H4）** |
| R4-M6 Langfuse 不记失败 | ✅ 已修 | trace_llm_error 新增并接入 chat/multimodal_chat/structured_output 三条 except 路径；embed 路径未接（P3）；版本钉 2.x 与 docs 3.x 漂移仍在 |
| R4-M7 四类无界数据 | ✅ 已修 | run_cognition_retention_cycle（loops.py:532-604）：tier-1 反思/日记/PM compacted 条目/archive 行各带保留期开关；tier-2 元反思永久保留设计合理；交互缺陷见 R5-M2 |
| R4-M8 评分公式双写 | ✅ 已修 | _HYBRID_SCORE_SQL 模块常量单一来源（memory_repo.py:44-48），search_hybrid 与 search_hybrid_global 共用（:468/:526），含 GREATEST(0,·) 时钟钳制 |
| R4-M9 structured_output 无重试 | ✅ 已修 | client.py:739-744 解析失败重试一次，二次失败才抛出 |
| R4-M10 move 参数契约 | ✅ 已修 | move.py params_schema 声明 required target_scene+_action_param_hint（tick.py:102-120）自动渲染进候选文本 |
| R4-M11 关系增量部分提交窗 | ✅ 已修 | 工具关系增量暂存 context["pending_relation_deltas"]，随 ActionRecord 主事务统一落库（tick.py:1335-1352），任一失败整体回滚 |
| R4-M12 Web WS 无背压 | ✅ 已修（带新 bug） | 10s 超时+死连接驱逐覆盖三个发送点；驱逐非身份校验新 bug 见 R5-M4 |
| R4-M13 分享扇出单 commit 收尾 | ✅ 已修（带新 bug） | 先 commit 落库后 spawn_background 推送（强引用注册+关停排空）；会话污染新 bug 见 R5-M5 |
| R4-M14 群聊上下文割裂 | ✅ 已修 | Redis 环（20 条/24h TTL）读旧录新，chat.yaml 注入 [群聊上下文]，token 上界 ~2KB |
| R4-M15 反思 embedding 失败永久 NULL | ⚠️ 文档化豁免 | docstring 明示「反思低频不值得建重试队列」——有意接受而非修复；语义检索跳过 NULL 行 |
| R4-L1~L8, M16 杂项 | ✅ 全部落实 | chat_with.yaml 删除/rec_ver 孤儿键清理/jaeger badger 卷/全服务 mem_limit/vitest jsdom/登录提示 DEV 门控/群活动每对每日限额（Redis SETNX 25h TTL） |

**结论：修复执行力与诚实度维持五轮纪录最高水准，唯 R4-H4 的修复本身需要修复。**

---

## 三、本轮新发现问题总览

### P1（HIGH）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| R5-H1 | 部署 | **deploy-smoke 作业红到货**：第一层，CI 环境只设 POSTGRES_PASSWORD/REDIS_PASSWORD/ENVIRONMENT（ci.yml:112-115），而 profile 门控的 grafana 服务要求 `${GRAFANA_ADMIN_PASSWORD:?}`（docker-compose.yml）——compose 对未激活 profile 的服务同样做插值校验，`config -q` 即失败（本机以同变量集实证复现）；第二层，backend 容器 environment 仅注入 DATABASE_URL/REDIS_URL/OTEL_ENDPOINT/TZ，缺 OPENAI_API_KEY/JWT_SECRET（config.py:18,76 必填）→ import 期 ValidationError → 容器崩溃循环 → 健康等待必超时。该作业只在有未跟踪 .env 的开发机上可绿 | ci.yml:105-140; docker-compose.yml backend.environment/grafana; config.py:18,76 |
| R5-H2 | 认知/产品 | **proactiveShareIntent 不可达，主动分享特性死代码**：决策 schema 六字段不含 proactiveShareIntent（tick.py:774-808），structured_output 按 schema 建 pydantic 模型丢弃额外键，schema 派生的输出示例亦不会包含；tick.py:864 读取恒得 False，tick.py:400 闸门永不开启。概率矩阵/冷却/扇出/投递顺序全套机制真实存在但永无触发信号。README 特性表第 8 行「主动分享」宣称失效 | tick.py:774-808 vs :864,:400; client.py _schema_to_pydantic create_model |
| R5-H3 | 认知 | **日记幂等键世界时/真实时错配**：generate_diaries_for_all_characters 接收 world_now 用于幂等 EXISTS（diary_service.py:260），但调用 generate_diary 时未转发（:273-277）→ effective_world_now 回退 real_now（:124）→ 落库 diary_date 为真实日历时间。世界时钟约 20 倍速推进且纪元任意，两套日期必然失配 → 幂等检查永不命中，同一触发窗口内每个 30min 轮询都重复生成（周记/月记/年记全日触发窗口内 2-3 篇重复）。H1 修复的回归/不完整落地 | diary_service.py:124,184,255-277; loops.py:149 注释宣称「归属与幂等键均使用世界时间」 |
| R5-H4 | 成本/可观测 | **multimodal_structured_output 完全游离于成本契约之外**：无 _check_cost_control、无熔断、无 LLM_CALL_TOTAL、无 Langfuse、无预算记账（client.py:808-846）——预算熔断与「埋点即契约」在该活代码路径上整体失效。叠加 generate_image/generate_video 同样不入账（media.py:12 自认「费用仍不可估」，MEDIA_GENERATION_TOTAL 定义在 tools/media.py 局部而非中央 metrics.py） | client.py:808-846 vs :307/:414/:718 的规范路径 |
| R5-H5 | 安全 | **OneBot access_token 默认可选**：config.py:177 默认 None，onebot.py:589-601 仅在配置时校验——未设时任何能触达 /ws/onebot/v12 的客户端可伪造消息事件、烧 LLM 预算、驱使机器人向任意群/私聊发消息。至少应在启用适配器且未设 token 时启动告警 | config.py:177; onebot.py:589-601 |

### P2（MEDIUM）

| # | 域 | 问题 | 关键证据 |
|---|---|------|---------|
| R5-M1 | 认知 | 日记素材采样系统性偏置：get_by_character_and_time_range 按 timestamp 升序 LIMIT 100，再取 memories[-20:]——character_tick_seconds=30 下即使日窗口也有 ~144 条记忆，周/月/年窗口数千条；升序截断意味着日记素材永远是**窗口开头**的第 81-100 条旧记忆，近期经历完全缺席 | diary_service.py:145 + repo limit=100 默认 |
| R5-M2 | 认知 | 归档行「出生即待死」：archive 行继承 episodes[-1].timestamp（旧时间戳，loops.py:804-811），而 cognition retention 按 timestamp<now-365d 删除 archive——从 >365d 积压压缩出的归档在创建后 24h 内即被删除，压缩摘要从未被服务过即永久丢失。应按创建时间计龄 | loops.py:804-811 vs :591-597 |
| R5-M3 | 认知 | decision_tools.yaml 无条件注入：工具全部停用时 prompt 仍指示 use_tool 用法（tick.py:836-837 无条件 append）→ LLM 可能输出 use_tool → 失败观察 → 至多浪费 3 轮全量 prompt LLM 调用/tick（默认全启用配置不受影响） | tick.py:836-837; decision_tools.yaml:6-8 |
| R5-M4 | 消息 | WS 驱逐非身份校验：broadcast 清理注释写「仅当仍是失败时的同一连接才移除」但代码只判非 None（websocket.py:190-194）；send_to_user 失败路径 disconnect 直接弹键（:153）。广播期间重连的用户会被误杀新连接。应比较 `is ws` | websocket.py:153,190-194 |
| R5-M5 | 消息 | 分享扇出会话污染：per-conversation try/except 无 rollback（proactive_sharing.py:473-496），message_repo.add 即时 flush（message_repo.py:62-63）——一个约束失败即置 session 于 pending-rollback 态，后续全部 add 抛 PendingRollbackError 被吞，最终 commit 失败 → **整个扇出全丢**（比部分失败更糟） | proactive_sharing.py:473-500 |
| R5-M6 | 并发 | 失锁不变量窄于文档声明：docstring 称闸口保证「失锁后不再发生任何 PG 写入——含 chat_with 的关系/记忆写入」，但 (a) ReAct 循环内工具记忆写入（tick.py:979-993）独立提交且无失锁检查；(b) chat_with 关系更新+双记忆写入（tick.py:1565-1618）在入口闸之后、PG 事务闸之前独立提交——多轮对话生成期间失锁这些写入照常发生 | tick.py:1116-1118 vs :979-993,:1565-1618 |
| R5-M7 | 消息 | QQ 入站无速率限制：仅 per-message_id SETNX 去重（TTL 600s），刷屏群可按消息驱动 judge+reply 双 LLM 调用烧预算 | onebot.py 全文无 rate_limit |
| R5-M8 | 消息 | 免打扰时段仍缺位（连续三轮）：世界时间早安可落在现实凌晨 3 点；按用户节流同样缺位（上限按角色计，单次扇出至多 100 会话同时推送，超出静默丢弃） | grep quiet/dnd 全零命中; proactive_sharing.py:459-462 |
| R5-M9 | 数据 | memory_episodes 高频插入/删除 churn 无 autovacuum 调优（仅 character_states 有，0002:313-319）；HASH 分区不能时间裁剪，HNSW 索引膨胀长期恶化 | 0002 vs memory_episodes 分区定义 |
| R5-M10 | 数据 | 增长估算不一致：partition_scheduler 注释称 action_records+csh 合计约 175 万行/年（≈4800 行/天），而 character_tick_seconds=30 理论上限 20 角色×2880 tick/天≈11.5 万行/天——实际吞吐取决于 LLM 延迟，但容量规划不应依赖未测量的注释 | partition_scheduler.py:13-14; config.py character_tick_seconds |
| R5-M11 | 数据 | restore_drill PG 断言弱：行数仅 echo 不断言（characters rows=0 也 PASS），仅 Redis 断言 dbsize>0；无校验和 | restore_drill.sh:44-50,64-67 |
| R5-M12 | 消息 | /ws/chat 半迁移：dashboard 端点已支持 Sec-WebSocket-Protocol 子协议鉴权（RFC 合规 accept），chat 端点仍 query param 可用（websocket.py:287,317-325）；且前端零消费方——泄漏面休眠但存在，端点存在必要性存疑 | websocket.py:287,317-325,533-569 |
| R5-M13 | 前端 | 路由级 error/not-found 组件仍缺：__root.tsx 仅根级 ErrorBoundary，未知路由渲染 TanStack 默认裸文本 | __root.tsx; grep notFoundComponent 零命中 |
| R5-M14 | 前端 | 轮询残留 6 处：adminStatus/nearbyCharacters/onebotMessages/notifications 10s + logs/detailedMetrics 5s（queries.ts:124,197,225,353-358,361-366,398-403）；notifications 与 WS 失效双通道冗余 | queries.ts; qq-monitor.tsx:61-72 每 10s 拉 100 条 |
| R5-M15 | 前端 | 聊天区 256px/limit-20 未变：max-h-64 overflow-y-auto + useMessages limit=20，无分页/虚拟化 | characters.$characterId.tsx:269; queries.ts:110-116 |
| R5-M16 | 前端 | i18n 缺位（32 页全硬编码中文）；localStorage token XSS 权衡未文档化（nginx CSP script-src 'self' 实质缓解但工程文档零记载） | package.json 无 i18n 依赖; auth.ts:13-15 |
| R5-M17 | 可观测 | 采样与日志关联缺口原样：头部采样 0.5（tracing.py:114 直连 Jaeger 无 collector），logging.py:58-60 以 is_recording() 为门槛——未采样的一半流量日志无 trace_id，Logs→Trace 跳转对一半流量死亡；docs 宣称的 collector 尾采样+错误必采为虚构 | tracing.py:114; logging.py:58-60; observability.md:364-376 |
| R5-M18 | 可观测 | span 覆盖 3/~16 原样（docs 矩阵 16 类承诺 vs 实际 world.tick/character.tick/embedding.batch，后者甚至不在 docs 矩阵中——双向漂移）；决策链内部/消息跳数/工具调用黑盒依旧 | observability.md:71-88 vs grep trace_span |

### P3（LOW，择要）

| # | 问题 | 证据 |
|---|------|------|
| R5-L1 | main.py 残留旧维度检查死路径：information_schema.character_maximum_length 对 halfvec 返回 NULL → int(None) TypeError 被泛化 except 吞掉静默 no-op，与新守卫双写共存 | main.py:200-240 vs startup_checks.py:47-72 |
| R5-L2 | 维度守卫不覆盖 reflections.embedding（0015 halfvec(2048)），靠 memory_episodes 先炸间接保护 | startup_checks.py:56-66 |
| R5-L3 | 文档漂移残留×3：conversations 唯一索引名（doc idx_conv_user_char vs 实际 idx_conv_user_platform_char）、event_key 类型+默认值（doc TEXT DEFAULT '' vs 实际 VARCHAR(100) DEFAULT 'default'）、0014 的 idx_world_events_created_at 未入 doc | data-model.md:367,397,401-403 |
| R5-L4 | cognition/messages retention 单条无分批 DELETE——首次大积压运行锁/WAL 一把梭 | loops.py:556-597,607-626 |
| R5-L5 | plans 表终态行永不删除（expire_daily_plans 只翻状态）慢积累 | loops.py:474-499 |
| R5-L6 | compose 未显式设置 redis maxmemory/policy（隐式 noeviction 无限内存；当前写入皆有界，风险低但应显式化） | docker-compose.yml redis command |
| R5-L7 | reconcile pg_to_redis HSET 与并发 Tick Redis 写存在小竞态窗（新鲜度复检覆盖 PG 侧不覆盖 Redis 侧；下轮周期自愈） | reconcile.py:139-153 |
| R5-L8 | 跨角色全局向量检索隔离靠约定（admin-only 文档化）非机制强制 | memory_repo.py:499-526 |
| R5-L9 | 群上下文环 LPUSH+LRANGE 实际最新在前，docstring 称「旧→新顺序」相悖；bot 自身回复不入环（多角色群互相看不见回复） | onebot.py:984-996 |
| R5-L10 | self_id 双缺（事件缺失+配置未设）时自消息抑制静默失效 | onebot.py:750-752 |
| R5-L11 | 工具调用记忆 episode 独立提交于主事务之外——主事务回滚后留下描述未生效效果的孤儿记忆 | tick.py:979-993 vs :1335+ |
| R5-L12 | tool_name 缺失时 _execute_tool 返回 None → 无观察注入 → 盲重决策烧满剩余轮次 | tick.py:945-947,897-906 |
| R5-L13 | 媒体视频工具同步轮询至 ~10 分钟占用 Tick 槽+信号量槽+分布式锁（docstring 已自知） | media.py:45-58; client.py 视频轮询 |
| R5-L14 | tick.py 2016 行上帝文件延续；PerceptionBuilder/SocialActionHandler 拆分仍未做 | tick.py 全文 |
| R5-L15 | 告警阈值 $8 硬编码不随 LLM_DAILY_BUDGET_USD 联动；ai_town_alerts_received_total 无面板无规则——告警投递死亡无人知晓 | alerts.yml:71; dashboards |
| R5-L16 | Langfuse 无 session_id/user_id 关联；成本面板 Counter 裸 gauge 跨重启误导；langfuse_integration.py 装饰器路径成死代码 | langfuse_tracing.py 全文 |
| R5-L17 | 日志文件 handler 无轮转；sanitizer 全库仅 1 处调用（main.py 启动时 Redis URL）——日志管道实际无脱敏 | logging.py:137; sanitizer.py |
| R5-L18 | CI 契约守卫守着手工导出的 openapi.json——后端路由变更不重导出则守卫照常通过（应对 CI 加 export_openapi.py+diff 步骤） | ci.yml:96-99; scripts/export_openapi.py |
| R5-L19 | export.tsx 内联 queryKey 违反项目自身 queryKeys 契约；.env.example 缺 4 个新保留旋钮（reflection/diary/pm_entry/archive retention days） | export.tsx:82; config.py:59-62 |
| R5-L20 | 认知演化债四项延续：importance 写时冻结无 last_accessed 通道、情节矛盾消解仅在 PM 压缩一隅、deadline 仅展示排序无强制过期、QQ 号数字 ID 入 prompt | plan_repo.py:49; person_memory_service get_top_users_context |

---

## 四、分维度详评

### 4.1 项目定位与差异化（8）

「世界驱动的陪伴」定位继续成立，且本轮 ReAct 复活让 README 特性表第 5 行重新与实现对齐。但第 8 行
「主动分享」被实锤为 schema 级死代码（R5-H2）——这是继四轮 ReAct 之后第二处「特性表宣称↔实现断裂」，
且模式相同：**每个零件都合格（概率矩阵/冷却/扇出/投递语义），唯独装配信号没接上**。定位无需调整；
需要的是一次系统性的「宣称清单↔行为测试」对账——README 特性表每一行都应该有一条能证明它的端到端测试。

### 4.2 分层架构与模块边界（8.5）

runtime 回调解耦格局继续经受考验：本轮全部修复落在既有分层缝隙内（安全收窄在 middleware、ReAct 复活在
core、retention 扩容在 scheduler、备份在 docker 编排层），无一例跨层直调。两点保留：(a) tick.py 2016 行
上帝文件延续，_perceive ~250 行装配函数与 chat_with/group_activity 特化处理器仍内联（R5-L14）；
(b) PersonMemoryService/DiaryService 的 Any 类型注入风格分裂未收敛。

### 4.3 多智能体交互与世界模拟（8.5）

本轮加分项：**群聊共享上下文落地**（Redis 环 20 条/24h，读旧录新防重复计数，chat.yaml 注入上界 ~2KB
token 控制）——多方对话答非所问的结构性根源被以最小手术方式缝合。agent-agent 多轮对话+关系质量化
（四轮已落地）运转正常。剩余差距不变：免打扰时段与按用户节流连续三轮欠账（R5-M8）、关系类型升级阈值
静态公式、bot 回复不入共享环（R5-L9）。

### 4.4 认知机制完备性与可演化性（8）

治理面显著补全：四类无界数据纳入分级 retention（tier-2 元反思永久保留的设计判断正确）、评分公式收敛
单一 SQL 来源、维度守卫启动期 fail-fast。规划系统小幅进化：deadline 字段已被解析、排序（deadline.asc()
nulls_last）并渲染进决策 prompt，progress 可经 planChanges 更新——但仍属软引导（无强制过期、无紧迫度
信号）。本轮两个新缺陷集中在日记子系统（幂等错配 R5-H3、素材采样偏置 R5-M1），加上归档行出生即待死
（R5-M2），说明**认知产物的「写入侧」质量明显好于「生命周期侧」**——前者经八轮 prompt 打磨，后者是
新近补建的代码。Prompt 工程质量维持高位：reflection 编号溯源+服务端钳制仍是全栈最强模式；解析范式
分裂（structured_output vs 手写括号剥离）依旧存在于 PM/压缩/群叙事三条路径（R5-L20）。

### 4.5 ReAct 工具调用（8）

**复活判定：成立。** 主审亲读 diff + 代理全链追踪双重确认：use_tool 保留字豁免（tick.py:72-88）→ 循环
执行（registry 启用检查+必填参数校验）→ 观察 800 字符截断回注（decision_react.yaml）→ 再决策 → 最终
action 落地；状态变更类工具 delta 经内存 context 流入主事务，关系增量暂存随主事务原子落库（R4-M11
修复正确）。终止保证：range(3) 硬上限+强制 wait（wait 恒在候选：scene=None/precondition=None）。
测试深度合格而非完美：_resolve_action_id 豁免矩阵+循环编排（stub _decide/_execute_tool）+真实 registry
参数校验各有锁定；缺口是无人用原始 LLM JSON 载荷穿过真实 _decide 断言 use_tool 幸存（组合缝隙间接覆盖）。
残余：工具全停时指令仍注入（R5-M3）、tool_name 缺失盲轮（R5-L12）、媒体工具 10 分钟占槽（R5-L13）。

### 4.6 数据持久化设计（8）

核心盘继续扎实且本轮补齐了运维短板：pg_dump -Fc+原子改名、Redis 服务端一致性 RDB 快照、真恢复演练
（退出码门禁）、幽灵索引迁移补建且 ORM 同步、维度守卫 atttypmod 化。查询-索引匹配审计未发现新的
seq-scan 热点；混合检索单一来源含时钟回拨钳制；reconcile 方向翻转+新鲜度复检+版本单调三件套完好；
失锁四闸口位置正确。扣分集中在新识别的长期隐患：memory_episodes 的 HNSW churn 无 autovacuum 调优
（R5-M9）、容量估算注释与配置现实差一个数量级（R5-M10）、restore_drill 断言弱（R5-M11）、备份同主机
缺口诚实但未解决。

### 4.7 全链路可观测性（8）

「最后一公里」三项接线完成：告警回流后端（bearer+常时比较+结构化日志+计数器）、LLM 预算全覆盖
（embed/multimodal 入账入指标）、Langfuse 失败调用记录（三条主路径）。RBAC 改造经查**未破坏抓取**：
Prometheus 直连 backend:8000 容器网络，/metrics 挂载点在 AuthMiddleware 的 /api/* 边界之外，nginx 404
只挡公网代理层——这次接缝改造是安全的。Grafana 三仪表盘 16 个指标名逐一核对零幽灵。但契约兑现率
未提升：span 3/16、采样缺口、sanitizer 近乎未接线、alerts_received_total 无监控（R5-M17/M18/R5-L15/17），
且 multimodal_structured_output 成为成本观测的新黑洞（R5-H4）。

### 4.8 部署与工程化（6）

拓扑面持续改进：全端口回环、Jaeger badger 持久化、12 服务全员 mem_limit、redis-backup 服务、CSP
script-src 'self'、动态 resolver 根治 nginx 502 类故障、ENVIRONMENT=production 启动门禁拦截默认凭据。
但本轮最重发现在此：**deploy-smoke 红到货**（R5-H1）——一个必然失败的门禁比没有更危险：要么阻塞所有
合并逼团队立刻修复，要么被养成「CI 红灯是常态」的破窗习惯。经验复现给出精确修复清单：job env 补
GRAFANA_ADMIN_PASSWORD；backend.environment 补 OPENAI_API_KEY/JWT_SECRET 注入（或 CI 专用 compose
override 文件）。另：alembic 内联 CMD 的横向扩展天花板（Dockerfile 注释自知）、浮动基础镜像标签、
openapi.json 手工导出使契约守卫守着可能过期的真相源（R5-L18）。

### 4.9 前端工程化与 UX（8）

架构底盘维持同类项目罕见水准：32 文件路由+自动代码分割、React Compiler 1.0 真实启用、tsconfig 极限
严格（exactOptionalPropertyTypes/noUncheckedIndexedAccess）、生成类型+CI 契约守卫、queryKeys 集中契约、
Zustand 仅 auth/toast 干净二分。UX 纪律执行到位：加载/错误/空态三态齐全（含过滤后空态）、a11y 高于
社区平均（dialog role/aria-live toast/switch role/焦点管理/Escape/IME 组合输入保护）、破坏性操作确认
全覆盖（删角色带 pending+错误展示）、移动抽屉导航完整。遗留热点与四轮清单一致未动：聊天小窗、六处
轮询、error/not-found 组件、i18n（R5-M13~M16）。新页面（person-memory/vector-search/compare/export/
import）质量与老页面一致；export.tsx 一处内联 queryKey 违反自家契约（R5-L19）。

### 4.10 消息服务与多端触达（8）

QQ 通道机械成熟度已达生产级：超时+跨连接故障转移+心跳新鲜度排序、at-least-once Streams+PEL 恢复+
DLQ 五次上限、三层 @检测、自消息早退、多段拟人节奏、群共享上下文。Web 侧背压补齐后两侧差距缩小，
但四个新缺口拉低上限：驱逐非身份校验（R5-M4）、扇出会话污染（R5-M5）、/ws/chat 半迁移+零消费
（R5-M12）、token 可选（R5-H5）。战略层欠账不变：免打扰、按用户节流、跨渠道身份合并、「多渠道」
实为 1.5 渠道（lark/internal 枚举残留，ChannelAdapter 协议不存在，扇出硬编码 qq_ 前缀——新增渠道
预估 3-5 人日/个）。

### 4.11 长期运行风险治理（7.5）

并发域保持收尾良好：失锁四闸口、move 矩阵校验降级、reconcile 版本仲裁、embedding worker SKIP LOCKED+
退避+熔断、跨角色对话排序加锁。记忆膨胀主战场闭环扩容（四类数据纳入 retention），但三个新增长隐患
入场：归档行出生即待死（R5-M2）、HNSW churn 无调优（R5-M9）、plans 表终态行慢积累（R5-L5）。容量
规划第一次出现与现实脱节的量化注释（R5-M10）——建议以实测有效 tick 周期替代理论值。

### 4.12 用户体验专项

QQ 端：三层回复决策+群共享上下文+多段节奏构成目前最好的对话体验底座；凌晨三点早安（免打扰缺位）
仍是单个最影响真实体验的欠账。Web 端：信息架构完整、三态纪律、a11y、移动端均高于平均；痛点仍是
聊天小窗与运维页轮询功耗。主动分享体验当前为零——不是做得不好，而是根本不会发生（R5-H2）。

---

## 五、技术选型评价

| 选型 | 评价 |
|------|------|
| LangChain 1.x | ✅ 维持四轮结论：使用面收敛于 client/fallback 两文件，替换无功能收益 |
| PostgreSQL 18 + pgvector(halfvec/HNSW) | ✅ 正确；本轮补齐备份/索引/维度治理；需补 memory_episodes autovacuum 调优与容量实测 |
| Redis 8（锁/Streams/状态/预算） | ✅ 用法克制；建议显式 maxmemory 策略 + 群环/budget 键 TTL 审计已通过 |
| OneBot v11/v12 反向 WS | ⚠️ 架构正确但 access_token 应改必填或启动强警告（R5-H5） |
| React 19 + React Compiler + TanStack + openapi-typescript | ✅ 自洽；契约守卫需补 openapi.json 再生步骤才是闭环 |
| OTel + Langfuse + LGTM 栈 | ⚠️ 组件齐全；receiver 已接但 span/采样/sanitizer 三项契约缺口进入第三轮未动 |
| Docker Compose + CI | ⚠️ 拓扑成熟但 smoke 门禁红到货——先修 R5-H1 再谈部署可信 |

---

## 六、修复路线图（优先级排序）

### 立即（P1，预计 3-4 人日）

1. **R5-H1 修复 deploy-smoke 本身**：ci.yml job env 补 `GRAFANA_ADMIN_PASSWORD`；backend 容器环境补
   `OPENAI_API_KEY/JWT_SECRET/ADMIN_*` 注入（推荐 CI 专用 compose override 文件避免污染生产模板）；
   验证标准=GitHub Actions 实际绿一次；
2. **R5-H2 复活主动分享或诚实降级**：决策 schema 补 `"proactiveShareIntent": {"type": "boolean"}`
   （一行）+ decision.yaml 提及语义 + stub-LLM 测试断言 True 能穿透 structured_output 到达 tick.py:400；
   若暂缓则 README 特性行降级「规划中」；
3. **R5-H3 日记幂等修复**：批量路径转发 `world_now=world_now, window_start=window_start`（两参数）+
   回归测试断言同窗口二次调用 skipped；
4. **R5-H4 multimodal_structured_output 接入成本契约**：_check_cost_control + record + trace_llm_error
   三件套，与兄弟路径对齐；generate_image/video 至少入 MEDIA_GENERATION 指标中央化；
5. **R5-H5 OneBot token 启动强校验**：适配器启用且 token 未设时 log warning 或直接 fail-fast（推荐后者，
   与项目 fail-fast 哲学一致）。

### 两周内（P2 择要）

6. R5-M4 驱逐身份校验（`is ws` 比较）+ R5-M5 扇出 except 内 rollback；
7. R5-M1 日记素材采样修正（limit 提升+分层抽样或按重要性加权）+ R5-M2 archive 计龄改创建时间；
8. R5-M3 工具指令注入按可用性门控；R5-M6 失锁不变量与实现对齐（补两处闸口或修订 docstring）;
9. R5-M9 memory_episodes autovacuum 调优迁移；R5-M10 容量注释以实测替换；R5-M11 drill 断言行数>0；
10. R5-M17 采样联动（logging 改从 get_span_context() 取 trace_id 无论 recording 与否——一行修复半个
    可观测性缺口）；R5-M12 /ws/chat 迁移子协议或删除端点；
11. R5-M8 免打扰时段（第三轮催办，建议映射世界时间→接收者本地时段或全局 DNT 窗口）。

### 战略级

12. README 特性表↔端到端测试对账机制（每行宣称一条 e2e 断言，进 CI）；
13. ChannelAdapter 协议抽取（若第三渠道在路线图上）或 README 去掉「多渠道」措辞；
14. 认知演化债四项（importance rehearsal/矛盾消解/planner 一等化/deadline 强制）连续两轮未动，
    建议纳入 roadmap 明确里程碑；
15. 「无界增长巡检」指标族（stream length/分区行数/compacted/archive 行数入 Prometheus+告警）。

---

## 七、总评

第五轮审查的发现模式延续了四轮的「接缝」主题但换了象限：四轮的问题是「零件合格、装配线少一行」，
本轮的问题是**「修复批自己制造了新的接缝缺陷」**——smoke 门禁的两层环境断层、share intent 的 schema
缺字段、日记幂等的世界时漏转发，全部位于「修复动作与其验证手段之间的缝隙」上。这不是退步，而是
项目演进速度（七周 190+ commits）下修复批规模的自然伴生风险；它再次确认四轮的结论：**下一个杠杆点是
端到端行为断言，而不是更多单元测试**。

同时必须公正记录：这是五轮以来修复执行力最强的一批——15/16 核验属实、ReAct 从 3 分复活到 8 分、
安全与隐私从 4.5 修复到 7、门禁实测全绿（ruff/format/mypy 140 文件零错误/pytest 481 通过较上轮净增
27/frontend lint+typecheck 通过）、CHANGELOG 与代码零失配纪录延续。项目的工程文化——fail-fast 强迫症、
ADR 沉淀、诚实的自我文档化（backup.sh 注释自认同主机限制、media.py 自认费用不可估）——在同类项目中
仍处第一梯队。

**五轮总评：7.85 / 10（十维）；安全与隐私单列 7。**
下一步的最高杠杆动作只有一个：**让 deploy-smoke 真绿一次**——它是四轮 R4-H4 的未竟承诺，也是全部
五轮发现的元教训（宣称↔实现↔验证三层一致性）的最后一块拼图。

---

## 附：本轮审查方法与覆盖声明

- 六路并行深审各自产出独立引用报告（认知 21 机制清单+4 修复核验 / ReAct 十步决策流全链追踪+162 行
  测试评估 / 消息双通道全 hop+4 修复核验 / 16 表 ERD+索引审计+备份演练实测 / 六组件×三信号覆盖矩阵+
  RBAC 接缝专项 / 12 服务拓扑+32 页面 UX 走查），全部 HIGH 结论由主审源码二次亲读确认后才定性；
- 四轮 16 项问题逐项抽查（15 属实/1 红到货）；质量门禁在 HEAD `871965a` 本地实测：ruff check 通过、
  ruff format 210 文件合规、mypy strict 140 文件零错误、pytest 481 通过 92 跳过、前端 oxlint+oxfmt+tsc
  全部通过；
- deploy-smoke 失败为本机 docker compose 以 CI 同款环境变量集实证复现（隔离临时目录、无 .env），
  非 CI 日志推断；
- 未覆盖声明：LLM 实际生成质量（需真实 key 的长程观察）、Windows 宿主外的 OS 兼容性、多实例水平扩展
  演练（架构单机假设明确）、QQ 真实账号端到端（需 NapCat 环境）、GitHub Actions 真实运行日志访问。
