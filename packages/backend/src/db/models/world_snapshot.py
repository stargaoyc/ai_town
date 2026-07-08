"""世界快照模型 - 冷启动恢复用

事件溯源 + 定期快照架构：
- world_events: 差分事件（高频，仅状态变化时写入）
- world_snapshots: 完整状态快照（低频，每 1000 Tick 存一次）
- 冷启动：加载最新快照 → 回放之后的增量事件 → 恢复状态

⚠️ world_snapshots 在 0001_init 中创建，v2 曾误删，v3 已恢复保留。
"""
from datetime import datetime
from uuid import UUID
from uuid6 import uuid7

from sqlalchemy import BigInteger, Index, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class WorldSnapshot(Base):
    """世界快照表 - 每 1000 Tick 存一次完整世界状态

    用于冷启动恢复：从最新快照开始，仅回放之后的增量 world_events，
    将启动时间控制在恒定范围内（避免随运行时长线性增长）。

    字段说明：
    - tick_id: 快照对应的 Tick 序号
    - world_time: 虚拟世界时间
    - weather: 天气状态
    - locations: 所有场景状态（拥挤度等）
    - resources: 资源状态
    - active_events: 活跃事件列表
    """
    __tablename__ = "world_snapshots"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid7, comment="快照 ID"
    )
    tick_id: Mapped[int] = mapped_column(
        BigInteger, comment="快照对应的 Tick 序号"
    )
    world_time: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), comment="虚拟世界时间"
    )
    weather: Mapped[str | None] = mapped_column(
        String(20), comment="天气状态"
    )
    locations: Mapped[dict | None] = mapped_column(
        JSONB, comment="所有场景状态 JSON"
    )
    resources: Mapped[dict | None] = mapped_column(
        JSONB, comment="资源状态 JSON"
    )
    active_events: Mapped[dict | None] = mapped_column(
        JSONB, comment="活跃事件列表 JSON"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()", comment="快照创建时间"
    )

    __table_args__ = (
        Index("idx_world_tick", "tick_id"),
    )
