"""A closing session goes quiet: nothing posts in a thread after it is archived.

Discord un-archives a thread the moment anything posts in it. The turn's
finalizer used to complete a pending close and *then* post the reply-needed
ping and schedule the context nudge — so the thread was archived, locked, and
back in the sidebar a second later. From the outside, "close this session"
visibly did nothing, which is exactly what it was reported as doing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.discord_ui.thread_dashboard import ThreadState

pytestmark = pytest.mark.asyncio


def _cog(record):
    cog = SimpleNamespace(
        repo=SimpleNamespace(get=AsyncMock(return_value=record)),
        lifecycle=SimpleNamespace(complete_pending_close=AsyncMock()),
        context_nudger=MagicMock(),
    )
    cog._close_requested = lambda thread_id: ClaudeChatCog._close_requested(cog, thread_id)
    cog._complete_pending_close = lambda thread_id: ClaudeChatCog._complete_pending_close(
        cog, thread_id
    )
    return cog


def _record(state: str):
    return SimpleNamespace(
        lifecycle_state=state,
        is_open=state == "open",
        close_pending=state == "closing",
        is_closed=state == "closed",
    )


async def test_a_closing_session_is_not_pinged_back_to_life():
    cog = _cog(_record("closing"))
    dashboard = AsyncMock()
    thread = SimpleNamespace(id=7)

    await ClaudeChatCog._finish_turn(
        cog, thread, dashboard=dashboard, description="d", notify_user_id=42
    )

    cog.lifecycle.complete_pending_close.assert_awaited_once_with(7)
    dashboard.set_state.assert_not_awaited()
    dashboard.remove.assert_awaited_once_with(7)
    cog.context_nudger.after_turn.assert_not_called()


async def test_an_already_closed_session_stays_silent_too():
    cog = _cog(_record("closed"))
    dashboard = AsyncMock()

    await ClaudeChatCog._finish_turn(
        cog, SimpleNamespace(id=7), dashboard=dashboard, description="d", notify_user_id=42
    )

    dashboard.set_state.assert_not_awaited()
    cog.context_nudger.after_turn.assert_not_called()


async def test_an_open_session_still_asks_for_the_next_reply():
    cog = _cog(_record("open"))
    dashboard = AsyncMock()
    thread = SimpleNamespace(id=7)

    await ClaudeChatCog._finish_turn(
        cog, thread, dashboard=dashboard, description="d", notify_user_id=42
    )

    dashboard.set_state.assert_awaited_once_with(
        7, ThreadState.WAITING_INPUT, "d", thread=thread, notify_user_id=42
    )
    dashboard.remove.assert_not_awaited()
    cog.context_nudger.after_turn.assert_called_once_with(thread)


async def test_a_thread_with_no_session_row_behaves_like_an_open_one():
    cog = _cog(None)
    dashboard = AsyncMock()

    await ClaudeChatCog._finish_turn(
        cog, SimpleNamespace(id=7), dashboard=dashboard, description="d", notify_user_id=42
    )

    dashboard.set_state.assert_awaited_once()
    cog.context_nudger.after_turn.assert_called_once()


async def test_an_unreadable_session_row_never_blocks_the_turn_ending():
    cog = _cog(None)
    cog.repo.get = AsyncMock(side_effect=RuntimeError("database is locked"))
    dashboard = AsyncMock()

    await ClaudeChatCog._finish_turn(
        cog, SimpleNamespace(id=7), dashboard=dashboard, description="d", notify_user_id=42
    )

    dashboard.set_state.assert_awaited_once()
