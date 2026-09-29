"""The chat cog follows Discord: a closed thread reopens on a message, and
nothing is ever posted into a thread Discord has put away.

The session store and the lifecycle service are real (SQLite on a temp path);
Discord is MagicMock threads and a fake surface that records archive calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import claude_discord.cogs.claude_chat as chat_mod
from claude_code_core.session_repo import SessionRepository
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.lifecycle_adapters import ChatTurnActivity
from claude_discord.session_lifecycle import CloseAuthorization, SessionLifecycleService
from claude_discord.voice_labels import label_key, title_tag

THREAD = 555
BOT_ID = 1
OWNER = 42


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


def _thread(*, archived: bool = False, locked: bool = False, owner: int = BOT_ID) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = THREAD
    thread.name = "Work"
    thread.parent_id = 200
    thread.owner_id = owner
    thread.archived = archived
    thread.locked = locked
    thread.auto_archive_duration = 10080
    thread.edit = AsyncMock()
    thread.send = AsyncMock(return_value=MagicMock(id=9001))
    thread.parent = MagicMock(spec=discord.TextChannel)
    thread.parent.create_thread = AsyncMock()
    return thread


@pytest.fixture
async def stores(tmp_path):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    repo = SessionRepository(db)
    await repo.save(THREAD, "sess-1", working_dir="/tmp/project", backend="claude")
    return repo, SettingsRepository(db)


@pytest.fixture
def cog(stores) -> ClaudeChatCog:
    repo, settings = stores
    bot = MagicMock()
    bot.channel_id = 999
    bot.user = SimpleNamespace(id=BOT_ID)
    bot.cogs = {}
    bot.get_cog = MagicMock(return_value=None)
    bot.capacity_repo = None
    bot.ask_repo = None
    bot.lounge_repo = None
    bot.resume_repo = None
    bot.handoff_repo = None
    bot.session_repo = None
    bot.get_channel = MagicMock(return_value=None)
    runner = MagicMock()
    chat = ClaudeChatCog(bot=bot, repo=repo, runner=runner, settings_repo=settings)
    chat.lifecycle = SessionLifecycleService(
        repo, surface=FakeSurface(), turns=ChatTurnActivity(chat)
    )
    chat._run_claude = AsyncMock()
    return chat


async def _close(cog: ClaudeChatCog) -> None:
    assert cog.lifecycle is not None
    await cog.lifecycle.close(THREAD, CloseAuthorization.from_discord())
    record = await cog.repo.get(THREAD)
    assert record is not None and record.is_closed


def _message(thread: object) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.id = 77
    message.channel = thread
    message.author = SimpleNamespace(id=OWNER, bot=False)
    message.type = discord.MessageType.default
    message.attachments = []
    message.content = "carry on"
    return message


class TestReopenFirst:
    async def test_typed_reply_in_closed_thread_reopens_before_running(self, cog, stores) -> None:
        _, settings = stores
        await _close(cog)
        calls: list[str] = []
        real_reopen = cog.lifecycle.reopen_from_discord

        async def reopen(thread_id: int) -> bool:
            calls.append("reopen")
            return await real_reopen(thread_id)

        cog.lifecycle.reopen_from_discord = reopen
        cog._run_claude.side_effect = lambda *a, **k: calls.append("run")
        cog._build_prompt_and_images = AsyncMock(return_value=("carry on", []))
        thread = _thread()

        await cog._handle_thread_reply(_message(thread))

        assert calls == ["reopen", "run"]
        assert cog._run_claude.await_args.kwargs["session_id"] == "sess-1"
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_open
        label = await settings.get(label_key(THREAD))
        assert label
        names = [c.kwargs["name"] for c in thread.edit.await_args_list if "name" in c.kwargs]
        assert any(title_tag(name) == label for name in names)

    async def test_different_backend_starts_fresh(self, cog) -> None:
        await _close(cog)
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="codex")
        cog._backend_settings = settings
        cog._build_prompt_and_images = AsyncMock(return_value=("carry on", []))

        await cog._handle_thread_reply(_message(_thread()))

        assert cog._run_claude.await_args.kwargs["session_id"] is None
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_open

    async def test_relayed_message_unarchives_before_first_post(self, cog) -> None:
        await _close(cog)
        thread = _thread(archived=True, locked=True)
        calls: list[str] = []
        thread.edit.side_effect = lambda **kw: calls.append(f"edit:{sorted(kw)}")
        thread.send.side_effect = lambda *a, **k: calls.append("send") or MagicMock(id=1)

        await cog.deliver_relayed_message(thread, "hello", interrupt=False)

        assert calls[0] == "edit:['archived', 'locked']"
        assert thread.edit.await_args_list[0].kwargs == {"archived": False, "locked": False}
        assert "send" in calls
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_open
        cog._run_claude.assert_awaited_once()


class TestPartialChannel:
    async def test_partial_messageable_is_fetched_and_routed_as_thread(self, cog) -> None:
        thread = _thread()
        partial = MagicMock(spec=discord.PartialMessageable)
        partial.id = THREAD
        cog.bot.fetch_channel = AsyncMock(return_value=thread)
        cog._try_send_drewai_lookup_handoff = AsyncMock(return_value=False)
        cog._is_no_mention_scope = lambda channel: True
        handle_reply = AsyncMock()
        cog._handle_thread_reply = handle_reply
        cog._handle_new_conversation = AsyncMock()
        message = _message(partial)

        await cog.on_message(message)

        cog.bot.fetch_channel.assert_awaited_once_with(THREAD)
        handle_reply.assert_awaited_once_with(message)
        assert message.channel is thread
        cog._handle_new_conversation.assert_not_awaited()
        thread.parent.create_thread.assert_not_awaited()


class TestMuteAndStop:
    async def test_stop_and_mute_an_active_run(self, cog) -> None:
        runner = MagicMock()
        runner.interrupt = AsyncMock()
        surface = MagicMock()
        cog._active_runners[THREAD] = runner
        cog._active_surfaces[THREAD] = surface

        assert await cog.mute_run(THREAD) is True
        assert await cog.stop_thread_run(THREAD) is True

        surface.mute.assert_called()
        runner.interrupt.assert_awaited_once()

    async def test_no_active_run_returns_false(self, cog) -> None:
        assert await cog.mute_run(THREAD) is False
        assert await cog.stop_thread_run(THREAD) is False

    async def test_run_registers_its_surface_and_a_muted_run_posts_no_ping(
        self, stores, monkeypatch
    ) -> None:
        from claude_discord.discord_ui.thread_dashboard import ThreadStatusDashboard

        repo, settings = stores
        bot = MagicMock()
        bot.capacity_repo = None
        chat = ClaudeChatCog(bot=bot, repo=repo, runner=MagicMock(), settings_repo=settings)
        channel = MagicMock(spec=discord.TextChannel)
        dashboard = ThreadStatusDashboard(channel, owner_id=OWNER, mention_user_ids={OWNER})
        chat._get_dashboard = lambda: dashboard
        chat._prepare_cross_backend_handoff = AsyncMock(return_value=(None, "work"))
        chat._get_current_model = AsyncMock(return_value=None)
        chat._get_allowed_tools = AsyncMock(return_value=None)
        chat._get_current_effort = AsyncMock(return_value=None)
        runner = MagicMock()
        runner.command = "claude"
        chat._build_runner_for_thread = AsyncMock(return_value=runner)
        seen: dict[str, object] = {}

        async def fake_run(config) -> None:
            seen["registered"] = chat._active_surfaces.get(THREAD) is config.surface
            seen["muted"] = await chat.mute_run(THREAD)

        monkeypatch.setattr(chat_mod, "run_claude_with_config", fake_run)
        status = MagicMock()
        status._stall_hard = 300
        status.set_queued = AsyncMock()
        monkeypatch.setattr(chat_mod, "StatusManager", lambda *a, **k: status)
        thread = _thread()
        message = _message(thread)

        await chat._run_claude(message, thread, "work", None, chat_only=True)

        assert seen == {"registered": True, "muted": True}
        assert THREAD not in chat._active_surfaces
        sent = [str(c.args[0]) for c in thread.send.await_args_list if c.args]
        assert not any("reply is needed" in text for text in sent)


def _resume_entry() -> SimpleNamespace:
    return SimpleNamespace(
        id=7, thread_id=THREAD, session_id="sess-1", reason="bot_shutdown", resume_prompt=None
    )


class TestRestartGuards:
    async def test_pending_resume_for_archived_thread_posts_nothing(self, cog) -> None:
        thread = _thread(archived=True)
        cog.bot.get_channel = MagicMock(return_value=thread)
        cog._resume_repo = MagicMock()
        cog._resume_repo.get_pending = AsyncMock(return_value=[_resume_entry()])
        cog._resume_repo.delete = AsyncMock()

        await cog.on_ready()

        thread.send.assert_not_awaited()
        cog._resume_repo.delete.assert_awaited_once_with(7)
        cog._run_claude.assert_not_awaited()
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_closed

    async def test_pending_resume_for_deleted_thread_closes_without_retry(self, cog) -> None:
        cog.bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )
        cog._resume_repo = MagicMock()
        cog._resume_repo.get_pending = AsyncMock(return_value=[_resume_entry()])
        cog._resume_repo.delete = AsyncMock()

        await cog.on_ready()

        cog.bot.fetch_channel.assert_awaited_once_with(THREAD)
        cog._resume_repo.delete.assert_awaited_once_with(7)
        cog._run_claude.assert_not_awaited()
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_closed

    async def test_capacity_resume_for_archived_thread_posts_nothing(self, cog) -> None:
        thread = _thread(archived=True)
        cog.bot.get_channel = MagicMock(return_value=thread)
        turn = SimpleNamespace(
            thread_id=THREAD, prompt_ref="work", session_id="sess-1", turn_key="k", claim_token="c"
        )

        await cog.resume_capacity_turn(turn)

        thread.send.assert_not_awaited()
        cog._run_claude.assert_not_awaited()
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_closed

    async def test_capacity_resume_for_deleted_thread_does_not_raise(self, cog) -> None:
        cog.bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )
        turn = SimpleNamespace(
            thread_id=THREAD, prompt_ref="work", session_id="sess-1", turn_key="k", claim_token="c"
        )

        await cog.resume_capacity_turn(turn)

        cog._run_claude.assert_not_awaited()
        record = await cog.repo.get(THREAD)
        assert record is not None and record.is_closed


class TestHandMadeThreadWindow:
    async def test_short_window_is_bumped_to_seven_days(self, cog) -> None:
        await cog.repo.delete(THREAD)
        thread = _thread(owner=OWNER)
        thread.auto_archive_duration = 1440

        await cog.on_thread_create(thread)

        assert any(
            c.kwargs == {"auto_archive_duration": 10080} for c in thread.edit.await_args_list
        )

    async def test_seven_day_window_is_left_alone(self, cog) -> None:
        await cog.repo.delete(THREAD)
        thread = _thread(owner=OWNER)
        thread.auto_archive_duration = 10080

        await cog.on_thread_create(thread)

        assert not any("auto_archive_duration" in c.kwargs for c in thread.edit.await_args_list)


class TestStartupSweep:
    async def test_sweep_runs_before_the_capacity_loader(self, cog, monkeypatch) -> None:
        calls: list[str] = []
        follow = SimpleNamespace(sweep=AsyncMock(side_effect=lambda: calls.append("sweep")))
        cog.bot.get_cog = MagicMock(
            side_effect=lambda name: follow if name == "ThreadFollowCog" else None
        )

        class FakeLoader:
            def __init__(self, repo, resume) -> None:
                pass

            async def load_due(self) -> list[str]:
                calls.append("capacity")
                return []

        monkeypatch.setattr(chat_mod, "CapacityRestartLoader", FakeLoader)
        cog._capacity_repo = MagicMock()

        await cog.on_ready()

        assert calls == ["sweep", "capacity"]

    async def test_failing_sweep_does_not_block_startup(self, cog, monkeypatch) -> None:
        calls: list[str] = []
        follow = SimpleNamespace(sweep=AsyncMock(side_effect=RuntimeError("boom")))
        cog.bot.get_cog = MagicMock(return_value=follow)

        class FakeLoader:
            def __init__(self, repo, resume) -> None:
                pass

            async def load_due(self) -> list[str]:
                calls.append("capacity")
                return []

        monkeypatch.setattr(chat_mod, "CapacityRestartLoader", FakeLoader)
        cog._capacity_repo = MagicMock()

        await cog.on_ready()

        assert calls == ["capacity"]


class TestDashboardKnowsSessions:
    """The reply-needed ping also stays out of a thread whose session is closing."""

    def test_dashboard_gets_the_session_store(self, cog) -> None:
        dashboard = MagicMock(spec=chat_mod.ThreadStatusDashboard)
        dashboard.session_repo = None
        cog._dashboard = None
        cog.bot.thread_dashboard = dashboard

        assert cog._get_dashboard() is dashboard
        assert dashboard.session_repo is cog.repo

    def test_an_explicit_dashboard_store_is_kept(self, cog) -> None:
        dashboard = MagicMock(spec=chat_mod.ThreadStatusDashboard)
        other = object()
        dashboard.session_repo = other
        cog._dashboard = None
        cog.bot.thread_dashboard = dashboard

        cog._get_dashboard()
        assert dashboard.session_repo is other
