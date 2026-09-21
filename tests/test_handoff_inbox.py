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
    ack = message.channel.send.await_args.args[0]
    assert "accepted" in ack.lower()
    assert TASK_ID[:8] in ack
    # The ack names the job, never the sender's text: a peer must not be able
    # to make this bot post arbitrary prose in a channel it is trusted in.
    assert "Pinterest visual picker process" not in ack


@pytest.mark.asyncio
async def test_handle_handoff_message_refuses_a_packet_from_another_guild(
    handoff_repo: HandoffRepository,
) -> None:
    """The packet's origin guild must be the guild the packet was posted in."""
    message = _inbox_message(format_handoff_message(_handoff_event()))
    message.guild = SimpleNamespace(id=999)  # origin says 111

    result = await handle_handoff_message(
        message,
        repo=handoff_repo,
        local_agent_id="drewai",
        now=NOW,
    )

    assert result is None
    assert await handoff_repo.count_tasks() == 0
    message.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_handoff_message_refuses_a_packet_outside_any_guild(
    handoff_repo: HandoffRepository,
) -> None:
    message = _inbox_message(format_handoff_message(_handoff_event()))
    message.guild = None

    result = await handle_handoff_message(
        message,
        repo=handoff_repo,
        local_agent_id="drewai",
        now=NOW,
    )

    assert result is None
    assert await handoff_repo.count_tasks() == 0


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


@pytest.mark.parametrize(
    ("env", "author_id", "webhook", "member", "expected"),
    [
        ("111,222", 111, None, True, True),  # listed bot
        ("111,222", 333, None, True, False),  # bot not on the list
        ("111", 111, 999, True, False),  # a webhook impersonating a listed bot
        ("", 444, None, True, False),  # no list: nobody, not even a bot member of this server
        ("", 444, 999, True, False),  # no list: webhooks never
        ("", 444, None, False, False),  # no list: a bot from another server never
    ],
)
def test_only_trusted_bots_may_hand_off(
    monkeypatch: pytest.MonkeyPatch,
    env: str,
    author_id: int,
    webhook: int | None,
    member: bool,
    expected: bool,
) -> None:
    """A handoff packet spawns a worker, so only allowlisted bot accounts may send one."""
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    monkeypatch.setenv("CCDB_HANDOFF_TRUSTED_BOT_IDS", env)
    message = MagicMock()
    message.author.id = author_id
    message.author.bot = True
    message.webhook_id = webhook
    message.guild = MagicMock()
    message.guild.id = 111
    message.guild.get_member = MagicMock(return_value=MagicMock() if member else None)
    assert ClaudeChatCog._handoff_sender_trusted(message) is expected


def _packet_from_bot(bot_id: int, *, guild_id: int | None = 111) -> SimpleNamespace:
    return SimpleNamespace(
        id=999,
        content=format_handoff_message(_handoff_event()),
        channel=SimpleNamespace(id=777, parent=None, send=AsyncMock()),
        author=SimpleNamespace(id=bot_id, bot=True),
        webhook_id=None,
        guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
    )


@pytest.mark.asyncio
async def test_legacy_intake_is_off_without_an_allowlist(
    handoff_repo: HandoffRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No CCDB_HANDOFF_TRUSTED_BOT_IDS and no HandoffConfig: no packet is accepted."""
    for name in (
        "CCDB_HANDOFF_TRUSTED_BOT_IDS",
        "CCDB_HANDOFF_GUILD_ID",
        "CCDB_HANDOFF_CHANNEL_ID",
        "CCDB_HANDOFF_AGENTS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CCDB_AGENT_ID", "drewai")
    cog = SimpleNamespace(_handoff_repo=handoff_repo, _handoff_worker_parent_channel=lambda m: None)
    message = _packet_from_bot(4242)

    handled = await ClaudeChatCog._try_receive_handoff_message(cog, message)

    assert handled is False
    assert await handoff_repo.count_tasks() == 0
    message.channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_intake_needs_an_explicit_agent_id(
    handoff_repo: HandoffRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recipient id is never guessed: without CCDB_AGENT_ID nothing is addressed to us."""
    monkeypatch.setenv("CCDB_HANDOFF_TRUSTED_BOT_IDS", "4242")
    monkeypatch.delenv("CCDB_AGENT_ID", raising=False)
    monkeypatch.delenv("CCDB_HANDOFF_AGENTS", raising=False)
    cog = SimpleNamespace(_handoff_repo=handoff_repo, _handoff_worker_parent_channel=lambda m: None)
    message = _packet_from_bot(4242)

    handled = await ClaudeChatCog._try_receive_handoff_message(cog, message)

    assert handled is False
    assert await handoff_repo.count_tasks() == 0


@pytest.mark.asyncio
async def test_legacy_intake_refuses_an_allowlisted_bot_from_another_guild(
    handoff_repo: HandoffRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCDB_HANDOFF_TRUSTED_BOT_IDS", "4242")
    monkeypatch.setenv("CCDB_AGENT_ID", "drewai")
    monkeypatch.delenv("CCDB_HANDOFF_AGENTS", raising=False)
    cog = SimpleNamespace(_handoff_repo=handoff_repo, _handoff_worker_parent_channel=lambda m: None)
    message = _packet_from_bot(4242, guild_id=999)  # packet origin says guild 111

    handled = await ClaudeChatCog._try_receive_handoff_message(cog, message)

    assert handled is False
    assert await handoff_repo.count_tasks() == 0


@pytest.mark.asyncio
async def test_setup_bridge_says_once_that_handoffs_are_disabled(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from claude_discord.setup import setup_bridge

    for name in (
        "CCDB_HANDOFF_TRUSTED_BOT_IDS",
        "CCDB_HANDOFF_GUILD_ID",
        "CCDB_HANDOFF_CHANNEL_ID",
        "CCDB_HANDOFF_AGENTS",
    ):
        monkeypatch.delenv(name, raising=False)
    bot = MagicMock()
    bot.channel_id = 123
    bot.add_cog = AsyncMock()
    bot.cogs = {}
    bot.wait_until_ready = AsyncMock()
    runner = MagicMock()
    runner.model = "sonnet"
    runner.working_dir = str(tmp_path)
    runner.api_port = None

    with caplog.at_level(logging.INFO, logger="claude_discord.setup"):
        await setup_bridge(
            bot,
            runner,
            session_db_path=str(tmp_path / "sessions.db"),
            enable_scheduler=False,
            worktree_base_dir=str(tmp_path / "worktrees"),
        )

    disabled = [r for r in caplog.records if "handoffs disabled" in r.getMessage().lower()]
    assert len(disabled) == 1
    assert "CCDB_HANDOFF_TRUSTED_BOT_IDS" in disabled[0].getMessage()
