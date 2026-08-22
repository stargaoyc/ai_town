"""Conversation/Message Repository 集成测试

覆盖文档「测试覆盖缺口」P1 项：
- 会话幂等创建（ON CONFLICT 唯一键 user_id+platform+character_id）
- 消息持久化与游标分页
- token/cost 聚合（A-7 真实 usage 持久化的下游验证）
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.db.models import Character
from src.db.repositories.conversation_repo import ConversationRepository
from src.db.repositories.message_repo import MessageRepository


@pytest_asyncio.fixture
async def a_character(it_session: AsyncSession) -> Character:
    char = Character(id=uuid7(), name="小艾")
    it_session.add(char)
    await it_session.flush()
    return char


class TestConversationRepository:
    async def test_get_or_create_idempotent(self, it_session: AsyncSession, a_character: Character) -> None:
        repo = ConversationRepository(it_session)

        first = await repo.get_or_create(a_character.id, "user-it", platform="web")
        second = await repo.get_or_create(a_character.id, "user-it", platform="web")

        assert second.id == first.id
        assert first.user_id == "user-it"
        assert first.platform == "web"

    async def test_same_user_different_platform_gets_separate_conversations(
        self, it_session: AsyncSession, a_character: Character
    ) -> None:
        repo = ConversationRepository(it_session)

        web_conv = await repo.get_or_create(a_character.id, "user-it", platform="web")
        qq_conv = await repo.get_or_create(a_character.id, "user-it", platform="qq")

        assert web_conv.id != qq_conv.id


class TestMessageRepository:
    async def test_add_and_list_with_cursor_pagination(self, it_session: AsyncSession, a_character: Character) -> None:
        conv = await ConversationRepository(it_session).get_or_create(a_character.id, "user-it", platform="web")
        msg_repo = MessageRepository(it_session)

        for i in range(5):
            await msg_repo.add(
                conversation_id=conv.id,
                sender="user" if i % 2 == 0 else "character",
                content=f"消息 {i}",
                tokens=100 + i if i % 2 else None,
            )

        newest_first = await msg_repo.list_by_conversation(conv.id, limit=3)
        assert [m.content for m in newest_first] == ["消息 4", "消息 3", "消息 2"]

        cursor_msg = newest_first[-1]
        older_page = await msg_repo.list_by_conversation(
            conv.id, limit=10, before=cursor_msg.created_at, before_id=cursor_msg.id
        )
        assert [m.content for m in older_page] == ["消息 1", "消息 0"]

        chronological = await msg_repo.list_recent(conv.id, limit=20)
        assert [m.content for m in chronological] == [f"消息 {i}" for i in range(5)]

    async def test_sum_tokens_and_cost(self, it_session: AsyncSession, a_character: Character) -> None:
        conv = await ConversationRepository(it_session).get_or_create(a_character.id, "user-it", platform="web")
        msg_repo = MessageRepository(it_session)

        await msg_repo.add(conversation_id=conv.id, sender="user", content="你好")
        await msg_repo.add(conversation_id=conv.id, sender="character", content="你好呀", tokens=120, cost=0.5)
        await msg_repo.add(conversation_id=conv.id, sender="character", content="再见", tokens=80, cost=0.25)

        conv_tokens, conv_cost = await msg_repo.sum_tokens_by_conversation(conv.id)
        assert (conv_tokens, round(conv_cost, 6)) == (200, 0.75)

        char_tokens, char_cost = await msg_repo.sum_tokens_by_character(a_character.id)
        assert (char_tokens, round(char_cost, 6)) == (200, 0.75)
