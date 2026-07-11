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

    # Storage
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str = "ai-town"
    minio_secure: bool = False

    # LLM
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    model_chat: str = "gpt-4o-mini"
    model_strong: str = "gpt-4o"
    model_flash: str = "gpt-3.5-turbo"
    model_embedding: str = "text-embedding-3-small"
    embedding_model_key: str | None = None
    embedding_model_url: str | None = None
    llm_timeout: int = 30
    llm_max_retries: int = 2
    embedding_dim: int = 1536

    # MCP
    mcp_tool_timeout: int = 30

    # Observability
    otel_endpoint: str | None = None
    otel_service_name: str = "ai-town-backend"
    otel_traces_sampler_rate: float = 0.5
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    loki_url: str = "http://loki:3100"
    log_level: str = "info"
    log_format: str = "json"

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    api_key: str | None = None
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # Cost Control
    llm_daily_budget_usd: float = 10.0
    llm_circuit_breaker_threshold: int = 5
    llm_circuit_breaker_recovery_timeout: int = 60

    # World Engine
    world_tick_seconds: int = 30
    world_tick_minutes: int = 10
    world_weather_interval: int = 60
    world_snapshot_interval: int = 120  # 每 N Tick 持久化差分事件到 world_events
    world_full_snapshot_interval: int = 1000  # 每 N Tick 存一次完整快照到 world_snapshots（冷启动恢复）
    character_tick_seconds: int = 30
    character_max_concurrent: int = 10
    character_lock_ttl_seconds: int = 30

    # OneBot 适配器
    onebot_default_character_id: str | None = None
    # 机器人自身 QQ 号（用于群聊 @ 检测，从 OneBot 事件的 self_id 也能获取）
    onebot_self_id: str | None = None
    # 群聊是否仅在被 @ 时回复（默认 True，避免刷屏）
    onebot_group_at_only: bool = True
    # 群组-角色映射：JSON 字符串 {"群号": "角色UUID"}，未配置的群使用默认角色
    onebot_group_character_map: str = "{}"


settings = Settings()
