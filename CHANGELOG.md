# Changelog

本文件记录面向使用者的显著变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。项目当前处于快速迭代期（0.x），
破坏性变更可能在 minor 版本间发生。

## [Unreleased]

### Added

- **视频生成链路**：新增 media.generate_video_clip 工具（帧数自动对齐 8n+1、同步轮询 agnes-video-v2.0），出站净化支持视频直链转 [CQ:video]；⚠️ 同步生成较慢约 1-3 分钟，仅建议在用户明确要求时使用。
- **改写式记忆去重（B1）**：EmbeddingWorker 向量化时与同角色近 24h 记忆余弦比对（≥0.95 判定重复），重复行不落向量且检索/反思排除；迁移新增 `is_duplicate` 标记列。配置 `MEMORY_DEDUP_ENABLED` / `MEMORY_DEDUP_SIMILARITY_THRESHOLD` / `MEMORY_DEDUP_WINDOW_HOURS`。
- **daily 计划滚动过期（B2）**：创建超 TTL 的 active 当日计划随世界时间检查自动置 `expired`。配置 `DAILY_PLAN_TTL_HOURS`。
- **LLM 新建计划（B3）**：决策输出新增 `createPlanChanges`（title 必填、类型白名单、优先级钳制、单次最多 3 条），服务端绑定 character_id 落库。
- **传闻行为化表达（B4）**：最近听说的传闻注入决策 Prompt「听说的消息」段，角色可在对话中自然提起八卦而非沉默存档。
- **群活动（B5）**：新 Action `group_activity`——同场景 ≥3 人可触发临时小聚，单次 LLM 生成集体叙事并为全体参与者写共同经历记忆（related_characters 互指）、两两关系 +2（上限 100）。
- **数据库定时备份**：新增 `db-backup` 服务（`--profile backup` 启用）——pg_dump | gzip 按间隔写入 `./data/backups`（.part 临时文件原子改名防半成品），按保留天数自动清理；配置 `BACKUP_INTERVAL_HOURS` / `BACKUP_RETENTION_DAYS`。
- **冷启动恢复演练脚本**：`packages/backend/scripts/cold_start_drill.py`——清空 Redis 世界/角色状态键后执行与启动路径一致的 `rehydrate_states()`，校验快照 tick_id/weather 回灌、角色镜像全量恢复与字段抽查；本地实跑 5/5 通过。
- **容器日志轮转**：compose 全部服务统一 `json-file` 驱动 + 单文件 10MB×3 份上限（YAML 锚点一处定义），防止日志无限增长吃满磁盘。
- **移动端导航与操作反馈**：md 断点以下汉堡抽屉导航（此前七个板块不可达）；全局 toast 提示与共享确认对话框（清除全部通知 / 配置重置接入）。
- **QQ 发送链路可观测**：识别 OneBot action 响应帧并按成败计数（`ai_town_onebot_action_response_total`）；媒体生成调用量计数（`ai_town_media_generation_total`）。

- **反思跨期主题归纳**：批次反思改为编号记忆主题归纳（每主题一条 Reflection、来源精确挂链）；新增 tier=2 跨期元反思——累计反思足够多且冷却期满时，对既有反思再归纳「长期倾向」，决策注入时元反思优先。
- **记忆压缩归档**：retention 循环改两阶段——到期低价值记忆先按角色×月份 LLM 压缩成 `[归档]` 摘要行（豁免后续删除），压缩失败整组跳过绝不未压缩先删除；低于最小批的小组保持直删。配置 `MEMORY_COMPRESSION_ENABLED` / `MEMORY_COMPRESSION_MIN_BATCH`。
- **Person Memory 两层改造**：新增 `person_memory_entries` append-only 事实条目层——交互时 LLM 只抽取新事实追加（不再全文重写，根除 telephone game 漂移）；后台每 6 小时把 ≥阈值条目合并进主档并软归档；对话上下文 = 主档 + 最近未压缩条目。配置 `PERSON_MEMORY_COMPACT_THRESHOLD`。
- **Plan 层级体系与作息桥接**：计划类型扩展 `daily`（当日计划）；决策 Prompt 计划段注入类型/优先级/截止日全量信息；ScheduleSystem 作息档位经 `{schedule}` 占位符注入决策（含睡眠约束提示）；`get_active_plans` 按优先级+截止时间排序；修正「计划影响 precondition」的失真注释为实际的 Prompt 软引导机制。
- **群体动力学·传闻传播**：好友的高重要性经历（importance≥门槛、沿关系强度过滤）以第二手记忆扩散——内容取源记忆原文模板拼接（非 LLM 编造）、importance 减半递减、每好友每窗口最多一条；经既有检索管线自然回流决策。配置项 `GOSSIP_ENABLED` / `GOSSIP_IMPORTANCE_THRESHOLD` / `GOSSIP_WINDOW_HOURS` / `GOSSIP_MAX_PER_TICK` / `GOSSIP_RELATION_MIN`。
- **群体动力学·共同经历标记**：Tick 记忆沉淀时将同场景在场角色写入 `memory_episodes.related_characters`，激活预留字段供共同经历查询与传闻溯源使用。
- **认知产物回流上下文**（20260824 审查 P0）：反思（最近 5 条）与最近日报注入角色决策 Prompt；Person Memory 注入对话 system prompt——此前三类认知产物只写不读，「我记得你」未在模型上下文生效。
- **记忆生命周期治理**：`memory_retention_loop` 每 24 小时按重要性分级清理老记忆（≤3 级 90 天、4-6 级 180 天，≥7 永久保留），`MEMORY_RETENTION_ENABLED` 可关闭。
- **Person Memory 热度衰减**：后台每 6 小时将超 14 天未交互的记忆热度减半。
- **记忆写入去重**：内容归一化后与近 24 小时比对，命中即跳过写入。
- **计划变更落库**：LLM 决策的 `planChanges` 在 Action 事务内经归属校验写入 plans 表（此前解析后丢弃）。
- **前端冒烟测试**：vitest 覆盖 queryKeys 契约与认证 store，CI frontend job 接入。

- **多模型备用源**：`LLM_FALLBACK_SOURCES` 可配置多个 OpenAI-compatible 源，调用失败自动切换，失败源冷却 5 分钟后作为末位兜底。
- **World Tick fencing 校验**：写路径前校验 Redis 锁 token 仍归本实例，旧 leader 停顿苏醒不再双写世界状态。
- **消息断连兜底队列**：OneBot 消息事件先持久化到 Redis Streams（处理成功后确认），崩溃/重启后自动重放未确认条目；幂等由 SETNX 去重保证，毒消息超限转死信流。
- **reconcile 版本感知仲裁**：对账基线记录 PG version，PG 在基线后发生过写入时修复方向翻转为 pg_to_redis——API 刚提交的合法变更不再被陈旧 Redis 回滚。
- **Langfuse Tick 父子追踪**：以 Tick 为根 trace，同 Tick 内全部 LLM 调用经 ContextVar 自动挂为子 generation，形成可展开的调用树。
- **类型收敛试点**：world/system 域端点挂载命名 response_model（WorldStateOut/HealthOut 等），openapi 重导出后前端 WorldState 切换为生成引用；CI 增加 API type contract guard。
- **前端组件套件**：ui.test.tsx 覆盖 ui.tsx 全部 12 个导出组件（27 测试）。
- **Redis vs PG 状态对账**：后台每 10 分钟 diff 两库并自动修复（键缺失回灌、字段漂移以 Redis 为准修正 PG）；新增指标 `ai_town_reconcile_drift_total` / `ai_town_reconcile_repair_total`。
- **Prometheus 告警规则**：11 条规则覆盖世界 Tick 停摆、角色 Tick 失败率、LLM 预算/熔断、状态漂移、Redis 断连、5xx 错误率。
- **`/ws/dashboard` 实时推送**：登录后订阅仪表盘帧（世界状态 + 通知未读数，每 5 秒），前端轮询降为 30 秒断连兜底。
- **openapi-typescript 类型生成管道**：`pnpm gen:api` 从后端 OpenAPI spec 生成契约类型。
- **集成测试基座**：`tests/integration/` 连真实 PostgreSQL + Redis（独立 `ai_town_it` 库经 alembic 迁移重建），服务不可达自动跳过；覆盖 Repository 层、分布式锁、状态对账。

### Changed

- **记忆混合检索改指数衰减**：`final_score = (sim*0.6 + importance*0.05) * (0.25 + 0.75*exp(-天数/30))`，老记忆得分有 25% 下限永不为负（原线性衰减使 22 天前记忆不可达）。
- **决策 Prompt 增强**：注入真实场景描述（容量/开放时段/活动）替换空占位；检索 query 拼入时段/情绪/计划标题。
- **chat_with 升级四句承接式对话（单次生成）**：一次 LLM 调用输出 4 句角色交替台词、第二轮承接第一轮话题，提升交互深度；非两次独立往返调用。
- **JWT 库替换**：python-jose（维护停滞）→ PyJWT；移除声明后零使用的 passlib[bcrypt]。
- **镜像 tag 固定**：Redis 统一 `redis:8-alpine`（compose/CI/README 三方对齐）；可观测性 profile 固定 prometheus/jaeger/alloy 版本。
- **容器自动迁移**：backend 启动前执行 `alembic upgrade head`。
- **main.py 瘦身**：844 → 约 500 行，三个后台循环下沉 `scheduler/loops.py`，AuthMiddleware 迁入 `auth/middleware.py`；core 层主动分享改为 runtime 回调，消除 core→messaging 反向依赖。
- **性能**：同场景角色感知复用一次性关系查询（消除 N+1）；工具启用状态增加 5 秒 TTL 缓存；`update_state` 每次写入自增 version；DB 查询耗时接入 Prometheus；Langfuse 埋点附带 OTel trace id。
- Docker 编排合并为单一 `docker-compose.yml`（原 `docker-compose.infra.yml` / `docker-compose-win.infra.yml` 删除）；PG 统一为 `pgvector/pgvector:pg18` 官方镜像、端口 5433、bind mount `./data/`。
- 消息分页改为 `(created_at, id)` keyset 双游标，修复同事务批量写入时游标漏数据；Action 时间线查询补 UUIDv7 tiebreaker。
- `EMBEDDING_DIM` 默认值对齐迁移 0005 的物理列 `halfvec(2048)`（此前默认配置下 embedding 写入必失败）。
- 场景拥挤度特性接通：角色移动时维护 `world:scene:visitors` 计数（此前恒为 0）。
- `/api/v1/actions/{id}` 返回真实 Action 字段（scene / allow_dynamic_duration / 全部带符号 cost 字段）。

### Fixed

- 认知回流断流（20260824 审查 P0）：反思 / Person Memory / 日记此前只写不读，现分别注入决策与对话上下文。
- 时间衰减缺陷（P0）：线性衰减使 22 天前记忆检索得分必为负，改指数衰减后老记忆保有 25% 得分下限。
- `planChanges` 死功能：LLM 决策的计划变更解析后被丢弃，现事务内落库（带角色归属校验）。
- 同场景角色感知 N+1 查询（注释宣称批量而实现逐角色开 session）。
- world_events 去重基线仅存内存，重启后首轮重复写入，现持久化到 Redis。
- 全仓 ruff 口径 42 错：删除包根遗留探针脚本 `_cycle_probe.py`。
- **日记世界时钟错位（20260825 三轮审查 H1）**：日记幂等键与记忆窗口此前用现实日期，而世界时钟以 20 倍速推进——每天最多产出 1 篇日报且「今天」实际汇总约 20 个世界日；现幂等键、归属日期与查询窗口全部对齐世界日历（1 世界日 = 72 现实分钟）。
- **反思幽灵计数（H2）**：`count_unreflected` 不过滤改写式去重标记且 `mark_duplicate` 不置已反思位，重复记忆永久滞留计数器使反思每 Tick 触发；双向修复并补一致性测试。
- **入站队列 XACK 空操作（H3）**：内联路径确认的条目从未经投递，XACK 无效导致每条消息被恢复循环二次处理、正确性悬于 600s 去重 TTL；成功处理改为 XACK+XDEL 彻底移除，事件流与死信流加 maxlen 裁剪。
- **崩溃恢复静默丢消息（H4）**：回复去重 SETNX 在处理开始抢占，「已去重未回复」窗口崩溃后重放被挡、消息永久丢失；认领移至发送前一刻，发送失败释放槽位允许重试。
- **机器人乒乓死循环（H5）**：无自消息排除且问候层零概率门控；现排除自身消息并对问候命中加 0.9 概率闸门。
- **看门狗失锁不中止（H10）**：续租失败仅记日志，Tick 失锁后继续写状态造成跨实例 double-tick 窗口；现置位 lock_lost 并在 Action 执行 / PG 事务 / Redis 镜像三处闸口中止。
- **部署暴露面（H6-H8）**：Redis 强制密码 + 基础设施端口绑定 127.0.0.1 + PG/Grafana 密码必填插值；`.env.example` 补齐 ENVIRONMENT 等 18 个缺失变量使生产密钥门禁可武装；Prometheus 抓取目标改为 backend:8000（Linux 上告警栈此前全盲）；nginx 安全响应头 + `/metrics` 不再公网代理。
- Tick/API 写入竞态收尾：move 端点 CAS 失败返回 409 不再写 Redis（M8）；reconcile 修复走版本自增路径并校验快照新鲜度（M9）；admin 双端点接入 leader fencing（M10）；importer 显式递增版本（M11）。
- 消息域长尾：OneBot action 响应帧识别与失败计数（M12）；回复路径跨连接 failover + 发送超时 + 半开连接驱逐（M13/M17）；恢复重放按 self_id 选同账号连接（M14）；群聊判定模型档位可配（M15）；媒体生成调用量计数（M18）。
- 前端：WS 重连计数成功后复位（M19）；中文输入法回车误发送（M20）；移动端汉堡导航（M21）；聊天发送失败静默吞没补 toast 提示 + 清除全部/配置重置加确认（M22/L10）；queryKeys 契约全量迁移（M23）。
- 数据层长尾：RANGE 分区保留生命周期（超期分区 DETACH+DROP，H9）；messages 定期清理任务（M1）；启动校验 EMBEDDING_DIM 与物理列一致（M2）；改写式去重 SQL 改 HNSW 可加速形态（M3）；world_events(created_at) 索引（M4）；ORM 元数据对齐 DDL（M5）；决策 Prompt 条目 500 字符截断（M7）；归档压缩传真实角色名（M24）；Person Memory preferences 增量合并（M6）；search_hybrid 负天数钳制（L1）；Prompt 用户标识剥离平台前缀（L3）；JWT 测试密钥加长（L7）。
- 集成测试探针仅做 TCP 检查（复审二轮 N1）：端口通但服务坏时 IT 以 error 收场，改 asyncpg `SELECT 1` + Redis `PING` 真实握手，环境缺失时正确 skip。
- compose 凭据硬编码（N3）：`POSTGRES_PASSWORD` / Grafana 密码改为变量插值（自动读根目录 .env），backend `DATABASE_URL` 与 PG 服务共用同一变量消除双真相源。
- 工具开关缓存无主动失效（N8）：toggle API 现即时清空进程内 5s TTL 缓存。
- Tick/API 并发写 last-write-wins 窗口（N4）：`update_state` 支持乐观锁 CAS（`expected_version` 条件更新 + 冲突重读重试一次），API 移动端点接入。
- planChanges 隐式兜底缺陷（N2 单测发现）：仅含 `planId` 的条目不再被默认当作 update 而错误复活为 active。
- dev 依赖双清单漂移风险（N9）：收敛到 `[dependency-groups]` 唯一真相源，CI 改 `uv sync --frozen`；pnpm 版本对齐 11（packageManager 字段 + CI + Dockerfile 三方一致）。

### Removed

- Lark（飞书）适配器（从未挂载路由）；`platform` 枚举保留 `lark` 值供历史数据。
- 前端死代码 `useWebSocket.ts`（由 `useDashboardSocket` 取代）。
- main.py 平行模块级全局实例与 notifications API 的重复实现（runtime 容器为唯一注册表）。

## [0.2.0] - 2026-08-22

### Added

- 第二阶段核心架构修复：角色 Tick 并发化（gather + 信号量）、Action executor 抽象落地、12 个内嵌 Prompt 全部外置到 `configs/prompts/*.yaml`（缺失即 fail-fast）、双配置体系统一（Settings 为唯一默认值真相源）。
- 分布式锁加固：唯一 token + Lua compare-and-delete/expire + 看门狗续租。
- 真实 token usage 贯通：预算扣减、消息持久化、指标上报统一使用 response_metadata 真实值与统一单价表。
- 消息服务加固：OneBot 消息 Redis SETNX 去重、群聊回复概率常量化单点判定、Web 分享推送 UUID/str 类型 bug 修复、扇出去重 + 后台投递。
- 启动回灌（P0-3）：PG 镜像 → Redis 缺失键。
- 工具 delta 纳入 PG 事务（P0-1）；Redis 状态统一编解码器 `state_codec.py`（P0-2）。

### Security

- WebSocket 握手 JWT 鉴权；PUBLIC_GET 白名单收敛；RBAC 默认角色改 viewer；限流器 Lua 原子化。

[Unreleased]: https://github.com/stargaoyc/ai_town/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/stargaoyc/ai_town/compare/v0.1.0...v0.2.0
