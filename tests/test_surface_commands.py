"""The location-aware commands: thin adapters over the shared services.

A fake chat cog stands in for ClaudeChatCog with the 3.1 services mocked, so
each command is shown to call exactly one of them — or, in the wrong place,
none at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.surface_commands import SurfaceCommandsCog, locate
from claude_discord.command_surface import CommandSurface, SurfaceLocation
from claude_discord.discord_ui.session_actions import SessionActionsView

CONTROL = 100
SESSION_PARENT = 200


def thread_interaction(thread_id: int = 555, user: int = 42, parent: int = SESSION_PARENT):
    item = MagicMock(spec=discord.Interaction)
    item.user = SimpleNamespace(id=user)
    item.guild_id = 10
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.name = "Work"
    thread.parent_id = parent
    thread.parent = MagicMock(spec=discord.TextChannel)
    thread.mention = f"<#{thread_id}>"
    item.channel = thread
    item.channel_id = thread_id
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.defer = AsyncMock()
    item.response.is_done = MagicMock(return_value=False)
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    return item


def channel_interaction(channel_id: int = CONTROL, user: int = 42):
    item = MagicMock(spec=discord.Interaction)
    item.user = SimpleNamespace(id=user)
    item.guild_id = 10
    item.channel = MagicMock(spec=discord.TextChannel)
    item.channel.id = channel_id
    item.channel_id = channel_id
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.defer = AsyncMock()
    item.response.is_done = MagicMock(return_value=False)
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    return item


def record(thread_id: int = 555):
    return SimpleNamespace(
        thread_id=thread_id,
        session_id="abc-123",
        working_dir="/tmp/project",
        context_window=None,
        context_used=None,
        is_closed=False,
    )


@pytest.fixture
def cog():
    bot = MagicMock()
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=lambda tid: record(tid) if tid == 555 else None)
    chat = SimpleNamespace(
        _allowed_user_ids={42},
        _active_runners={},
        stop_turn=AsyncMock(return_value=True),
        fork_thread=AsyncMock(),
        rewind_view=MagicMock(return_value=None),
        compact_thread=AsyncMock(),
        clear_thread=AsyncMock(return_value=True),
        run_goal=AsyncMock(),
        goal_label=lambda condition: "◎ label",
    )
    return SurfaceCommandsCog(
        bot,
        surface=CommandSurface.for_control_centers(CONTROL),
        repo=repo,
        chat=chat,
    )


class TestLocate:
    async def test_session_thread_control_center_and_elsewhere(self, cog):
        assert await locate(cog.surface, cog.repo, thread_interaction()) is (
            SurfaceLocation.MANAGED_SESSION
        )
        assert await locate(cog.surface, cog.repo, thread_interaction(999)) is (
            SurfaceLocation.UNSUPPORTED
        )
        assert await locate(cog.surface, cog.repo, channel_interaction()) is (
            SurfaceLocation.CONTROL_CENTER
        )
        assert await locate(cog.surface, cog.repo, channel_interaction(300)) is (
            SurfaceLocation.UNSUPPORTED
        )


class TestSessionCommand:
    async def test_session_opens_the_action_view_in_a_managed_thread(self, cog):
        event = thread_interaction()
        await cog.session_command.callback(cog, event)
        kwargs = event.response.send_message.call_args.kwargs
        assert isinstance(kwargs["view"], SessionActionsView)
        assert kwargs["ephemeral"] is True

    async def test_session_in_the_control_center_changes_nothing(self, cog):
        event = channel_interaction()
        await cog.session_command.callback(cog, event)
        args, kwargs = event.response.send_message.call_args
        assert "inside a session thread" in args[0]
        assert "view" not in kwargs
        assert kwargs["ephemeral"] is True

    async def test_session_in_an_unbound_thread_is_refused(self, cog):
        event = thread_interaction(999)
        await cog.session_command.callback(cog, event)
        assert "inside a session thread" in event.response.send_message.call_args.args[0]

    async def test_unauthorized_user_is_refused_before_any_service(self, cog):
        event = thread_interaction(user=7)
        await cog.session_command.callback(cog, event)
        assert event.response.send_message.call_args.kwargs["ephemeral"] is True
        assert "view" not in event.response.send_message.call_args.kwargs


class TestSessionActionAdapter:
    async def test_fork_calls_the_service_once_and_links_the_new_thread(self, cog):
        new_thread = MagicMock(spec=discord.Thread)
        new_thread.mention = "<#777>"
        cog.chat.fork_thread.return_value = new_thread
        event = thread_interaction()
        await cog.actions.fork(event)
        cog.chat.fork_thread.assert_awaited_once()
        assert "<#777>" in event.followup.send.call_args.args[0]

    async def test_rewind_without_history_falls_back_to_the_shared_clear(self, cog):
        event = thread_interaction()
        await cog.actions.rewind(event)
        cog.chat.clear_thread.assert_awaited_once_with(555)

    async def test_rewind_with_history_shows_the_turn_picker(self, cog):
        picker = MagicMock(spec=discord.ui.View)
        cog.chat.rewind_view.return_value = picker
        event = thread_interaction()
        await cog.actions.rewind(event)
        assert event.response.send_message.call_args.kwargs["view"] is picker
        cog.chat.clear_thread.assert_not_awaited()

    async def test_compact_refuses_while_a_turn_runs(self, cog):
        cog.chat._active_runners[555] = object()
        event = thread_interaction()
        await cog.actions.compact(event)
        cog.chat.compact_thread.assert_not_awaited()
        assert "running" in event.response.send_message.call_args.args[0].lower()

    async def test_compact_seeds_and_calls_the_service(self, cog):
        event = thread_interaction()
        await cog.actions.compact(event)
        cog.chat.compact_thread.assert_awaited_once()

    async def test_clear_calls_the_service_and_reports(self, cog):
        event = thread_interaction()
        await cog.actions.clear(event)
        cog.chat.clear_thread.assert_awaited_once_with(555)
        assert "cleared" in event.response.send_message.call_args.args[0].lower()

    async def test_context_answers_without_changing_anything(self, cog):
        event = thread_interaction()
        await cog.actions.context(event)
        cog.chat.clear_thread.assert_not_awaited()
        assert event.response.send_message.call_args.kwargs["ephemeral"] is True

    async def test_goal_passes_the_condition_to_the_service(self, cog):
        event = thread_interaction()
        await cog.actions.goal(event, "all tests pass")
        args = cog.chat.run_goal.call_args.args
        assert args[2] == "all tests pass"

    async def test_actions_outside_a_managed_session_do_nothing(self, cog):
        event = channel_interaction()
        await cog.actions.clear(event)
        cog.chat.clear_thread.assert_not_awaited()
