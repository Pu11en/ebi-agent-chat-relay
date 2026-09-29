"""A run whose thread was archived or deleted mid-turn goes silent.

Discord un-archives a thread the moment anything is posted in it, so a run that
keeps streaming after the person put the thread away would drag it straight
back into the sidebar. The chat cog stops the run separately; these tests pin
the other half — from the moment ``DiscordSurface.mute()`` is called nothing
the run still produces (stream text, tool embeds, the Stop button bump, the
stall notice, the reply-needed ping) reaches Discord, and nothing is raised.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from claude_code_core.frontend import ActivitySpec, Notice, NoticeLevel, StatusKind
from claude_discord.discord_ui.status import StatusManager
from claude_discord.discord_ui.streaming_manager import StreamingMessageManager
from claude_discord.discord_ui.thread_dashboard import ThreadState, ThreadStatusDashboard
from claude_discord.discord_ui.views import StopView
from claude_discord.surface import DiscordSurface


class _Recorder:
    """Counts every call that would reach Discord, sends and edits alike."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def message(self) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.id = 900 + len(self.calls)
        msg.guild = None
        msg.channel = MagicMock()
        msg.channel.id = 4242

        async def _edit(*_a: Any, **_k: Any) -> None:
            self.calls.append("edit")

        async def _delete(*_a: Any, **_k: Any) -> None:
            self.calls.append("delete")

        msg.edit = AsyncMock(side_effect=_edit)
        msg.delete = AsyncMock(side_effect=_delete)
        msg.add_reaction = AsyncMock(side_effect=lambda *_a: self.calls.append("react"))
        msg.remove_reaction = AsyncMock(side_effect=lambda *_a: self.calls.append("unreact"))
        return msg

    def thread(self) -> MagicMock:
        thread = MagicMock(spec=discord.Thread)
        thread.id = 4242
        thread.archived = False
        thread.locked = False

        async def _send(*_a: Any, **_k: Any) -> MagicMock:
            self.calls.append("send")
            return self.message()

        thread.send = AsyncMock(side_effect=_send)
        thread.edit = AsyncMock(side_effect=lambda **_k: self.calls.append("thread-edit"))
        return thread


async def _fake_run(gate: asyncio.Event) -> AsyncIterator[str]:
    """A backend that produces one event, then waits, then keeps producing."""
    yield "first words "
    await gate.wait()
    for word in ("more ", "text ", "after ", "the ", "archive"):
        yield word


async def _drive(surface: DiscordSurface, gate: asyncio.Event, muted: asyncio.Event) -> str:
    """What the event processor does with a run, reduced to surface calls."""
    stream = surface.open_stream()
    interrupt = await surface.offer_interrupt(AsyncMock())
    started = False
    async for delta in _fake_run(gate):
        await stream.append(delta)
        if not started:
            started = True
            muted.set()
            continue
        activity = await surface.open_activity(ActivitySpec(title="Bash", kind="tool"))
        await activity.update("running")
        await activity.complete("ok")
        await interrupt.bump()
        await surface.send_notice(Notice(title="note", level=NoticeLevel.INFO))
        await surface.send_text("a reply")
        await surface.set_status(StatusKind.THINKING)
    result = await stream.finalize()
    await interrupt.disable()
    await surface.clear_status()
    await surface.rename("new title")
    return result


class TestMuteMidRun:
    async def test_nothing_reaches_discord_after_mute_while_run_completes(self) -> None:
        rec = _Recorder()
        thread = rec.thread()
        view = StopView(MagicMock())
        surface = DiscordSurface(thread, interrupt_view=view)
        gate = asyncio.Event()
        muted = asyncio.Event()

        task = asyncio.create_task(_drive(surface, gate, muted))
        await muted.wait()
        before = len(rec.calls)
        assert before >= 1  # the first words were streamed before the archive

        surface.mute()
        gate.set()
        result = await asyncio.wait_for(task, timeout=5)
        await asyncio.sleep(0.05)  # let any stray debounced task fire

        assert rec.calls[before:] == []
        assert "archive" in result  # the run itself still finished

    async def test_mute_twice_is_harmless(self) -> None:
        rec = _Recorder()
        surface = DiscordSurface(rec.thread())
        surface.mute()
        surface.mute()
        assert surface.muted is True
        assert await surface.send_text("hi") is None
        assert rec.calls == []

    async def test_stop_view_bump_and_disable_are_silent_once_muted(self) -> None:
        rec = _Recorder()
        thread = rec.thread()
        view = StopView(MagicMock())
        await view.bump(thread)
        before = len(rec.calls)
        view.mute()
        await view.bump(thread)
        await view.set_label("waiting")
        await view.disable()
        assert rec.calls[before:] == []

    async def test_streaming_manager_drops_queued_edit_after_mute(self) -> None:
        rec = _Recorder()
        mgr = StreamingMessageManager(rec.thread())
        await mgr.append("hello")
        before = len(rec.calls)
        mgr.mute()
        await mgr.append(" world" + "x" * 5000)
        text = await mgr.finalize()
        assert rec.calls[before:] == []
        assert text  # the text is still returned to the caller

    async def test_mute_stops_the_stall_notice(self) -> None:
        rec = _Recorder()
        stalled = AsyncMock()
        status = StatusManager(rec.message(), on_hard_stall=stalled)
        status._stall_hard = 0  # fire on the first check
        status._stall_soft = 0
        surface = DiscordSurface(rec.thread(), status_manager=status)
        await surface.set_status(StatusKind.THINKING)
        surface.mute()
        await asyncio.sleep(2.2)  # the stall monitor polls every two seconds
        stalled.assert_not_awaited()
        await surface.set_status(StatusKind.THINKING)
        await asyncio.sleep(0.05)
        assert "react" not in rec.calls


class TestReplyNeededPing:
    def _dashboard(self) -> ThreadStatusDashboard:
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        return ThreadStatusDashboard(channel=channel, owner_id=7)

    async def test_ping_skipped_for_a_closing_row(self) -> None:
        rec = _Recorder()
        thread = rec.thread()
        repo = MagicMock()
        repo.get = AsyncMock(return_value=MagicMock(lifecycle_state="closing"))
        dashboard = self._dashboard()
        dashboard.session_repo = repo
        await dashboard.set_state(thread.id, ThreadState.WAITING_INPUT, "d", thread=thread)
        assert rec.calls == []

    async def test_ping_skipped_for_an_archived_thread(self) -> None:
        rec = _Recorder()
        thread = rec.thread()
        thread.archived = True
        dashboard = self._dashboard()
        await dashboard.set_state(thread.id, ThreadState.WAITING_INPUT, "d", thread=thread)
        assert rec.calls == []

    async def test_ping_still_sent_for_an_open_row(self) -> None:
        rec = _Recorder()
        thread = rec.thread()
        repo = MagicMock()
        repo.get = AsyncMock(return_value=MagicMock(lifecycle_state="open"))
        dashboard = self._dashboard()
        dashboard.session_repo = repo
        await dashboard.set_state(thread.id, ThreadState.WAITING_INPUT, "d", thread=thread)
        assert rec.calls == ["send"]
