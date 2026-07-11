"""世界事件模型 - 差分事件记录

事件溯源 + 定期快照架构：
- world_events: 差分事件（高频，仅状态变化时写入）
- world_snapshots: 完整状态快照（低频，每 1000 Tick 存一次）
- 冷启动恢复：加载最新快照 → 回放之后的增量事件 → 恢复状态

幂等性保证：UNIQUE(tick_id, event_type, event_key) 约束防止重复写入，
支持同一 Tick 同一类型的多条事件（event_key 区分不同实体）。
服务重启 / Tick 重试时自动跳过已存在的事件。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import BigInteger, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class WorldEvent(Base):
    """世界事件表 - 记录增量变更

    event_type 取值：
    - time: 时间推进
    - weather: 天气变化
    - scene: 场景状态变化
    - resource: 资源变化
    - event: 节日/特殊事件

    event_key 取值：
    - 默认 "default"（全局事件，如时间/天气）
    - 实体级事件使用实体 ID（如场景 ID），支持同 Tick 同类型多条

    payload 示例：
    - time: {"virtual_time": "2026-07-06T10:30:00", "tick_id": 42}
    - weather: {"from": "sunny", "to": "rainy"}
    - scene: {"scene_id": "cafe", "crowdedness": 0.8}

    幂等性：UNIQUE(tick_id, event_type, event_key) 约束保证幂等写入。
    """
    __tablename__ = "world_events"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="事件 ID"
    )
    tick_id: Mapped[int] = mapped_column(BigInteger, comment="Tick 序号")
    event_type: Mapped[str] = mapped_column(
        String(30), comment="事件类型：time/weather/scene/resource/event"
    )
    event_key: Mapped[str] = mapped_column(
        String(100), default="default",
        comment="事件键（区分同 Tick 同类型不同实体，默认 default）"
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, comment="变更内容（仅差分）"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="创建时间"
    )

    __table_args__ = (
        # 幂等约束：同一 Tick 同一类型同一 key 事件唯一
        UniqueConstraint("tick_id", "event_type", "event_key", name="uq_world_events_tick_type_key"),
        Index("idx_world_events_tick", "tick_id"),
        Index("idx_world_events_type_time", "event_type", "created_at"),
    )
