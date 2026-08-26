# src/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    # Database
    database_url: str
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_echo: bool = False

    # Redis
    redis_url: str

    # LLM
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    model_chat: str = "gpt-4o-mini"
    # 图像生成模型（P1-23：此前误用 model_strong 强推理模型计费）
    model_image: str = "agnes-image-2.1-flash"
    # 视频生成模型（原 MODEL_FLASH 更名：该档位实际只被 generate_video 消费）
    model_video: str = "agnes-video-v2.0"
    model_embedding: str = "text-embedding-3-small"
    embedding_model_key: str | None = None
    embedding_model_url: str | None = None
    llm_timeout: int = 30
    llm_max_retries: int = 2

    # 视频生成同步轮询（R5-L13）：generate_video_clip 在 Tick 槽位内轮询直至完成，
    # 上限 = max_polls × interval 秒。默认 120×5s≈10 分钟；调小可缩短角色被占用的时长，
    # 但过小会导致长视频任务必然超时失败
    media_video_poll_interval: int = 5
    media_video_max_polls: int = 120

    # 多模型备用源（JSON 数组，按顺序尝试；失败冷却 5 分钟后仍可作末位兜底）
    # 每项: {"api_key": "...", "base_url": "...", "model": "可选，缺省用 model_chat"}
    llm_fallback_sources: str = "[]"

    # LLM 单价（USD / 1M tokens）。默认为 agnes-2.0-flash 价格；
    # 更换模型供应商时必须同步修改，否则成本指标与日预算控制失真（审查 §八）
    llm_price_input_per_mtoken: float = 0.5
    llm_price_output_per_mtoken: float = 1.5
    # 按模型单价覆盖（USD / 1M tokens）：
    # {"gpt-4o-mini": {"input": 0.15, "output": 0.6}, "gpt-4o": {"input": 2.5, "output": 10.0}}
    # chat/strong/flash 常配不同价位模型，仅设全局单价时成本统计必然失真
    llm_model_prices: str = ""

    # 与迁移 0005 的物理列 halfvec(2048) 对齐；改此值必须同步新迁移重建列与 HNSW 索引
    embedding_dim: int = 2048

    # 世界历史保留（审查 §11.2：world_events/world_snapshots 此前无清理策略，长期运行持续增长）
    world_events_retention_days: int = 90
    world_snapshots_keep_latest: int = 3

    # RANGE 分区表保留策略（三轮审查 H9：分区此前只建不删，action_records/state_history
    # 随 Tick 无限累积——按月分区必须配套按月丢弃才有生命周期闭环；增速量级
    # 见 partition_scheduler 模块注释，以 ai_town_character_tick_total 实测为准）
    action_records_retention_months: int = 12
    state_history_retention_months: int = 6

    # 消息表保留天数（三轮审查 M1：messages 刻意不分区却无清理任务；0 = 永久保留）
    messages_retention_days: int = 180

    # 认知产物保留（R4-M7）：reflections/diaries/PM 已压缩条目/归档行四类此前
    # 无任何清理路径，「长期运行记忆不膨胀」对这四类不成立；0 = 对应类永久保留
    reflection_retention_days: int = 365  # 仅 tier=1 批次反思；tier=2 元反思永久保留
    diary_retention_days: int = 730
    person_memory_entry_retention_days: int = 180  # 仅清理已压缩（compacted）条目
    archive_episode_retention_days: int = 365  # source_type=archive 的归档行

    # plans 终态修剪（R5-L5）：completed/abandoned/expired 行此前只翻状态不删除，
    # 无界累积；0 = 永久保留
    plans_retention_days: int = 90

    # 保留周期单批删除行数（R5-L4）：大积压时一次性 DELETE 是长事务，锁与 WAL
    # 一口气生成；所有保留周期共用此旋钮分批删空
    retention_delete_batch_size: int = 5000

    # Observability
    otel_endpoint: str | None = None
    otel_service_name: str = "ai-town-backend"
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    loki_url: str = "http://loki:3100"
    log_level: str = "info"
    log_format: str = "json"
    # 日志文件轮转（RotatingFileHandler）：单文件上限与保留份数，
    # 长驻进程不轮转会写满磁盘
    log_file_max_bytes: int = 50_000_000
    log_backup_count: int = 5

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    api_key: str | None = None
    admin_username: str = "admin"
    admin_password: str = "admin123"
    # Alertmanager webhook 回流鉴权 token（R4-M2）：未配置时告警端点返回 403，
    # 与 alertmanager.yml 的 bearer_token 占位符替换值保持一致
    alert_webhook_token: str = ""
    # RBAC 角色配置（逗号分隔的用户名:角色列表）
    rbac_roles: str = ""  # 如 "admin:admin,viewer1:viewer,operator1:operator"

    # CORS（逗号分隔的具体来源列表；allow_credentials=True 与通配符 * 互斥，
    # 生产必须配置实际前端域名，如 "https://town.example.com,https://www.town.example.com"）
    cors_origins: str = ""

    # 运行环境标识：production 时启用安全 fail-fast（如默认口令禁止启动）
    environment: str = "development"

    # Cost Control
    llm_daily_budget_usd: float = 10.0
    llm_circuit_breaker_threshold: int = 5
    llm_circuit_breaker_recovery_timeout: int = 60

    # Memory LLM Scoring
    memory_llm_scoring_enabled: bool = False

    # Memory Retention（记忆生命周期治理，审查 §七-P1：HASH 分区无法按时间 drop，
    # 必须应用层定期清理低价值老记忆，否则每角色年增百万行 + GB 级向量）
    memory_retention_enabled: bool = True
    memory_retention_low_importance_days: int = 90  # importance<=3 保留天数
    memory_retention_mid_importance_days: int = 180  # importance 4-6 保留天数；importance>=7 永久保留

    # 记忆压缩归档（retention 删除前先按角色×月份 LLM 压缩成归档行，
    # 压缩失败则整组跳过留待下周期——绝不未压缩先删除）
    memory_compression_enabled: bool = True
    memory_compression_min_batch: int = 5  # 单组少于该条数不压缩（摘要收益低于成本）
    memory_compression_batch_limit: int = 300  # 单周期最多处理的候选条数

    # 改写式记忆去重（向量化时与同角色近窗口记忆余弦比对，
    # pg_trgm 对中文无效已实测证伪，向量比对是可靠信号——复审 N7）
    memory_dedup_enabled: bool = True
    memory_dedup_similarity_threshold: float = 0.95
    memory_dedup_window_hours: int = 24

    # Plan 层级体系：当日计划滚动过期（创建超过 TTL 的 active daily 置 expired）
    daily_plan_ttl_hours: int = 24

    # 反思机制参数（P1-10：此前硬编码为类常量，调参需改码发版）
    reflection_threshold: int = 20  # 未反思记忆达到该数量触发批次反思
    reflection_pool_size: int = 30  # 单次参与归纳的记忆池上限
    meta_reflection_min_total: int = 6  # 累计反思达到该数量后才考虑元反思
    meta_reflection_cooldown_days: int = 7  # 两次元反思的最小间隔
    meta_source_limit: int = 10  # 元反思读取的最近 tier-1 反思条数

    # 计划自动推进（P1-13）：Action 完成后按标题与决策理由的字符二元组重叠
    # 启发式推进相关计划进度；0 关闭。delta 为单次推进百分比，overlap 为触发阈值
    plan_auto_progress_enabled: bool = True
    plan_auto_progress_delta: int = 10
    plan_auto_progress_overlap: float = 0.34

    # HNSW 索引维护（P1-1）：保留周期大量 DELETE 后索引项不被 VACUUM 回收，
    # 定期 REINDEX CONCURRENTLY 在线重建；间隔天数 0 关闭
    hnsw_reindex_enabled: bool = True
    hnsw_reindex_interval_days: int = 30

    # Person Memory 热度上限（P2-11）：高频用户 heat 无界增长会与低频用户
    # 拉开数千倍差距，检索排序失去区分度；交互累加时钳制到该值
    person_memory_heat_cap: int = 500

    # 传闻单源传播上限（P2-12）：同一源记忆在窗口内最多扩散给 N 个听者，
    # 抑制多好友路径对同一事件的放大
    gossip_max_listeners_per_source: int = 3

    # 公开只读 GET 端点限流（P1-25）：每 IP 每分钟请求数上限，0 关闭
    public_get_rate_limit_per_minute: int = 120

    # HNSW 检索参数（SET LOCAL 事务内生效；调大提升召回率、增加延迟）
    hnsw_ef_search: int = 100

    # Person Memory 两层结构：未压缩事实条目达到阈值后合并进主档
    person_memory_compact_threshold: int = 20

    # World Engine
    world_tick_seconds: int = 30
    world_tick_minutes: float = 10.0
    world_initial_time: str = ""  # 虚拟世界初始时间（ISO 格式，如 "2026-07-01T08:00:00"）；留空则使用当前现实日期 08:00
    world_weather_interval: int = 60
    world_snapshot_interval: int = 10  # 每 N Tick 持久化差分事件到 world_events（降低以让前端事件时间线更快有数据）
    world_full_snapshot_interval: int = 1000  # 每 N Tick 存一次完整快照到 world_snapshots（冷启动恢复）
    character_tick_seconds: int = 30
    character_max_concurrent: int = 10
    character_lock_ttl_seconds: int = 30

    # 主动分享配置
    share_cooldown_seconds: int = 1800  # 分享冷却时间（秒），同一角色两次分享的最小间隔
    share_daily_limit: int = 8  # 单角色每日最大主动分享次数（防刷屏）
    share_probability_action: float = 0.6  # 特定 Action 完成时的分享概率（0.0-1.0）
    share_probability_mood: float = 0.5  # 强烈情绪时的分享概率（0.0-1.0）
    share_probability_location: float = 0.2  # 位置变化时的分享概率（0.0-1.0）
    share_probability_routine: float = 0.15  # 日常行为的分享概率（0.0-1.0）

    # 群体动力学·传闻传播：好友的高重要性经历以第二手记忆扩散，
    # 内容取自源记忆原文（模板拼接非 LLM 编造），importance 减半保真度递减
    gossip_enabled: bool = True
    gossip_importance_threshold: int = 7  # 源记忆重要性门槛（仅显著经历值得传播）
    gossip_window_hours: int = 24  # 源记忆与去重回窗（小时）；每好友每窗口最多传播 1 条
    gossip_max_per_tick: int = 1  # 单次 Tick 最多传播条数（控制记忆膨胀速率）
    gossip_relation_min: int = 20  # 好友关系强度门槛（传闻沿既有社交关系流动）

    # 角色间多轮对话：每轮双方各生成一句，轮数上限 3（控制单次 chat_with 的 LLM 成本）
    chat_with_max_rounds: int = 2
    # 对话结束后由 LLM 评估关系增量（替代固定 +5/+2）；关闭则回退固定值
    chat_quality_enabled: bool = True
    # 用户对话链路注入反思/日记（默认关闭保持上下文精简；开启后角色在 QQ 对话中体现近期心境与长期倾向）
    chat_inject_cognition: bool = False

    # OneBot 适配器
    onebot_default_character_id: str | None = None
    # 机器人自身 QQ 号（用于群聊 @ 检测，从 OneBot 事件的 self_id 也能获取）
    onebot_self_id: str | None = None
    # 群聊是否仅在被 @ 时回复（默认 False：读取所有群消息并智能决策是否回复）
    onebot_group_at_only: bool = False
    # 群组-角色映射：JSON 字符串 {"群号": "角色UUID"}，未配置的群使用默认角色
    onebot_group_character_map: str = "{}"
    # OneBot 反向 WS 接入令牌：配置后强制校验 Authorization: Bearer / access_token 参数。
    # 生产环境（ENVIRONMENT=production）未配置将拒绝启动（R5-H5：无令牌端点可被任意伪造事件）
    onebot_access_token: str | None = None
    # 单群/单私聊每分钟入站消息上限（R5-M7）：洪泛会逐条触发 judge+reply 两次 LLM
    # 调用打爆预算；超限静默丢弃。0=禁用
    onebot_rate_limit_per_minute: int = 20
    # OneBot 事件流长度上限（round-3 H3：Streams 必须配 maxlen，否则已处理条目与
    # 死信永久累积；XDEL 只删单条，历史长度仍需上限收敛）
    onebot_stream_maxlen: int = 10_000
    # 同一会话两条出站消息的最小间隔毫秒（R6-L6）：降低 QQ 风控触发概率。0=禁用
    onebot_send_min_interval_ms: int = 1000
    # 心跳过期阈值秒（R6-L6）：超过该时长未收到心跳的连接被主动 close，
    # 由 OneBot 实现自动重连，避免半开连接长期占据发送候选位
    onebot_heartbeat_stale_seconds: float = 90.0


settings = Settings()  # type: ignore[call-arg]

# P3-2 配套：空字符串归一为 None——setup_tracing 以 is None 判定「禁用」，
# ci 冒烟覆盖层用 OTEL_ENDPOINT="" 显式关闭 tracing，避免空串被当端点尝试导出
if settings.otel_endpoint is not None and not settings.otel_endpoint.strip():
    settings.otel_endpoint = None
