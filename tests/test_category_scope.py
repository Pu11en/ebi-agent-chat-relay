"""Category command routing without Discord calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.category_scope import category_allowed, install_category_check


@pytest.mark.parametrize("category,expected", [(123, True), (456, False), (None, False)])
def test_category_boundary(monkeypatch, category, expected):
    monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "123")
    assert category_allowed(SimpleNamespace(category_id=category)) is expected


def test_thread_uses_parent_category(monkeypatch):
    monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "123")
    thread = MagicMock(spec=discord.Thread)
    thread.parent = SimpleNamespace(category_id=123)
    assert category_allowed(thread)
    thread.parent = SimpleNamespace(category_id=456)
    assert not category_allowed(thread)


def test_unconfigured_preserves_existing_behavior(monkeypatch):
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    assert category_allowed(None)


async def test_wrong_category_command_stops_before_original_check(monkeypatch):
    monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "123")
    previous = AsyncMock(return_value=True)
    bot = SimpleNamespace(tree=SimpleNamespace(interaction_check=previous))
    install_category_check(bot)
    event = SimpleNamespace(
        channel=SimpleNamespace(category_id=456),
        type=discord.InteractionType.application_command,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    assert not await bot.tree.interaction_check(event)
    previous.assert_not_awaited()
    event.response.send_message.assert_awaited_once()
    event.channel.category_id = 123
    assert await bot.tree.interaction_check(event)
    previous.assert_awaited_once_with(event)


async def test_autocomplete_outside_category_returns_no_suggestions(monkeypatch):
    monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "123")
    bot = SimpleNamespace(tree=SimpleNamespace(interaction_check=AsyncMock()))
    install_category_check(bot)
    event = SimpleNamespace(
        channel=None,
        type=discord.InteractionType.autocomplete,
        response=SimpleNamespace(autocomplete=AsyncMock()),
    )
    assert not await bot.tree.interaction_check(event)
    event.response.autocomplete.assert_awaited_once_with([])


async def test_chat_outside_category_never_joins_or_starts(monkeypatch):
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "123")
    channel = MagicMock(spec=discord.Thread)
    channel.parent = SimpleNamespace(category_id=456)
    cog = SimpleNamespace(
        _allowed_user_ids={42},
        _ensure_thread_members=AsyncMock(),
        _is_no_mention_scope=lambda channel: True,
        _handle_thread_reply=AsyncMock(),
    )
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=42), type=discord.MessageType.default, channel=channel
    )
    await ClaudeChatCog.on_message(cog, message)
    cog._ensure_thread_members.assert_not_awaited()
    cog._handle_thread_reply.assert_not_awaited()


async def test_the_launcher_control_row_does_not_start_chat(monkeypatch):
    """Typing in the control center starts a session; the bot's own row never does.

    See tests/test_quick_chat.py for the other half — a typed message there is
    a quick chat, which is why this guard is about the author and not the
    channel any more.
    """
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", "500")
    cog = SimpleNamespace(
        _allowed_user_ids=None,
        _is_no_mention_scope=lambda channel: True,
        _handle_new_conversation=AsyncMock(),
        _try_receive_handoff_message=AsyncMock(return_value=False),
    )
    message = SimpleNamespace(
        author=SimpleNamespace(bot=True),
        type=discord.MessageType.default,
        channel=SimpleNamespace(id=500),
    )
    await ClaudeChatCog.on_message(cog, message)
    cog._handle_new_conversation.assert_not_awaited()
    cog._try_receive_handoff_message.assert_not_awaited()
