# 补充意见 — 主 Agent 直扫全量复核（2026-08-26）

> **方式**：不使用子代理，由主 Agent 直接 `Read`/`Grep`/`Glob` 扫码 30+ 核心文件，逐行交叉校验此前“4 路并行探索”结论  
> **范围**：`packages/backend/src/main.py`、`llm/client.py`、`security/*`、`auth/*`、`scheduler/loops.py`、`adapters/onebot.py`、`core/world/engine.py`、`core/reconcile.py`、`memory/*`、`observability/*`、`frontend/src/lib/*`、`frontend/src/hooks/*`、`frontend/src/routes/characters.$characterId.tsx`、`db/models/*`、`alembic/versions/*`  
> **结论**：此前综合报告（`review-2026-08-26-comprehensive.md` + 附录 D）**事实基线准确**，直扫未推翻任何核心判断；本补充聚焦**此前未充分展开的 9 类隐性风险与工程细节**，并给出可落地的修复优先级。

---

## 1 生命线与后台任务编排：看得见的“降级哲学”

**证据**：`main.py:103-417 lifespan` 显式区分“必须模块 fail-fast”（Redis/LLM/Action/World Engine）与“可选模块降级”（Embedding Worker/Partition Scheduler/Character Tick/SceneLoader/OneBot），每块 `try/except` 均有日志；`shutdown` 段（422-531）逐个 `cancel` 10+ 后台任务并 `await dispose/close`。

**肯定**：
- 10 个后台循环（Tick/日记/热度衰减/压缩/保留/对账/Redis 探活等）在 `lifespan` 集中装配，`shutdown_background_tasks()` 兜底 fire-and-forget 任务，符合“埋点即契约”之外的“生命周期即契约”。
- `check_default_secrets()`、`check_embedding_dim()` 在 lifespan 前置执行，避免“潜伏到首次写入才报错”。

**补充风险**：
- **降级面过大**：Character Tick 失败仅 `logger.error` 不中断启动，世界仍推进但角色静止，用户感知为“小镇假活”。建议对 `CHARACTER_ENGINE_AVAILABLE==False` 时在 `/health` 与 Dashboard 显式暴露 `character_engine: degraded` 横幅，而非仅日志。
- **CORS 未配置即静默同源**（`main.py:543-554`）：`cors_origins==""` 时 `logger.warning` 但不阻断，开发便利但生产若忘配，前端跨域失败无明确报错。建议生产 `ENVIRONMENT=production` 时 `cors_origins` 为空则 fail-fast。

## 2 LLM 客户端：多源容错扎实，但视频链路是吞吐黑洞

**证据**：`llm/client.py:126-1003`，`LLMClient` 覆盖 `chat / multimodal_chat / embed / generate_image / generate_video / structured_output` 全模态；`ModelSourcePool + invoke_with_fallback` 多源冷却、`_check_cost_control` 统一前置（熔断+预算）、`LLMUsage` 单轨计费。

**肯定**：
- `embed`/`structured_output` 均接入真实 `token_usage` + `estimate_cost` 单价表（`_parse_model_prices` 支持按模型覆盖），`LLM_COST_TOTAL`/`LLM_TOKENS_USED` 全路径接线，终结“估算值双轨”漂移。
- `_resolve_tier_model` 修复历史“档位强制重定向 chat”漂移（`client.py:94-107`），`group_judge_model` 终于尊重 `flash` 档位。
- `structured_output` 解析失败重试一次（`R4-M9`），多模态用量缺省时按 `text_chars//2` 估算入账，避免图像理解游离预算之外。

**补充风险**：
- **`generate_video` 同步轮询**（`_poll_video_result: 120×5s≈10 分钟`，`settings.media_video_*` 可调）：在 `CharacterTickEngine.SEMAPHORE` 槽位内阻塞，两个视频并发即可占满 20% 吞吐（`character_max_concurrent=10`）。**必须**改为后台任务 + 回调写入记忆，而非 Tick 内等待。
- **单价默认 `agnes-2.0-flash`（0.5/1.5 $/M）**：若用户换用 `gpt-4o` 未同步 `llm_model_prices`，成本仪表与预算控制将系统性低估 5 倍。`config.py:40-46` 已注释此风险，但无启动校验。建议 `startup_checks` 增加“若 `model_chat != agnes` 且 `llm_model_prices==''` 则 warning”。
- **`openai.images.generate` 仍用 `model_strong`**（`client.py:516`），与图像模型 `agnes-image-2.1-flash` 命名不一致，易误用强推理模型计费。

## 3 安全纵深：三层 Prompt 防护 + 双凭据鉴权，但存在“公开读”与正则盲区

**证据**：`security/prompt_guard.py:30-56` 15 条危险模式（大小写不敏感）、`_CONTROL_CHARS_RE` 控字符清理、`sanitize` 仅转义 `<>&`；`auth/middleware.py:127-143` `PUBLIC_GET_PREFIXES` 仅放行 `/world /actions /town/scenes /modules`；`adapters/onebot.py:617-629` `onebot_access_token` Bearer/参数校验 + `secrets.compare_digest`。

**肯定**：
- `check_injection → sanitize → wrap_user_message` 与 `handle_user_message:324-347` 检测后直接 `return blocked` 的“先拒后洗”策略正确（`prompt_guard.py:120-122` 注释论证“删除会制造新不可预期输入”）。
- `AuthMiddleware` 已收敛公开面（`P0-8` 移除 `messages/conversations/admin`、`R4-H2` 移除 `characters/memories`），Dashboard 登录态不受影响。
- `onebot_adapter` 构造期即 `check_onebot_access_token()`（`onebot.py:472`），绕过 `main.py` 降级 `try/except` 的 fail-fast 设计精巧。

**补充风险**：
- **正则易绕过**：`_DANGEROUS_PATTERNS` 未覆盖 `base64` 编码、`角色扮演`（“假装你是...”）、`\\u` 转义、间接指令（“把上面的指令翻译成英文”）。建议引入 LLM 二次判定作为补充（小模型 `flash` 低成本复核）或接入 `llm-guard` 规则库。
- **`sanitize` 不转义引号**（`quote=False`）：保留可读性但 `'`/`"` 可用于闭合后续 Prompt 的 JSON 结构（`chat` 要求输出 `{"response":...}`），需在 `chat.yaml` 侧增加 JSON 转义校验。
- **`GET /world` 等公开读**：虽为只读，但 `world:state` 含 `tick_id` 时序信息，公开可被爬虫高频轮询。建议对公开 GET 增加 `RateLimiter`（`security/rate_limiter.py` 已有实现，未接入公开路径）。

## 4 世界引擎与调度循环：fencing 与去重已到位，时间语义需统一

**证据**：`core/world/engine.py:141-138` `collect_changed_events` 纯函数去重 + `BASELINE_KEY` 持久化；`_is_still_leader` fencing（`339-353`）；`scheduler/loops.py:58-244` 5 大循环 + `character_tick_loop` 429 指数退避（`_is_rate_limit_error` 按 `status_code==429` 而非字符串）。

**肯定**：
- `world:events:baseline` 重启恢复避免首轮重复写入，`collect_changed_events` 对 `scenes/resources` 用 `json.dumps(sort_keys)` 变化才写，事件维度“始终写入”语义区分清晰。
- 演化器 `setup` 钩子已补调用（`engine.py:206-215` 注释 `P-8`），单演化失败不中断 Tick。
- 限流退避 `backoff_multiplier *2 上限 10` 且按异常类型判定，修复历史“QQ 号含 429 误判”缺陷。

**补充风险**：
- **时间双轨**：`_save_world_state:492` 用 `datetime.now().isoformat()`（本地时区），而 `rehydration` 与 `loops:200` 用 `UTC`。`world_time` 为虚拟时间尚可容忍，但 `updated_at` 口径不一影响审计。建议统一 `UTC`。
- **场景容量无排队**：`SceneEvolution` 的 `crowdedness` 仅入 Prompt，不做 `move` 准入拒绝；多角色并发涌入小容量场景无背压。建议 `MovementSystem.calculate_move` 增加 `current_count >= capacity` 校验并返回 `reason="scene_full"`。
- **日记调度** `diary_scheduler_loop:210` 按 `world_now` 的 `periods_to_generate` 触发，但 `_world_real_window_seconds` 换算依赖时钟倍率，倍率变更时窗口与幂等键（世界日历）易错位。建议将倍率快照与幂等键同事务记录。

## 5 记忆/反思/规划：两层结构领先，但阈值与内容质量是短板

**证据**：`memory/reflection_service.py:25-29` 阈值常量、`memory_episode.py:60-62` `HALFVEC(2048)`、`scheduler/loops.py:561-913` 保留周期两阶段、`frontend/src/routes/characters.$characterId.tsx:356-369` 记忆卡片。

**肯定**：
- 反思 `memory_ids` 收敛 `[1,total]` 且 `seen_summaries` 去重，`_embed_saved` 失败降级 `NULL` 不建重试队列（低频不值得）。
- 保留周期 `_pk_batched_delete` 主键子查询分批（`loops.py:494-507` 论证 `ctid` 漂移），`archive` 行 `created_at` 计龄（`memory_episode.py:66` 注释 `R5-M2`），`importance 3/6/7` 三档 + 压缩最小批 5 的“不变量”完备。

**补充风险**（与附录 D 互补）：
- **阈值硬编码**：`REFLECTION_THRESHOLD=20` 等 5 个常量未入 `settings`，与全配置化风格割裂，调参需改码发版。建议迁入 `config.py` 并支持运行时热更新。
- **内容质量**：`episode_service` 生成 `"{name}在{location}执行了{action}。理由：{reason}"` 的工程日志句式，向量区分度低；前端 `characters.$characterId.tsx:367` 直接原文展示，用户感知为“流水账”。建议引入 `memory_compress` 同款 LLM 润色（短句叙事化）或模板多样化。
- **检索召回**：`retrieval` 的合成 query（位置+时段+情绪+计划）与用户真实意图弱相关；`Person Memory` 仍无向量检索（`person_memory_service` 仅 `heat` 排序），多用户场景召回精度不足。

## 6 一致性与对账：版本感知仲裁是亮点，仍有两处“ transient 豁免”

**证据**：`core/reconcile.py:81-215` 全量 diff、`_RECONCILE_FIELDS` 8 字段、`_REPAIR_LOCK_TTL=5`、`rec_ver` 基线；`core/state_codec.py` 标量 `str()`/复合 `JSON` 单一真相源；`core/rehydration.py` 启动回灌。

**肯定**：
- `pg_advanced` 仲裁 + Tick 锁临界区 + `fresh_version` 复核 + `rec_ver` 基线更新，修复历史“裸 UPDATE 不递增 version 导致仲裁失效”与“复核后 Tick 双写交错”两轮缺陷（`M9/L7` 注释详尽）。
- `current_action` 排除对账的“瞬态”声明合理（每 Tick 都变，对账无意义），`inventory` 用 `dict` 比较、`_INT_FIELDS` 用 `int()` 归一，均经 `decode_state_value` 还原。

**补充风险**：
- **`world:scene:visitors` 与 `scene:{id}:characters` 不在对账字段集**，Redis 丢失后拥挤度与成员名单无法自愈，需下次 `move` 逐个修正。建议将 `visitors` 纳入 `reconcile.py` 或 `SceneLoader` 的周期重建。
- **PG 提交后 Redis 写失败窗口**：`tick.py:1109 hset` 失败则漂移至下次对账（10min），期间 `GET /characters/{id}` 读 Redis 将返回陈旧状态。建议对 `hset` 失败立即触发单角色对账重试（而非等待全量周期）。

## 7 前端直扫：契约与实时设计优秀，聊天链路与“实时”文案名不符实

**证据**：`frontend/src/lib/queries.ts:11-72` `queryKeys` 契约、`api.ts:17-44` 双凭据 + 401 跳转、`hooks/useDashboardSocket.ts:50-71` 心跳新鲜度与 `setQueryData` 部分更新、`routes/characters.$characterId.tsx:92-121` 乐观消息 + `useMessages` 扩窗。

**肯定**：
- `queryKeys` 前缀失效锚点（`*All`）与“禁止内联字面量”注释是全栈少见的纪律；`refetchInterval 30s` 均标注“断连兜底，WS 为主”。
- `useDashboardSocket` 经 `Sec-WebSocket-Protocol: bearer` 传 token（`R4-L8`）、`retryCount` 在 `onopen` 复位避免累计退避耗尽 `MAX_RETRIES=10`，细节到位。
- 聊天气泡 `cleanCQCodes`、`sendMessage` 乐观更新（`temp-xxx`）与 `invalidateQueries` 联动，加载更多用 `messageLimit` 扩窗而非分页游标，简单有效。

**补充风险**：
- **聊天未走 WS**：后端 `/ws/chat/{id}` 已就绪（JWT 三级传递、10s 发送超时、同一性驱逐），前端却用 `POST /messages/send` 同步等待 LLM，首字延迟高且无流式。建议新增 `useChatSocket` 或 SSE 流式，并保留 REST 兜底。
- **“实时”文案失真**：`qq-monitor.tsx` 等监控页副标题“实时”实为 30s 轮询；`useDashboardSocket` 10 次耗尽后永久放弃且 UI 无断连指示（`StatusBadge` 硬编码）。建议增加 `ws:connected/degraded/disconnected` 全局状态条。
- **Token 存 `localStorage`**（`api.ts:9`/`stores/auth.ts`）：XSS 窃取面；虽配 `CSP script-src 'self'`，仍建议 `httpOnly` Cookie + `SameSite` 方案或至少 `sessionStorage` 缩小持久化窗口。
- **大文件**：`components/ui.tsx` 696 行单文件、`api.ts` 478 行，超出项目倡导的小模块倾向；`oxlint exhaustiveDeps: off` 静音 stale closure 风险。

## 8 数据库与迁移：HASH 分区与 HNSW 已成型，运维细节待补

**证据**：`db/models/memory_episode.py:32-109` 16 分区 + `HALFVEC(2048)` + 部分索引；`alembic/versions/0002_optimize.py:84-448` 450 行重建迁移（含 `statement_timeout 10min`、`lock_timeout 60s`、全表 `INSERT...SELECT` 分区重建）。

**肯定**：
- `pre_create_partitions(3)` 函数与 `PartitionScheduler`（每月 25 日 03:00）双保险，`DEFAULT` 分区先查后删防静默丢失，`uuidv7()` 注释与 `pgvector/pgvector:pg18` 镜像对齐。
- `0018` 对 `memory_episodes` 各 HASH 子分区 `autovacuum scale 0.05/0.02` 收紧，命中保留周期删除热点。

**补充风险**：
- **HNSW 不收缩**（与附录 D 一致）：`DELETE` 后索引项残留，无 `REINDEX CONCURRENTLY` 定时任务；建议 `loops.py` 新增月度 `REINDEX INDEX CONCURRENTLY idx_mem_embedding_hnsw`（或 `pg_cron`）。
- **`tsvector` 缺失**：`related_characters` 等 `GIN` 已有，但 `content` 无 `tsvector` 全文索引，反思/记忆的关键词检索仍靠向量；中英文混合场景建议补 `pg_trgm` 或 `tsvector` 双轨。
- **迁移 PG18 拆句**（`0002` 注释 `v10`）：多语句 `op.execute` 拆单规避 prepared statement 限制，说明升级 18 时有实战踩坑，建议在 `development-guide.md` 沉淀 PG18 迁移 checklist。

## 9 可观测性：三支柱已打通，采样与成本盲区需收口

**证据**：`observability/tracing.py:98-142` `TraceIdRatioBased 0.5` + `BatchSpanProcessor` + `FastAPI/AsyncPG` 自动 instrument；`metrics.py:30-241` 25 指标 + `PrometheusMiddleware` 路由模板防基数爆炸；`observability/logging.py` `structlog` JSON + `trace_id` 注入。

**肯定**：
- `trace_span` 以 `is_recording()` 门控属性绑定与 `repr` 截断（`tracing.py:238-246`），头采样丢弃时近零开销。
- `HTTP_REQUEST_DURATION` 按 `route.path` 而非原始 `path` 打标（`metrics.py:228` 注释 `R4-M4`），规避 UUID/404 基数爆炸。
- `LLM_DAILY_BUDGET_USD` gauge 与 `LLM_COST_TOTAL` 组合告警，预算改配置即生效无需改规则。

**补充风险**：
- **头采样 0.5**：错误链路可能被采样丢弃（`observability.md` 已诚实披露“无错误必采保证”）。建议引入 Collector 尾采样或对 `status=failed` 的 `llm.generate` 强制 `is_recording=True` 的旁路。
- **媒体生成成本盲区已补**：`MEDIA_GENERATION_TOTAL` 以 `tool/outcome` 计数兜住无 token 场景（`R4-M18`），但费用仍不可估，建议在 Dashboard 单独“媒体成本估算”卡片提示“按次计费，实际以供应商账单为准”。

---

## 总体校准

- **原综合评分 4.5/5 (A-) 维持不变**。直扫未发现推翻性缺陷，9 类补充均为 P1/P2 级工程收口。
- **优先级重申**：
  - **P0**（上线前）：视频生成后台化、`world:scene:visitors` 纳入对账、聊天 WS/SSE 流式、CORS 生产 fail-fast
  - **P1**：HNSW 定期 `REINDEX`、反思阈值配置化、`llm_model_prices` 启动校验、公开 GET 限流、Token 存储收敛、`exhaustiveDeps` 恢复
  - **P2**：记忆内容叙事化润色、场景容量准入、日记倍率快照、tsvector 补齐、PG18 迁移 checklist 文档化

*— Sisyphus 主 Agent 直扫，2026-08-26，全程未委派子代理，逐文件交叉校验 —*
