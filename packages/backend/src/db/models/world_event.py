"""世界事件模型 - 差分事件记录

事件溯源 + 定期快照架构：
- world_events: 差分事件（高频，仅状态变化时写入）
- world_snapshots: 完整状态快照（低频，每 1000 Tick 存一次）
- 冷启动恢复：加载最新快照 → 回放之后的增量事件 → 恢复状态
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import BigInteger, Index, String
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

    payload 示例：
    - time: {"virtual_time": "2026-07-06T10:30:00", "tick_id": 42}
    - weather: {"from": "sunny", "to": "rainy"}
    - scene: {"scene_id": "cafe", "crowdedness": 0.8}
    """
    __tablename__ = "world_events"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="事件 ID"
    )
    tick_id: Mapped[int] = mapped_column(BigInteger, comment="Tick 序号")
    event_type: Mapped[str] = mapped_column(
        String(30), comment="事件类型：time/weather/scene/resource/event"
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, comment="变更内容（仅差分）"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default="now()", comment="创建时间"
    )

    __table_args__ = (
        Index("idx_world_events_tick", "tick_id"),
        Index("idx_world_events_type_time", "event_type", "created_at"),
    )
