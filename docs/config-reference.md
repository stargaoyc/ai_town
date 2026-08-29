# 配置参考

> 本文档列出 AI Town 的全部配置项。以 `packages/backend/src/config.py` 的 `Settings` 类为唯一真相源；凡代码注释与本文冲突，以代码为准。
>
> 表中 **⚡** 标记的项为**运行时热更新配置**（详见 [二、运行时热更新配置](#二运行时热更新配置runtimconfig)），可在运行时通过 `PUT /api/v1/admin/config` 修改，无需重启。其余项变更必须重启后端。

---

## 一、环境变量（Settings）

以下为 `Settings` 类定义的全部 138 个配置项，按代码中的分组列出。

### 1.1 数据库

| 变量              | 必填 | 默认 | 说明                                                   |
| ----------------- | ---- | ---- | ------------------------------------------------------ |
| `DATABASE_URL`    | 是   | —    | PG 连接串，`postgresql+asyncpg://user:pass@host:5432/db` |
| `DB_POOL_SIZE`    | 否   | 20   | 连接池大小                                             |
| `DB_MAX_OVERFLOW` | 否   | 10   | 连接池溢出上限                                         |
| `DB_ECHO`         | 否   | `false` | 是否打印 SQL（调试用）                               |

### 1.2 Redis

| 变量        | 必填 | 默认 | 说明                     |
| ----------- | ---- | ---- | ------------------------ |
| `REDIS_URL` | 是   | —    | `redis://host:6379/0`    |

### 1.3 LLM 配置

| 变量                          | 必填 | 默认                                   | 说明                                                                   |
| ----------------------------- | ---- | -------------------------------------- | ---------------------------------------------------------------------- |
| `OPENAI_API_KEY`              | 是   | —                                      | OpenAI 兼容 API Key                                                     |
| `OPENAI_BASE_URL`             | 否   | `https://api.openai.com/v1`            | API 基址（兼容代理）                                                    |
| `MODEL_CHAT`                  | 否   | `gpt-4o-mini`                          | 日常对话模型                                                           |
| `MODEL_IMAGE`                 | 否   | `agnes-image-2.1-flash`                | 图像生成模型（仅 `llm.generate_image` 使用，勿复用文本推理模型计费）      |
| `MODEL_VIDEO`                 | 否   | `agnes-video-v2.0`                     | 视频生成模型（原 `MODEL_FLASH` 更名：该档位只被 `generate_video` 消费） |
| `MODEL_EMBEDDING`             | 否   | `text-embedding-3-small`               | 向量化模型                                                             |
| `EMBEDDING_MODEL_KEY`         | 否   | `None`                                 | Embedding 专用 API Key（如 OpenRouter / 本地服务）                      |
| `EMBEDDING_MODEL_URL`         | 否   | `None`                                 | Embedding 专用 API URL                                                  |
| `LLM_TIMEOUT`                 | 否   | 30                                     | 单次请求超时（秒）                                                     |
| `LLM_MAX_RETRIES`             | 否   | 2                                      | 失败重试次数                                                           |
| `MEDIA_VIDEO_POLL_INTERVAL`   | 否   | 5                                      | 视频生成同步轮询间隔（秒）                                              |
| `MEDIA_VIDEO_MAX_POLLS`       | 否   | 120                                    | 视频生成同步轮询上限；上限 = `max_polls × interval` 秒，默认约 10 分钟   |
| `LLM_FALLBACK_SOURCES`        | 否   | `[]`                                   | 多源备用源 JSON 数组，按顺序尝试；失败冷却 5 分钟后可作末位兜底。每项 `{"api_key", "base_url", "model"（可选）}` |
| `LLM_PRICE_INPUT_PER_MTOKEN`  | 否   | 0.5                                    | LLM 输入单价（USD / 1M tokens），未配置模型单价时回退到此值             |
| `LLM_PRICE_OUTPUT_PER_MTOKEN` | 否   | 1.5                                    | LLM 输出单价（USD / 1M tokens），同上                                   |
| `LLM_MODEL_PRICES`            | 否   | `""`                                   | 按模型单价覆盖（JSON，如 `{"gpt-4o-mini": {"input": 0.15, "output": 0.6}}`） |
| `EMBEDDING_DIM`               | 否   | 2048                                   | 向量维度，与 PG `halfvec(2048)` 物理列对齐；改值须新迁移重建列与 HNSW 索引 |
| `EMBEDDING_PROBE_ENABLED`     | 否   | `true`                                 | 启动时对 `MODEL_EMBEDDING` 做真实调用，校验输出维度与 `EMBEDDING_DIM` 一致；维度错配 fail-fast，调用失败告警放行。离线开发可置 `false` 跳过 |

### 1.4 保留与治理（Retention）

| 变量                                  | 必填 | 默认 | 说明                                                                 |
| ------------------------------------- | ---- | ---- | -------------------------------------------------------------------- |
| `WORLD_EVENTS_RETENTION_DAYS`         | 否   | 90   | `world_events` 超期删除（天）                                        |
| `WORLD_SNAPSHOTS_KEEP_LATEST`         | 否   | 3    | `world_snapshots` 仅保留最近 N 份（冷启动恢复真相源）                |
| `ACTION_RECORDS_RETENTION_MONTHS`     | 否   | 12   | RANGE 分区表保留月数，超期整月分区自动 DETACH+DROP                   |
| `STATE_HISTORY_RETENTION_MONTHS`      | 否   | 6    | RANGE 分区表保留月数，同上                                           |
| `MESSAGES_RETENTION_DAYS`             | 否   | 180  | 消息表保留天数；`0` = 永久保留                                        |
| `REFLECTION_RETENTION_DAYS`           | 否   | 365  | 反思保留天数；仅清理 tier=1 批次产物，tier=2 元反思永久保留           |
| `DIARY_RETENTION_DAYS`                | 否   | 730  | 角色日记保留天数；`0` = 永久保留                                      |
| `PERSON_MEMORY_ENTRY_RETENTION_DAYS`  | 否   | 180  | Person Memory 已压缩（compacted）条目保留天数；仅清理已压缩条目        |
| `ARCHIVE_EPISODE_RETENTION_DAYS`      | 否   | 365  | `source_type=archive` 归档行保留天数；`0` = 永久保留                   |
| `PLANS_RETENTION_DAYS`                | 否   | 90   | plans 终态（completed/abandoned/expired）保留天数；`0` = 永久保留       |
| `RETENTION_DELETE_BATCH_SIZE`         | 否   | 5000 | 保留周期单批删除行数；大积压时分批删空，避免长事务                   |

### 1.5 可观测性

| 变量                    | 必填 | 默认                 | 说明                                              |
| ----------------------- | ---- | -------------------- | ------------------------------------------------- |
| `OTEL_ENDPOINT`         | 否   | `None`               | OTel Collector 地址（空串视为禁用，`setup_tracing` 以 `is None` 判定） |
| `OTEL_SERVICE_NAME`     | 否   | `ai-town-backend`    | 服务名                                            |
| `LANGFUSE_HOST`         | 否   | `None`               | Langfuse 地址                                     |
| `LANGFUSE_PUBLIC_KEY`   | 否   | `None`               | Langfuse 公钥                                     |
| `LANGFUSE_SECRET_KEY`   | 否   | `None`               | Langfuse 密钥                                     |
| `LOKI_URL`              | 否   | `http://loki:3100`   | Loki 推送地址                                      |
| `LOG_LEVEL` ⚡          | 否   | `info`               | 日志级别（`debug`/`info`/`warning`/`error`）       |
| `LOG_FORMAT`            | 否   | `json`               | 日志格式（`json`/`text`）                          |
| `LOG_FILE_MAX_BYTES`    | 否   | 50000000             | 日志文件轮转单文件上限（RotatingFileHandler）      |
| `LOG_BACKUP_COUNT`      | 否   | 5                    | 日志文件轮转保留份数                              |

### 1.6 鉴权与安全

| 变量                  | 必填 | 默认        | 说明                                                                     |
| --------------------- | ---- | ----------- | ------------------------------------------------------------------------ |
| `JWT_SECRET`          | 是   | —           | JWT 签名密钥                                                             |
| `JWT_ALGORITHM`       | 否   | `HS256`     | JWT 算法                                                                 |
| `JWT_EXPIRE_HOURS`    | 否   | 24          | JWT 过期时间（小时）                                                     |
| `API_KEY`             | 否   | `None`      | 静态 API Key（第三方集成用）                                             |
| `API_KEY_ROLE`        | 否   | `admin`     | 静态 API Key 绑定的 RBAC 角色（默认 admin 供运维；只读监控下调为 `operator`/`viewer`）。取值见 `src/auth/rbac.py` 的 `ROLE_*` 常量 |
| `ADMIN_USERNAME`      | 否   | `admin`     | 管理员用户名                                                             |
| `ADMIN_PASSWORD`      | 否   | `admin123`  | 管理员密码；production 模式下默认弱口令将禁止启动                        |
| `ALERT_WEBHOOK_TOKEN` | 否   | `""`        | Alertmanager webhook 回流鉴权 token；未配置时告警端点返回 403            |
| `RBAC_ROLES`          | 否   | `""`        | 逗号分隔的用户名:角色列表，如 `admin:admin,viewer1:viewer`               |
| `CORS_ORIGINS`        | 否   | `""`        | 逗号分隔的 CORS 允许来源；`allow_credentials=true` 与通配符 `*` 互斥，生产必须配置实际域名 |
| `ENVIRONMENT`         | 否   | `development` | 运行环境标识；`production` 时启用安全 fail-fast（默认弱口令禁止启动） |

### 1.7 成本控制

| 变量                                  | 必填 | 默认   | 说明                                                                   |
| ------------------------------------- | ---- | ------ | ---------------------------------------------------------------------- |
| `LLM_DAILY_BUDGET_USD` ⚡             | 否   | 10.0   | 全局 LLM 日预算上限（USD）                                              |
| `LLM_CIRCUIT_BREAKER_THRESHOLD`       | 否   | 5      | 熔断器触发阈值（连续失败次数）                                          |
| `LLM_CIRCUIT_BREAKER_RECOVERY_TIMEOUT`| 否   | 60     | 熔断器恢复超时（秒）                                                    |
| `LLM_DAILY_BUDGET_PER_CHARACTER_USD`  | 否   | 1.0    | 单角色日预算上限；`<=0` 表示不限制。收窄 QQ 公开入口被单角色高频对话打爆的影响面 |
| `LLM_DAILY_BUDGET_PER_USER_USD`       | 否   | 0.5    | 单用户日预算上限；`<=0` 不限制                                          |
| `LLM_RESERVE_ESTIMATE_USD`            | 否   | 0.02   | 调用前预留额度（在途计入预算占用）；保守取值略高于单次调用真实上四分位        |

### 1.8 记忆系统

| 变量                                   | 必填 | 默认    | 说明                                                                   |
| -------------------------------------- | ---- | ------- | ---------------------------------------------------------------------- |
| `MEMORY_LLM_SCORING_ENABLED` ⚡         | 否   | `false` | 是否启用 LLM 记忆重要程度评分（1-10 分）；关闭时使用规则评分              |
| `MEMORY_RETENTION_ENABLED`             | 否   | `true`  | 记忆保留开关                                                           |
| `MEMORY_RETENTION_LOW_IMPORTANCE_DAYS` | 否   | 90      | `importance<=3` 的记忆保留天数                                         |
| `MEMORY_RETENTION_MID_IMPORTANCE_DAYS` | 否   | 180     | `importance` 4-6 的记忆保留天数                                        |
| `MEMORY_RETENTION_PERMANENT_IMPORTANCE`| 否   | 7       | 永久保留阈值（`>=此值` 永不过期）；外置以便按实测分布调参                  |
| `MEMORY_RETENTION_INTERVAL_SECONDS`    | 否   | 3600    | 记忆治理周期（秒）；配合批次上限共同决定清理吞吐                         |
| `MEMORY_COMPRESSION_ENABLED`           | 否   | `true`  | retention 删除前按角色×月份 LLM 压缩成归档行；绝不未压缩先删除             |
| `MEMORY_COMPRESSION_MIN_BATCH`         | 否   | 5       | 单组少于该条数不压缩（摘要收益低于成本）                                 |
| `MEMORY_COMPRESSION_BATCH_LIMIT`       | 否   | 5000    | 单周期最多处理的候选条数；压缩调用 LLM，提高此值会线性增加成本           |
| `MEMORY_DEDUP_ENABLED`                 | 否   | `true`  | 改写式记忆去重（向量化时与同角色近窗口记忆余弦比对）                      |
| `MEMORY_DEDUP_SIMILARITY_THRESHOLD`    | 否   | 0.95    | 判定重复的相似度阈值（`pg_trgm` 对中文无效，向量比对是可靠信号）           |
| `MEMORY_DEDUP_WINDOW_HOURS`            | 否   | 24      | 去重回窗（小时）                                                       |
| `ACTION_BASE_IMPORTANCE`               | 否   | 见下    | 按 Action 类型的基础重要度；键必须与 `actions/registry.py` 注册的 action id 完全一致 |
| `ACTION_EMOTION_IMPORTANCE_BOOST`      | 否   | 2       | 理由含情绪关键词时在该值基础上提升的分数；`0` 关闭                       |
| `MEMORY_EMOTION_BOOST_MAX_TOTAL`       | 否   | 6       | 加成后重要度上限；默认低于永久保留阈值，防止情绪加成把普通记忆钉入永不过期集合 |
| `MEMORY_WRITE_GATE_ENABLED`            | 否   | `true`  | 记忆写入显著性门禁；源头减量优先于提升清理吞吐                         |
| `MEMORY_WRITE_MIN_IMPORTANCE`          | 否   | 4       | 低于该重要度需命中「同场景社交 / 位置迁移」例外才写入                    |

`ACTION_BASE_IMPORTANCE` 默认值（键 = action id，必须与注册表一致）：

| action id               | 基础重要度 |
| ----------------------- | ---------- |
| `wait` / `charge_phone` | 2          |
| `sleep` / `relax` / `use_phone` | 3   |
| `eat` / `eat_at_home` / `read_book` / `move` | 4 |
| `study`                 | 5          |
| `chat_with` / `group_activity` / `work_parttime_cafe` / `work_parttime_store` | 6 |

### 1.9 计划与反思

| 变量                            | 必填 | 默认  | 说明                                                       |
| ------------------------------- | ---- | ----- | ---------------------------------------------------------- |
| `DAILY_PLAN_TTL_HOURS`          | 否   | 24    | 当日计划滚动过期时长（创建超过 TTL 的 active daily 置 expired） |
| `REFLECTION_THRESHOLD`          | 否   | 20    | 未反思记忆达到该数量触发批次反思                            |
| `REFLECTION_POOL_SIZE`          | 否   | 30    | 单次参与归纳的记忆池上限                                    |
| `REFLECTION_MAJOR_IMPORTANCE`   | 否   | 9     | 单条记忆达到该重要性即时触发反思                            |
| `REFLECTION_MAJOR_COOLDOWN_SECONDS` | 否 | 300  | 重大事件反思的每角色冷却（防 LLM 风暴）                     |
| `SOCIAL_ENCOUNTER_ENABLED`      | 否   | `true`| 空闲时概率触发与同场景角色闲聊                              |
| `SOCIAL_ENCOUNTER_PROBABILITY`  | 否   | 0.25  | 每次空闲 Tick 触发闲聊的概率                                |
| `SOCIAL_ENCOUNTER_COOLDOWN_SECONDS` | 否 | 600  | 每角色相遇冷却（避免连续闲聊刷屏）                          |
| `META_REFLECTION_MIN_TOTAL`     | 否   | 6     | 累计反思达到该数量后才考虑元反思                            |
| `META_REFLECTION_COOLDOWN_DAYS` | 否   | 7     | 两次元反思的最小间隔                                        |
| `META_SOURCE_LIMIT`             | 否   | 10    | 元反思读取的最近 tier-1 反思条数                            |
| `PLAN_AUTO_PROGRESS_ENABLED`    | 否   | `true`| Action 完成后按标题与决策理由字符二元组重叠启发式推进计划进度 |
| `PLAN_AUTO_PROGRESS_DELTA`      | 否   | 10    | 单次推进百分比；`0` 关闭                                    |
| `PLAN_AUTO_PROGRESS_OVERLAP`    | 否   | 0.34  | 触发推进的重叠阈值                                          |

### 1.10 检索与记忆维护

| 变量                                | 必填 | 默认  | 说明                                                                   |
| ----------------------------------- | ---- | ----- | ---------------------------------------------------------------------- |
| `HNSW_REINDEX_ENABLED`              | 否   | `true`| 保留期大量 DELETE 后 HNSW 索引项不被 VACUUM 回收，定期在线重建           |
| `HNSW_REINDEX_INTERVAL_DAYS`        | 否   | 30    | 重建间隔（天）；`0` 关闭                                                |
| `PERSON_MEMORY_HEAT_CAP`            | 否   | 500   | 高频用户 heat 无界增长会与低频用户拉开数千倍差距，交互累加时钳制到该值  |
| `GOSSIP_MAX_LISTENERS_PER_SOURCE`   | 否   | 3     | 同一源记忆在窗口内最多扩散给 N 个听者（抑制多好友路径放大同一事件）      |
| `PUBLIC_GET_RATE_LIMIT_PER_MINUTE`  | 否   | 120   | 公开只读 GET 每 IP 每分钟限流；`0` 关闭                                 |
| `HNSW_EF_SEARCH`                    | 否   | 100   | HNSW 检索 ef_search 参数（SET LOCAL 事务内生效；调大提升召回率、增加延迟）|
| `RETRIEVAL_CANDIDATE_MULTIPLIER`    | 否   | 4     | 混合检索候选池放大倍数；`候选 LIMIT = top_k × 此值`，平衡召回广度与延迟  |
| `PERSON_MEMORY_COMPACT_THRESHOLD`   | 否   | 20    | Person Memory 未压缩事实条目达到该阈值后合并进主档                      |

### 1.11 世界引擎

| 变量                              | 必填 | 默认                                          | 说明                                                      |
| --------------------------------- | ---- | --------------------------------------------- | --------------------------------------------------------- |
| `WORLD_TICK_SECONDS` ⚡           | 否   | 30                                            | World Tick 真实间隔（秒）                                  |
| `WORLD_TICK_MINUTES`              | 否   | 10.0                                          | 每次 Tick 推进的虚拟分钟（支持小数）                        |
| `WORLD_INITIAL_TIME`              | 否   | `""`                                          | 虚拟世界初始时间（ISO 格式，如 `2026-07-01T08:00:00`）；留空用当前现实日期 08:00 |
| `WORLD_WEATHER_INTERVAL`          | 否   | 60                                            | 每 N Tick 更新天气                                        |
| `WORLD_SNAPSHOT_INTERVAL`         | 否   | 10                                            | 每 N Tick 持久化差分事件到 `world_events`（降低以让前端时间线更快有数据） |
| `WORLD_FULL_SNAPSHOT_INTERVAL`    | 否   | 1000                                          | 每 N Tick 存一次完整快照到 `world_snapshots`（冷启动恢复） |
| `CHARACTER_TICK_SECONDS` ⚡       | 否   | 30                                            | Character Tick 真实间隔（秒）                              |
| `CHARACTER_MAX_CONCURRENT` ⚡     | 否   | 10                                            | 并发角色 Tick 上限                                        |
| `CHARACTER_LOCK_TTL_SECONDS`      | 否   | 30                                            | 角色分布式锁 TTL（秒）                                    |
| `SOLO_RECOVERY_ACTIONS`           | 否   | `{"relax","sleep","read_book"}`                | 独处动作列表，执行时恢复社交能量（上限 100）                 |
| `SOLO_RECOVERY_SOCIAL_ENERGY_BOOST` | 否 | 10                                           | 单次独处动作恢复的社交能量值                                |

### 1.12 工具（Tools / ReAct）

| 变量                   | 必填 | 默认 | 说明                                           |
| ---------------------- | ---- | ---- | ---------------------------------------------- |
| `TOOL_TIMEOUT_SECONDS` | 否   | 60.0 | 单次工具执行超时（秒）；`0` = 禁用超时          |

### 1.13 主动分享与群体动力学

| 变量                               | 必填 | 默认  | 说明                                                       |
| ---------------------------------- | ---- | ----- | ---------------------------------------------------------- |
| `SHARE_COOLDOWN_SECONDS` ⚡        | 否   | 1800  | 分享冷却时间（秒），同一角色两次分享的最小间隔              |
| `SHARE_DAILY_LIMIT` ⚡             | 否   | 8     | 单角色每日最大主动分享次数（防刷屏）                        |
| `SHARE_PROBABILITY_ACTION` ⚡      | 否   | 0.6   | 特定 Action 完成时的分享概率（0.0-1.0）                     |
| `SHARE_PROBABILITY_MOOD` ⚡        | 否   | 0.5   | 强烈情绪时的分享概率（0.0-1.0）                            |
| `SHARE_PROBABILITY_LOCATION` ⚡    | 否   | 0.2   | 位置变化时的分享概率（0.0-1.0）                            |
| `SHARE_PROBABILITY_ROUTINE` ⚡     | 否   | 0.15  | 日常行为的分享概率（0.0-1.0）                              |
| `GOSSIP_ENABLED`                   | 否   | `true`| 传闻传播开关（好友的高重要性经历以第二手记忆扩散）          |
| `GOSSIP_IMPORTANCE_THRESHOLD`      | 否   | 7     | 源记忆重要性门槛（仅显著经历值得传播）                      |
| `GOSSIP_WINDOW_HOURS`              | 否   | 24    | 源记忆与去重回窗（小时）；每好友每窗口最多传播 1 条         |
| `GOSSIP_MAX_PER_TICK`              | 否   | 1     | 单次 Tick 最多传播条数（控制记忆膨胀速率）                  |
| `GOSSIP_RELATION_MIN`              | 否   | 20    | 好友关系强度门槛（传闻沿既有社交关系流动）                  |
| `GROUP_ACTIVITY_PARTICIPANT_MAX`   | 否   | 4     | 同场景临时群聚总参与人数上限（含发起者）                    |

### 1.14 社交会话（交互终止防无休止）

| 变量                   | 必填 | 默认    | 说明                                                                   |
| ---------------------- | ---- | ------- | ---------------------------------------------------------------------- |
| `CHAT_WITH_MAX_ROUNDS` | 否   | 2       | 单次 Action 内最多轮数（每轮双方各生成一句）                            |
| `CHAT_QUALITY_ENABLED` | 否   | `true`  | 对话结束后由 LLM 评估关系增量（替代固定 +5/+2）；关闭则回退固定值       |
| `CHAT_MAX_TURNS`       | 否   | 6       | 会话累计轮数硬上限（跨 Tick 延续后剩余配额递减）                        |
| `CHAT_IDLE_TICKS`      | 否   | 2       | 会话超时：超过 N 个世界 Tick 无人回应即自动结束                        |
| `CHAT_INJECT_COGNITION`| 否   | `false` | 用户对话链路注入反思/日记（默认关闭保持上下文精简）                     |

### 1.15 OneBot 适配器（QQ 接入）

| 变量                                | 必填 | 默认     | 说明                                                                      |
| ----------------------------------- | ---- | -------- | ------------------------------------------------------------------------- |
| `ONEBOT_DEFAULT_CHARACTER_ID`       | 否   | `None`   | 默认对话角色 UUID（私聊和未映射的群聊使用）                               |
| `ONEBOT_SELF_ID`                    | 否   | `None`   | 机器人自身 QQ 号（用于群聊 @ 检测，也可从 OneBot 事件 `self_id` 读取）      |
| `ONEBOT_GROUP_AT_ONLY`              | 否   | `false`  | 群聊是否仅在被 @ 时回复（默认读取所有群消息并智能决策是否回复）            |
| `ONEBOT_GROUP_CHARACTER_MAP`        | 否   | `{}`     | 群-角色映射 JSON（`{"群号":"角色UUID"}`），未配置的群使用默认角色           |
| `ONEBOT_ACCESS_TOKEN`               | 否   | `None`   | OneBot 反向 WS 接入令牌；配置后强制校验。`production` 未配置将拒绝启动      |
| `ONEBOT_RATE_LIMIT_PER_MINUTE`      | 否   | 20       | 单群/单私聊每分钟入站消息上限（超限静默丢弃）；`0` = 禁用                 |
| `ONEBOT_STREAM_MAXLEN`              | 否   | 10000    | OneBot 事件流长度上限（Redis Streams `maxlen` 裁剪）                      |
| `ONEBOT_SEND_MIN_INTERVAL_MS`       | 否   | 1000     | 同一会话两条出站消息的最小间隔（毫秒）；`0` = 禁用。降低 QQ 风控触发概率   |
| `ONEBOT_HEARTBEAT_STALE_SECONDS`    | 否   | 90.0     | 心跳过期阈值（秒）；超时未收到心跳的连接被主动 close，由 OneBot 实现重连   |

---

## 二、运行时热更新配置（RuntimeConfig）

运行时配置定义在 `packages/backend/src/config_runtime.py` 的 `RuntimeConfig`，通过 Redis key `config:overrides` 存储。

**关键特性（与 Settings 的区别）：**

1. **默认值统一为 `None`（未覆盖）**：`Settings` 是唯一默认值真相源，`RuntimeConfig` 只记录「管理员显式设置的覆盖值」，避免两套默认值漂移。
2. **Pydantic 类型/范围校验**：从 Redis 加载时经 `model_validate` 校验，无效值被拒绝，不再绕过 Pydantic。
3. **应用器（P-7）**：覆盖值同步到 `settings` 后，日志、预算管理器等已构造组件需显式通知才生效（如 `log_level` 重建 handlers、`llm_daily_budget_usd` 更新预算管理器）。
4. **损坏配置不静默**：Redis 配置解析/校验失败时报 error 级日志（供告警规则抓取），并以 Settings 默认值运行，不采用绕过校验的旧值。

### 2.1 热更新项清单

| 配置项                          | 类型    | 默认（未覆盖） | 范围约束                     | 说明                   |
| ------------------------------- | ------- | -------------- | ---------------------------- | ---------------------- |
| `share_cooldown_seconds`        | int     | 未覆盖         | `>= 0`                       | 分享冷却时间（秒）     |
| `share_daily_limit`             | int     | 未覆盖         | `>= 0`                       | 每日分享上限           |
| `share_probability_action`      | float   | 未覆盖         | `0-1`                        | Action 分享概率        |
| `share_probability_mood`        | float   | 未覆盖         | `0-1`                        | 情绪分享概率           |
| `share_probability_location`    | float   | 未覆盖         | `0-1`                        | 位置变化分享概率       |
| `share_probability_routine`     | float   | 未覆盖         | `0-1`                        | 日常行为分享概率       |
| `memory_llm_scoring_enabled`    | bool    | 未覆盖         | —                            | LLM 记忆评分           |
| `world_tick_seconds`            | int     | 未覆盖         | `>= 5`                       | 世界 Tick 间隔（秒）   |
| `character_tick_seconds`        | int     | 未覆盖         | `>= 5`                       | 角色 Tick 间隔（秒）   |
| `character_max_concurrent`      | int     | 未覆盖         | `>= 1`                       | 角色并发上限           |
| `llm_daily_budget_usd`          | float   | 未覆盖         | `>= 0`                       | LLM 日预算（USD）      |
| `log_level`                     | str     | 未覆盖         | `debug`/`info`/`warning`/`error` | 日志级别           |

> **取值说明**：未覆盖时这些项保持 `Settings` 中的默认值（见 §一各分组表）。例如 `share_cooldown_seconds` 未覆盖时取 `1800`，`world_tick_seconds` 未覆盖时取 `30`。

### 2.2 管理 API

| 操作   | 端点                          | 说明                                        |
| ------ | ----------------------------- | ------------------------------------------- |
| 热更   | `PUT /api/v1/admin/config`    | 更新传入字段（Pydantic 校验后写入 Redis）   |
| 重置   | `DELETE /api/v1/admin/config/{key}` | 重置单字段为 Settings 默认值              |

> 越权注意：仅 `admin` 角色可调用该 API。

---

## 三、模块与本地工具

工具随后端启动自动加载，无独立进程或网络配置项。工具按命名空间（shop / knowledge / social / world / self_info / media）组织，共 18 个工具。启用/禁用通过 Redis hash `tools:enabled` 持久化，键为工具全名（如 `shop.buy_item`），值为 `"true"` / `"false"`，未配置时默认全部启用。

| 项           | 说明                                                                                        |
| ------------ | ------------------------------------------------------------------------------------------- |
| 命名空间开关 | Redis hash `tools:enabled`                                                                  |
| 开关控制方式 | 前端 Dashboard toggle / `PUT /api/v1/tools/servers/{namespace}/enabled`                    |
| 健康检查     | 本地工具为进程内调用，`/api/v1/tools/servers/health` 始终返回 `online`                      |

详见 [模块与本地工具系统设计](module-system.md#二本地工具调用层toolregistry)。

---

## 四、角色卡配置

角色卡定义角色的基础档案，支持从 YAML 文件批量导入：

```yaml
# configs/characters/xiaoming.yaml
name: 小明
age: 17
occupation: 高中生
personality:
  - 开朗
  - 细心
  - 有点社恐
traits:
  hobby: 咖啡拉花
  favorite_color: blue
  schedule: early_bird
backstory: |
  从小在小镇长大，父母经营一家咖啡店。
  性格开朗但对陌生人有社恐，喜欢画画和咖啡。
avatar_url: https://cdn.example.com/avatar/xm.png
```

详见 [角色设计](character-design.md)。

---

## 五、Prompt 配置

所有 Prompt 模板外置到 `configs/prompts/*.yaml`（缺失即启动失败），禁止在 Python 代码里内嵌 Prompt 字符串。详见 [Prompt 规范](../docs/rules/prompt-style.md)。

| 文件                            | 用途                       |
| ------------------------------- | -------------------------- |
| `configs/prompts/chat.yaml`     | 角色回复用户消息           |
| `configs/prompts/decision.yaml` | 角色 Action 决策           |
| `configs/prompts/reflection.yaml` | 角色反思生成             |

---

## 六、场景与资源配置

场景元数据（容量 / 开放时段 / 活动词表）定义在 `configs/scenes.yaml`，移动耗时矩阵在 `configs/world-map.yaml`（loader 启动校验对称性与可达性），两者场景 ID 必须一致，loader 加载时交叉校验。

```yaml
# configs/scenes.yaml
scenes:
  - id: cafe
    name: 咖啡店
    open_hours: [7, 22] # 营业时间
    capacity: 20 # 最大容量
    activities: [eat, drink, work_parttime, chat]
  - id: park
    name: 公园
    open_hours: [0, 24]
    capacity: 100
    activities: [relax, chat, exercise]
```

节日事件日历在 `configs/events.yaml`（`event_evolution` 启动加载，损坏即 fail-fast）。详见 [小镇设计](town-design.md)。

---

## 七、默认值速查

| 配置                    | 默认                          | 来源                                 |
| ----------------------- | ----------------------------- | ------------------------------------ |
| World Tick 间隔         | 30s                           | `WORLD_TICK_SECONDS`                  |
| 虚拟时间推进            | 10 分钟 / Tick                | `WORLD_TICK_MINUTES`                  |
| 角色并发上限            | 10                            | `CHARACTER_MAX_CONCURRENT`            |
| 反思阈值                | 20 条                         | `REFLECTION_THRESHOLD`                |
| embedding 维度          | 2048                          | `EMBEDDING_DIM`                       |
| 连接池大小              | 20                            | `DB_POOL_SIZE`                        |
| LLM 超时                | 30s                           | `LLM_TIMEOUT`                         |
| LLM 日预算              | 10.0 USD                      | `LLM_DAILY_BUDGET_USD`                |
| 本地工具开关            | 全部启用                      | Redis `tools:enabled` 未配置时默认    |
| 记忆 LLM 评分           | 关闭 (`false`)                | `MEMORY_LLM_SCORING_ENABLED`          |
| 记忆永久保留阈值        | 7                             | `MEMORY_RETENTION_PERMANENT_IMPORTANCE` |
| 记忆治理周期            | 3600s                         | `MEMORY_RETENTION_INTERVAL_SECONDS`   |
| 记忆压缩批次上限        | 5000                          | `MEMORY_COMPRESSION_BATCH_LIMIT`      |
| 记忆写入门禁            | 开启 (`true`)                 | `MEMORY_WRITE_GATE_ENABLED`           |
| 会话累计轮数硬上限      | 6                             | `CHAT_MAX_TURNS`                      |
| 会话超时                | 2 Tick                        | `CHAT_IDLE_TICKS`                     |
| OneBot 群聊回复模式     | 智能回复 (`false`)            | `ONEBOT_GROUP_AT_ONLY`                |
| JWT 过期                | 24h                           | `JWT_EXPIRE_HOURS`                    |
| 静态 API Key 角色       | `admin`                       | `API_KEY_ROLE`                        |

---

## 八、相关文档

| 主题                 | 文档                                                                      |
| -------------------- | ------------------------------------------------------------------------- |
| 部署环境变量         | [deployment.md](deployment.md#三环境变量清单)                             |
| Docker 部署          | [docker-deployment.md](docker-deployment.md)                              |
| 模块系统             | [module-system.md](module-system.md)                                      |
| 世界引擎参数         | [world-engine.md](world-engine.md#六配置参数)                             |
| 记忆系统（LLM 评分） | [memory-system.md](memory-system.md#九llm-记忆重要程度评分)               |
