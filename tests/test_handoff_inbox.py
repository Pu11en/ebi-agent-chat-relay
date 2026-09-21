"""Tests for receiving trusted Discord handoff packets."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.handoff_repo import HandoffRepository
from claude_discord.database.models import init_db
from claude_discord.handoff_inbox import handle_handoff_message
from claude_discord.handoff_messages import format_handoff_message
from claude_discord.handoff_sender import build_project_lookup_handoff_event
from claude_discord.handoff_triggers import DrewAILookupTrigger

NOW = datetime(2026, 9, 20, 23, 30, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


@pytest.fixture
async def handoff_repo() -> AsyncIterator[HandoffRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield HandoffRepository(path)
    finally:
        os.unlink(path)


def _origin_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=444,
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, parent_id=222),
        author=SimpleNamespace(id=555),
    )


def _handoff_event():
    return build_project_lookup_handoff_event(
        DrewAILookupTrigger("the Pinterest visual picker process"),
        origin_message=_origin_message(),
        sender_agent_id="david",
        now=NOW,
        id_factory=lambda: (TASK_ID, EVENT_ID),
    )


def _inbox_message(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=999,
        content=content,
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=777, send=AsyncMock()),
        author=SimpleNamespace(id=888, bot=True),
        type=discord.MessageType.default,
    )


@pytest.mark.asyncio
async def test_handle_handoff_message_records_task_event_and_acknowledges(
    handoff_repo: HandoffRepository,
) -> None:
    event = _handoff_event()
    message = _inbox_message(format_handoff_message(event))

    result = await handle_handoff_message(
        message,
        repo=handoff_repo,
        local_agent_id="drewai",
        now=NOW,
    )

    assert result is not None
    assert result.accepted is True
    assert result.created is True
    assert await handoff_repo.get_task(TASK_ID, "drewai") == event.task
    assert await handoff_repo.has_event(EVENT_ID)
    message.channel.send.assert_awaited_once()
    assert "accepted" in message.channel.send.await_args.args[0].lower()
    assert "Pinterest visual picker process" in message.channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_handoff_message_deduplicates_redelivery(
    handoff_repo: HandoffRepository,
) -> None:
    event = _handoff_event()
    message = _inbox_message(format_handoff_message(event))

    first = await handle_handoff_message(
        message,
        repo=handoff_repo,
        local_agent_id="drewai",
        now=NOW,
    )
    second = await handle_handoff_message(
        message,
        repo=handoff_repo,
        local_agent_id="drewai",
        now=NOW,
    )

    assert first is not None
    assert second is not None
    assert first.created is True
    assert second.created is False
    assert await handoff_repo.count_tasks() == 1
    assert await handoff_repo.list_events(TASK_ID)


@pytest.mark.asyncio
async def test_handle_handoff_message_ignores_other_recipients(
    handoff_repo: HandoffRepository,
) -> None:
    message = _inbox_message(format_handoff_message(_handoff_event()))

    result = await handle_handoff_message(
        message,
        repo=handoff_repo,
        local_agent_id="imac",
        now=NOW,
    )

    assert result is None
    assert await handoff_repo.count_tasks() == 0
    message.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_cog_processes_bot_handoff_before_ignoring_bot_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.delenv("CCDB_LAUNCHER_CHANNEL_ID", raising=False)
    cog = SimpleNamespace(
        _try_receive_handoff_message=AsyncMock(return_value=True),
        _handle_thread_reply=AsyncMock(),
        _handle_new_conversation=AsyncMock(),
        _is_no_mention_scope=lambda channel: True,
        _allowed_user_ids=None,
        _claimed_by_task_loop=lambda message: False,
    )
    message = _inbox_message("CCDB_HANDOFF_V1\n```json\n{}\n```")

    await ClaudeChatCog.on_message(cog, message)

    cog._try_receive_handoff_message.assert_awaited_once_with(message)
    cog._handle_thread_reply.assert_not_awaited()
    cog._handle_new_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_bridge_attaches_handoff_repo(tmp_path) -> None:
    from claude_discord.setup import setup_bridge

    bot = MagicMock()
    bot.channel_id = 123
    bot.add_cog = AsyncMock()
    bot.cogs = {}
    bot.wait_until_ready = AsyncMock()
    runner = MagicMock()
    runner.model = "sonnet"
    runner.working_dir = str(tmp_path)
    runner.api_port = None

    components = await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),
        enable_scheduler=False,
        worktree_base_dir=str(tmp_path / "worktrees"),
    )

    assert components.handoff_repo is bot.handoff_repo
    assert isinstance(bot.handoff_repo, HandoffRepository)
