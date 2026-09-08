"""Admission and cancellation regressions without real model calls."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs import _run_helper as helper
from claude_discord.cogs.run_config import RunConfig
from claude_discord.concurrency import SessionRegistry
from claude_discord.discord_ui.status import StatusManager
from claude_discord.discord_ui.views import StopView
from claude_discord.session_view import build_session_views


@pytest.mark.real_system_context
async def test_queued_turn_cancel_removes_registry_without_releasing_occupied_slot() -> None:
    registry = SessionRegistry()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 321
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    thread.send = AsyncMock(return_value=message)
    runner = MagicMock()
    runner.working_dir = None
    runner.clone.return_value = runner
    view = StopView(runner)
    view.set_message(message)
    worktrees = MagicMock()
    sem = asyncio.Semaphore(0)
    with patch.object(helper, "_global_semaphore", sem):
        task = asyncio.create_task(
            helper.run_claude_with_config(
                RunConfig(
                    thread=thread,
                    runner=runner,
                    prompt="queued",
                    registry=registry,
                    stop_view=view,
                    worktree_manager=worktrees,
                )
            )
        )
        try:
            await asyncio.sleep(0.05)
            views = build_session_views(
                records=[],
                active=registry.list_active(),
                running_thread_ids={321},
                lounge_messages=[],
            )
            assert views[0]["state"] == "queued"
            interaction = MagicMock()
            interaction.response.edit_message = AsyncMock()
            interaction.followup.send = AsyncMock()
            await view.stop_button.callback(interaction)
            with pytest.raises(asyncio.CancelledError):
                await task
            assert registry.list_active() == []
            assert sem.locked(), "Cancellation must not release somebody else's slot"
            runner.run.assert_not_called()
            assert worktrees.mock_calls == []
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


async def test_finished_stop_card_does_not_keep_saying_running() -> None:
    view = StopView(MagicMock())
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.set_message(message)
    await view.disable()
    assert "finished" in message.edit.call_args.kwargs["content"].lower()


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_capacity_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        helper.configure_session_limit(value)


async def test_queued_status_has_no_stall_timer() -> None:
    message = MagicMock(spec=discord.Message)
    message.add_reaction = AsyncMock()
    message.remove_reaction = AsyncMock()
    status = StatusManager(message)
    try:
        await status.set_thinking()
        await status.set_queued()
        assert status._stall_task is None or status._stall_task.cancelling()
        await status.set_thinking()
        assert status._stall_task is not None
        assert not status._stall_task.cancelling()
    finally:
        await status.cleanup()


async def test_ten_turns_start_and_eleventh_starts_when_one_finishes() -> None:
    """Shared capacity is usable by any ten threads without a fixed role split."""
    gates = [asyncio.Event() for _ in range(11)]
    started: list[int] = []
    tasks = []

    async def events(index: int):
        started.append(index)
        await gates[index].wait()
        if False:
            yield

    with patch.object(helper, "_global_semaphore", asyncio.Semaphore(10)):
        try:
            for index in range(11):
                thread = MagicMock(spec=discord.Thread)
                thread.id = index + 100
                thread.send = AsyncMock()
                runner = MagicMock()
                runner.run = lambda *a, i=index, **k: events(i)
                tasks.append(
                    asyncio.create_task(
                        helper.run_claude_with_config(
                            RunConfig(thread=thread, runner=runner, prompt="capacity test")
                        )
                    )
                )
            await asyncio.sleep(0.05)
            assert started == list(range(10))
            gates[3].set()
            await asyncio.sleep(0.05)
            assert started == list(range(11))
        finally:
            for gate in gates:
                gate.set()
            await asyncio.gather(*tasks)
