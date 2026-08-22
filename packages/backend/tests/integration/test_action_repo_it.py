"""ActionRepository 集成测试 - RANGE 分区表写入与时间线查询

覆盖文档「测试覆盖缺口」P1 项：
- 行为记录落库到按月 RANGE 分区（当前日期落入 default 分区或预创建分区均可）
- get_by_character 时间线倒序 + limit
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import ActionRecord, Character
from src.db.repositories.action_repo import ActionRepository


async def _make_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="行动测试角色")
    it_session.add(char)
    await it_session.flush()
    return char


class TestActionTimeline:
    async def test_add_and_get_by_character_desc(self, it_session: AsyncSession) -> None:
        char = await _make_character(it_session)
        repo = ActionRepository(it_session)

        for i in range(3):
            await repo.add(
                ActionRecord(
                    character_id=char.id,
                    action_id="read_book",
                    action_name="读书",
                    duration_minutes=30,
                    reason=f"第 {i} 次阅读",
                )
            )

        timeline = await repo.get_by_character(char.id, limit=10)

        assert len(timeline) == 3
        assert [r.reason for r in timeline] == ["第 2 次阅读", "第 1 次阅读", "第 0 次阅读"]
        assert all(r.action_id == "read_book" for r in timeline)

    async def test_record_lands_in_a_partition(self, it_session: AsyncSession) -> None:
        """插入的记录必须真实路由到某个子分区（RANGE 分区生效；0002 未建 default 分区，
        超出预建月份范围的写入会直接报错，因此能插入即代表路由成功）"""
        char = await _make_character(it_session)
        await ActionRepository(it_session).add(
            ActionRecord(character_id=char.id, action_id="wait", action_name="等待", duration_minutes=5)
        )

        result = await it_session.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_inherits i ON i.inhrelid = c.oid "
                "WHERE i.inhparent = 'action_records'::regclass "
                "AND EXISTS (SELECT 1 FROM action_records p WHERE p.character_id = :cid)"
            ),
            {"cid": char.id},
        )
        # 记录总数为 1 即已成功路由到某个月份分区
        total = await it_session.execute(
            text("SELECT COUNT(*) FROM action_records WHERE character_id = :cid"),
            {"cid": char.id},
        )
        assert total.scalar_one() == 1
        assert result.scalars().first() is not None
