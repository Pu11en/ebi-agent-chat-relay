"""Typing in the control center starts a chat — no folder, no menus.

The control center was the one place a typed message did nothing: `on_message`
returned early so the launcher's own control row could not start a session. The
cost was that the fastest way to ask the agent a question — type it — was the
one way that did not work, and every quick question needed New session, a
folder and a thread first.

A quick chat binds no folder on purpose: it runs where this instance's runner
runs by default, exactly like every other unbound thread.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog

pytestmark = pytest.mark.asyncio

CONTROL_CENTER = 500


def _cog(**overrides):
    cog = SimpleNamespace(
        _allowed_user_ids=None,
        _mention_only_channel_ids=set(),
        _channel_ids=set(),
        _monitor_all_channels=False,
        _mention_anywhere=False,
        _chat_only_channel_ids=set(),
        _ensure_thread_members=AsyncMock(),
        _try_send_drewai_lookup_handoff=AsyncMock(return_value=False),
        _try_receive_handoff_message=AsyncMock(return_value=False),
        _claimed_by_task_loop=lambda message: False,
        _try_folder_session=AsyncMock(return_value=False),
        _handle_new_conversation=AsyncMock(),
        _handle_thread_reply=AsyncMock(),
        _handle_mention=AsyncMock(),
    )
    for name, value in overrides.items():
        setattr(cog, name, value)
    cog._is_no_mention_scope = lambda channel: ClaudeChatCog._is_no_mention_scope(cog, channel)
    cog._is_summoned = lambda message: ClaudeChatCog._is_summoned(cog, message)
    return cog


def _message(*, channel, bot: bool = False):
    return SimpleNamespace(
        author=SimpleNamespace(bot=bot, id=42),
        type=discord.MessageType.default,
        channel=channel,
        content="what is in this repo?",
        guild=SimpleNamespace(id=1),
        mentions=[],
    )


async def test_typing_in_the_control_center_starts_a_session(monkeypatch):
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", str(CONTROL_CENTER))
    cog = _cog()
    await ClaudeChatCog.on_message(cog, _message(channel=SimpleNamespace(id=CONTROL_CENTER)))
    cog._handle_new_conversation.assert_awaited_once()


async def test_the_control_row_itself_never_starts_a_session(monkeypatch):
    """The launcher reposts its control row constantly; none of it is a prompt."""
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", str(CONTROL_CENTER))
    cog = _cog()
    await ClaudeChatCog.on_message(
        cog, _message(channel=SimpleNamespace(id=CONTROL_CENTER), bot=True)
    )
    cog._handle_new_conversation.assert_not_awaited()
    cog._try_receive_handoff_message.assert_not_awaited()


async def test_replies_in_a_quick_chat_thread_continue_it(monkeypatch):
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", str(CONTROL_CENTER))
    thread = MagicMock(spec=discord.Thread)
    thread.id = 77
    thread.parent_id = CONTROL_CENTER
    cog = _cog()
    await ClaudeChatCog.on_message(cog, _message(channel=thread))
    cog._handle_thread_reply.assert_awaited_once()
    cog._handle_new_conversation.assert_not_awaited()


async def test_without_a_control_center_nothing_changes(monkeypatch):
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.delenv("CCDB_LAUNCHER_CHANNEL_ID", raising=False)
    cog = _cog()
    await ClaudeChatCog.on_message(cog, _message(channel=SimpleNamespace(id=CONTROL_CENTER)))
    cog._handle_new_conversation.assert_not_awaited()


async def test_a_control_center_listed_as_mention_only_stays_mention_only(monkeypatch):
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", str(CONTROL_CENTER))
    cog = _cog(_mention_only_channel_ids={CONTROL_CENTER})
    assert cog._is_no_mention_scope(SimpleNamespace(id=CONTROL_CENTER)) is False


async def test_a_quick_chat_thread_can_be_reopened_from_sessions():
    """A thread born in the control center is still a session the browser can open.

    ``visible_thread`` gates on the thread's parent, so the control center has
    to be one of the launcher's own channels — otherwise every quick chat would
    be unreachable from **Sessions** the moment it scrolled away.
    """
    from claude_discord.cogs.project_launcher import ProjectLauncherCog

    cog = ProjectLauncherCog.__new__(ProjectLauncherCog)
    ProjectLauncherCog.__init__(
        cog,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        channel_id=1,
        channel_ids={1, 2},
        home_channel_id=CONTROL_CENTER,
    )
    assert CONTROL_CENTER in cog.channel_ids
