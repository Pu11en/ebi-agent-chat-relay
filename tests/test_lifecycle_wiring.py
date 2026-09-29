"""`/close` and the Sessions buttons reach the one lifecycle service, on a real database.

The repository is real SQLite; the Discord side is a fake surface that records
archive/unarchive calls and a fake chat cog whose active-runner table stands
in for a running turn. That is enough to walk the spec's scenarios end to end:
idle close, close during a turn completed by run finalization, a duplicate
close, a restart with a pending close, and reopen.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.session_repo import SessionRepository
from claude_discord.cogs.surface_commands import SurfaceCommandsCog
from claude_discord.command_surface import CommandSurface
from claude_discord.database.models import init_db
from claude_discord.lifecycle_adapters import (
    ChatTurnActivity,
    DiscordThreadSurface,
    build_lifecycle_service,
)
from claude_discord.session_lifecycle import (
    CloseAuthorization,
    CloseState,
    SessionLifecycleService,
)

CONTROL = 100
THREAD = 555


class FakeSurface:
    def __init__(self) -> None:
        self.archived: list[int] = []
        self.unarchived: list[int] = []

    async def archive(self, thread_id: int) -> bool:
        self.archived.append(thread_id)
        return True

    async def unarchive(self, thread_id: int) -> bool:
        self.unarchived.append(thread_id)
        return True


def thread_interaction(thread_id: int = THREAD, user: int = 42):
    item = MagicMock(spec=discord.Interaction)
    item.user = SimpleNamespace(id=user)
    item.guild_id = 10
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.name = "Work"
    thread.parent_id = 200
    item.channel = thread
    item.channel_id = thread_id
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.defer = AsyncMock()
    item.response.is_done = MagicMock(return_value=False)
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    return item


def channel_interaction(channel_id: int = CONTROL):
    item = MagicMock(spec=discord.Interaction)
    item.user = SimpleNamespace(id=42)
    item.guild_id = 10
    item.channel = MagicMock(spec=discord.TextChannel)
    item.channel.id = channel_id
    item.channel_id = channel_id
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.is_done = MagicMock(return_value=False)
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    return item


@pytest.fixture
async def repo(tmp_path):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    repo = SessionRepository(db)
    await repo.save(THREAD, "sess-1", working_dir="/tmp/project")
    return repo


@pytest.fixture
def chat():
    return SimpleNamespace(_allowed_user_ids={42}, _active_runners={}, _active_tasks={})


@pytest.fixture
def surface():
    return FakeSurface()


@pytest.fixture
def lifecycle(repo, chat, surface):
    return SessionLifecycleService(repo, surface=surface, turns=ChatTurnActivity(chat))


@pytest.fixture
def cog(repo, chat, lifecycle):
    return SurfaceCommandsCog(
        MagicMock(),
        surface=CommandSurface.for_control_centers(CONTROL),
        repo=repo,
        chat=chat,
        lifecycle=lifecycle,
    )


class TestAdapters:
    async def test_turn_activity_reads_the_chat_cog_and_waits_for_its_task(self, chat):
        activity = ChatTurnActivity(chat)
        assert await activity.is_active(THREAD) is False
        chat._active_runners[THREAD] = object()
        assert await activity.is_active(THREAD) is True
        done = asyncio.Event()

        async def run():
            await done.wait()

        chat._active_tasks[THREAD] = asyncio.create_task(run())
        waiter = asyncio.create_task(activity.wait_until_idle(THREAD))
        await asyncio.sleep(0)
        assert not waiter.done()
        done.set()
        await asyncio.wait_for(waiter, 1)

    async def test_thread_surface_archives_without_locking_and_unarchives(self):
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.edit = AsyncMock()
        thread.send = AsyncMock()
        bot.fetch_channel = AsyncMock(return_value=thread)
        surface = DiscordThreadSurface(bot)
        assert await surface.archive(THREAD) is True
        # Locked as well as archived: an unlocked archived thread re-opens the
        # moment anyone posts in it, so "closed" did not stay closed.
        thread.edit.assert_awaited_once_with(archived=True, locked=True)
        thread.send.assert_not_awaited()  # no repository, no note
        assert await surface.unarchive(THREAD) is True
        # Reopening undoes both, so nobody is left locked out of their own thread.
        assert thread.edit.call_args.kwargs == {"archived": False, "locked": False}

    async def test_thread_surface_posts_the_wrap_up_before_archiving(self, repo):
        await repo.request_close(THREAD, "direct_interaction")
        await repo.mark_closed(THREAD, "Shipped the fix.")
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        order: list[str] = []
        thread.send = AsyncMock(side_effect=lambda *a, **k: order.append("send"))
        thread.edit = AsyncMock(side_effect=lambda **k: order.append("edit"))
        bot.fetch_channel = AsyncMock(return_value=thread)
        assert await DiscordThreadSurface(bot, repo).archive(THREAD) is True
        assert order == ["send", "edit"]
        assert "Shipped the fix." in thread.send.call_args.args[0]

    async def test_workflow_close_archives_worker_without_lock_or_note(self, repo, chat):
        """Retried by any process, a finished worker is never locked (task 3.1)."""
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "worker"
        thread.send, thread.edit = AsyncMock(), AsyncMock()
        bot.get_channel.return_value = thread
        chat._active_runners[THREAD] = object()  # the worker's turn is still running
        lifecycle = build_lifecycle_service(bot, chat, repo)
        authorization = CloseAuthorization.from_workflow("gowork", close_on_done=True)
        assert (await lifecycle.close(THREAD, authorization)).is_pending
        chat._active_runners.clear()
        # Run finalization in the chat cog completes it with the same shared service.
        assert (await lifecycle.complete_pending_close(THREAD)).archived
        thread.edit.assert_awaited_once_with(archived=True)
        thread.send.assert_not_awaited()

    async def test_thread_surface_counts_a_gone_thread_as_archived(self):
        """A deleted thread has nothing left to archive, so the close is acknowledged.

        Reporting False kept the archive pending for ever, retried on every
        restart against a thread that no longer existed. A refusal is different:
        the thread may well still be there, so that stays pending.
        """
        bot = MagicMock()
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
        assert await DiscordThreadSurface(bot).archive(THREAD) is True
        bot.fetch_channel = AsyncMock(return_value=MagicMock(spec=discord.TextChannel))
        assert await DiscordThreadSurface(bot).archive(THREAD) is True
        bot.fetch_channel = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
        assert await DiscordThreadSurface(bot).archive(THREAD) is False
        bot.fetch_channel = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "x"))
        assert await DiscordThreadSurface(bot).archive(THREAD) is False

    async def test_thread_surface_cannot_unarchive_a_thread_it_cannot_reach(self):
        bot = MagicMock()
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
        assert await DiscordThreadSurface(bot).unarchive(THREAD) is False
        bot.fetch_channel = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
        assert await DiscordThreadSurface(bot).unarchive(THREAD) is False

    async def test_a_discord_archived_close_makes_no_discord_call(self, repo, chat):
        """Discord archived the thread itself: the close only records that.

        A post would un-archive the thread Discord just put away, a rename is
        refused on an archived thread, and a lock would stop the person typing
        their way back in. So nothing is sent, edited or even fetched.
        """
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "[bravo] 📂 project"
        thread.send, thread.edit = AsyncMock(), AsyncMock()
        bot.get_channel.return_value = thread
        bot.fetch_channel = AsyncMock(return_value=thread)
        lifecycle = build_lifecycle_service(bot, chat, repo)

        outcome = await lifecycle.close(THREAD, CloseAuthorization.from_discord())

        assert outcome.state is CloseState.CLOSED
        assert outcome.archived is True
        record = await repo.get(THREAD)
        assert record is not None and record.is_closed
        assert record.close_authority == "discord_archived"
        assert not record.archive_pending
        assert record.wrap_up
        thread.send.assert_not_awaited()
        thread.edit.assert_not_awaited()
        bot.fetch_channel.assert_not_awaited()
        bot.get_channel.assert_not_called()

    async def test_a_discord_archived_close_during_a_turn_completes_with_no_discord_call(
        self, repo, chat
    ):
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "[bravo] 📂 project"
        thread.send, thread.edit = AsyncMock(), AsyncMock()
        bot.get_channel.return_value = thread
        bot.fetch_channel = AsyncMock(return_value=thread)
        chat._active_runners[THREAD] = object()
        lifecycle = build_lifecycle_service(bot, chat, repo)

        assert (await lifecycle.close(THREAD, CloseAuthorization.from_discord())).is_pending
        record = await repo.get(THREAD)
        assert record is not None and record.close_pending
        assert record.close_authority == "discord_archived"

        chat._active_runners.clear()
        # Run finalization in the chat cog completes it with the same shared service.
        outcome = await lifecycle.complete_pending_close(THREAD)

        assert outcome.state is CloseState.CLOSED and outcome.archived
        record = await repo.get(THREAD)
        assert record is not None and record.is_closed and not record.archive_pending
        thread.send.assert_not_awaited()
        thread.edit.assert_not_awaited()
        bot.fetch_channel.assert_not_awaited()

    @pytest.mark.parametrize("authority", ["direct_interaction", "workflow_close_on_done"])
    async def test_a_close_posts_nothing_into_a_thread_discord_already_archived(
        self, repo, chat, authority
    ):
        """Whoever closed the session, a thread already out of sight is left there.

        Discord un-archives a thread the moment anything is posted in it, so the
        closing note — retried by a restart's reconcile, or sent when a thread
        archived by hand is closed from the Sessions browser — would bring back
        the conversation it closes. Discord refuses a rename or a lock on an
        archived thread anyway, so the close is acknowledged with no edit at all.
        """
        await repo.request_close(THREAD, authority)
        await repo.mark_closed(THREAD, "Shipped the fix.", archive_pending=True)
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "[bravo] 📂 project"
        thread.archived = True
        thread.send, thread.edit = AsyncMock(), AsyncMock()
        bot.get_channel.return_value = None  # discord.py drops archived threads from its cache
        bot.fetch_channel = AsyncMock(return_value=thread)
        lifecycle = build_lifecycle_service(bot, chat, repo)

        outcomes = await lifecycle.reconcile_pending_closes()

        assert [outcome.archived for outcome in outcomes] == [True]
        record = await repo.get(THREAD)
        assert record is not None and record.is_closed and not record.archive_pending
        assert record.close_authority == authority
        thread.send.assert_not_awaited()
        thread.edit.assert_not_awaited()

    def test_build_lifecycle_service_wires_both_adapters(self, repo, chat):
        service = build_lifecycle_service(MagicMock(), chat, repo)
        assert isinstance(service.turns, ChatTurnActivity)
        assert isinstance(service.surface, DiscordThreadSurface)


class TestCloseCommand:
    async def test_idle_close_wraps_up_marks_closed_and_archives(self, cog, repo, surface):
        event = thread_interaction()
        await cog.close_command.callback(cog, event)
        record = await repo.get(THREAD)
        assert record is not None and record.is_closed
        assert record.wrap_up
        assert record.working_dir == "/tmp/project"
        assert record.session_id == "sess-1"
        assert surface.archived == [THREAD]
        assert "closed" in event.followup.send.call_args.args[0].lower()

    async def test_close_during_a_turn_is_pending_and_finishes_when_the_run_ends(
        self, cog, repo, chat, surface, lifecycle
    ):
        chat._active_runners[THREAD] = object()
        event = thread_interaction()
        await cog.close_command.callback(cog, event)
        record = await repo.get(THREAD)
        assert record is not None and record.close_pending
        assert surface.archived == []
        assert "still running" in event.followup.send.call_args.args[0]
        # The turn ends: run finalization completes the close.
        chat._active_runners.clear()
        outcome = await lifecycle.complete_pending_close(THREAD)
        assert outcome.state is CloseState.CLOSED
        assert surface.archived == [THREAD]

    async def test_duplicate_close_reports_without_rewriting(self, cog, repo, surface):
        await cog.close_command.callback(cog, thread_interaction())
        first = await repo.get(THREAD)
        second_event = thread_interaction()
        await cog.close_command.callback(cog, second_event)
        second = await repo.get(THREAD)
        assert first is not None and second is not None
        assert second.wrap_up == first.wrap_up and second.closed_at == first.closed_at
        assert surface.archived == [THREAD]
        assert "already closed" in second_event.followup.send.call_args.args[0]

    async def test_close_outside_a_session_changes_nothing(self, cog, repo, surface):
        event = channel_interaction()
        await cog.close_command.callback(cog, event)
        record = await repo.get(THREAD)
        assert record is not None and record.is_open
        assert surface.archived == []
        assert "inside a session thread" in event.response.send_message.call_args.args[0]

    async def test_close_without_a_lifecycle_service_declines_and_never_deletes(self, repo, chat):
        cog = SurfaceCommandsCog(
            MagicMock(), surface=CommandSurface.for_control_centers(CONTROL), repo=repo, chat=chat
        )
        event = thread_interaction()
        await cog.close_command.callback(cog, event)
        assert (await repo.get(THREAD)) is not None
        assert "not available" in event.response.send_message.call_args.args[0]


class TestRestartAndReopen:
    async def test_restart_reconciles_a_pending_close(self, repo, chat, surface):
        chat._active_runners[THREAD] = object()
        before = SessionLifecycleService(repo, surface=surface, turns=ChatTurnActivity(chat))
        from claude_discord.session_lifecycle import CloseAuthorization

        outcome = await before.close(THREAD, CloseAuthorization.from_interaction(42))
        assert outcome.is_pending
        # "Restart": a fresh service over the same database, no turn running.
        fresh_chat = SimpleNamespace(_active_runners={}, _active_tasks={})
        after = SessionLifecycleService(repo, surface=surface, turns=ChatTurnActivity(fresh_chat))
        results = await after.reconcile_pending_closes()
        assert [r.state for r in results] == [CloseState.CLOSED]
        assert surface.archived == [THREAD]

    async def test_reopen_from_sessions_unarchives_and_continues(self, cog, repo, surface):
        await cog.close_command.callback(cog, thread_interaction())
        outcome = await cog.lifecycle.reopen(THREAD, backend="claude")
        assert outcome.is_reopened
        assert surface.unarchived == [THREAD]
        record = await repo.get(THREAD)
        assert record is not None and record.is_open
        assert outcome.resume_session_id == "sess-1"

    async def test_reopen_under_a_different_harness_does_not_hand_over_the_id(self, cog, repo):
        await repo.save(THREAD, "sess-1", working_dir="/tmp/project", backend="claude")
        await cog.close_command.callback(cog, thread_interaction())
        outcome = await cog.lifecycle.reopen(THREAD, backend="codex")
        assert outcome.is_reopened
        assert outcome.requires_fresh_session is True
        assert outcome.resume_session_id is None

    async def test_typing_in_a_closed_thread_reopens_it_through_the_discord_surface(
        self, repo, chat
    ):
        """A message in an archived thread brings the session back, unarchived and unlocked."""
        await repo.save(THREAD, "sess-1", working_dir="/tmp/project", backend="claude")
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "📂 project"
        thread.send, thread.edit = AsyncMock(), AsyncMock()
        bot.get_channel.return_value = thread
        lifecycle = build_lifecycle_service(bot, chat, repo)
        assert (await lifecycle.close(THREAD, CloseAuthorization.from_discord())).is_closed
        thread.edit.assert_not_awaited()

        assert await lifecycle.reopen_from_discord(THREAD) is True

        record = await repo.get(THREAD)
        assert record is not None and record.is_open
        assert (record.session_id, record.backend) == ("sess-1", "claude")
        thread.edit.assert_awaited_once_with(archived=False, locked=False)
        assert await lifecycle.reopen_from_discord(THREAD) is False


class TestTheTagLeavesWithTheSession:
    """A closed thread must not keep showing a spoken tag.

    It did, and the result was two threads in the sidebar both titled
    "[bravo]" — one of them finished. The tag data is corrected when a session
    closes, but the *title* is a separate copy, and Discord will not rename an
    archived thread. So the title is cleaned while the thread is still editable:
    in the same breath as archiving it, before the archive lands.
    """

    async def test_the_tag_is_stripped_from_the_title_before_archiving(self):
        order: list[str] = []
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "[bravo] 📂 ebi-agent-chat-relay"

        async def edit(**kwargs):
            order.append("rename" if "name" in kwargs else "archive")

        thread.edit = AsyncMock(side_effect=edit)
        thread.send = AsyncMock()
        bot.fetch_channel = AsyncMock(return_value=thread)

        assert await DiscordThreadSurface(bot).archive(THREAD) is True

        assert order == ["rename", "archive"], "renaming an archived thread is refused"
        renamed = [c for c in thread.edit.await_args_list if "name" in c.kwargs]
        assert renamed[0].kwargs["name"] == "📂 ebi-agent-chat-relay"

    async def test_a_title_with_no_tag_is_not_renamed(self):
        """One needless rename per close would spend a tight Discord budget."""
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "📂 ebi-agent-chat-relay"
        thread.edit = AsyncMock()
        thread.send = AsyncMock()
        bot.fetch_channel = AsyncMock(return_value=thread)

        await DiscordThreadSurface(bot).archive(THREAD)

        assert [c for c in thread.edit.await_args_list if "name" in c.kwargs] == []

    async def test_a_failed_rename_still_archives(self):
        """Closing matters more than the title being tidy."""
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        thread.name = "[bravo] repo"
        calls: list[dict] = []

        async def edit(**kwargs):
            calls.append(kwargs)
            if "name" in kwargs:
                raise discord.HTTPException(MagicMock(status=429), "rate limited")

        thread.edit = AsyncMock(side_effect=edit)
        thread.send = AsyncMock()
        bot.fetch_channel = AsyncMock(return_value=thread)

        assert await DiscordThreadSurface(bot).archive(THREAD) is True
        assert {"archived": True, "locked": True} in calls
