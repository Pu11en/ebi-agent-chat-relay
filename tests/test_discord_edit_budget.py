"""One edit budget per thread, because that is how Discord meters edits.

Discord allows roughly five message edits per five seconds **per channel**, and a
thread is a channel. The Discord frontend paced each message on its own clock —
the streaming answer every 1.5s, every running tool's elapsed counter every 5s —
so a thread with a dozen live counters ran about 3.7x over. The log held 6,012
rejections, up to 120 in a single minute, each costing ~0.9s of backoff, which is
why long answers crawled.
"""

from __future__ import annotations

import asyncio

import pytest

from claude_discord.discord_ui.edit_budget import (
    DISCORD_MIN_EDIT_INTERVAL,
    budget_for,
    forget_budget,
)


@pytest.fixture(autouse=True)
async def _clean():
    yield
    await forget_budget(1)
    await forget_budget(2)


class TestOneBudgetPerThread:
    def test_the_same_thread_shares_one_budget(self) -> None:
        """The surface is built several times for one thread; a per-surface
        budget would give each copy its own and defeat the whole point."""
        assert budget_for(1) is budget_for(1)

    def test_different_threads_do_not_share(self) -> None:
        """Discord's bucket is per channel, so one thread must not throttle another."""
        assert budget_for(1) is not budget_for(2)

    def test_the_interval_stays_under_discords_limit(self) -> None:
        """5 edits / 5s means 1.0s each; the interval must leave headroom."""
        assert DISCORD_MIN_EDIT_INTERVAL > 1.0

    async def test_a_thread_is_forgotten_so_budgets_do_not_accumulate(self) -> None:
        first = budget_for(1)
        await forget_budget(1)
        assert budget_for(1) is not first


class TestSpendingIsShared:
    async def test_two_messages_in_one_thread_take_turns(self) -> None:
        sent: list[str] = []
        clock = {"t": 0.0}
        from claude_code_core.pacer import UpdatePacer

        async def no_sleep(_seconds: float) -> None:
            clock["t"] += DISCORD_MIN_EDIT_INTERVAL

        pacer = UpdatePacer(DISCORD_MIN_EDIT_INTERVAL, now=lambda: clock["t"], sleep=no_sleep)

        async def edit(which: str) -> None:
            sent.append(which)

        # The answer text and a tool counter both want a slot at the same moment.
        await pacer.submit(lambda: edit("text"), key="text")
        await pacer.submit(lambda: edit("timer"), key="timer")
        await pacer.drain()

        assert sent == ["text", "timer"], "one went immediately, one waited its turn"

    async def test_the_newest_state_of_one_message_replaces_the_older(self) -> None:
        """Spending a slot on a state nobody will ever see is the waste."""
        sent: list[str] = []
        clock = {"t": 0.0}
        from claude_code_core.pacer import UpdatePacer

        async def no_sleep(_seconds: float) -> None:
            clock["t"] += DISCORD_MIN_EDIT_INTERVAL

        pacer = UpdatePacer(DISCORD_MIN_EDIT_INTERVAL, now=lambda: clock["t"], sleep=no_sleep)

        async def edit(text: str) -> None:
            sent.append(text)

        await pacer.submit(lambda: edit("first"), key="text")
        await pacer.submit(lambda: edit("5s"), key="timer")
        await pacer.submit(lambda: edit("10s"), key="timer")
        await pacer.drain()

        assert sent == ["first", "10s"], "the 5s tick was never worth a slot"


class TestTheCounterOnlyAppearsWhenItMatters:
    """A counter is only information when something is slow.

    Every running tool ticking every five seconds was most of the load, and for a
    command that finishes in under a second the number says nothing at all.
    """

    async def test_a_quick_tool_never_spends_an_edit(self) -> None:
        from claude_discord.discord_ui.edit_budget import COUNTER_AFTER_SECONDS

        assert COUNTER_AFTER_SECONDS >= 10

    async def test_the_delay_is_long_enough_to_skip_ordinary_commands(self) -> None:
        """Measured: a typical tool call in this project finishes well under 10s."""
        from claude_discord.discord_ui.edit_budget import COUNTER_AFTER_SECONDS

        assert COUNTER_AFTER_SECONDS <= 15, "too long and a stuck build looks frozen"


async def test_forget_budget_is_safe_for_a_thread_never_seen() -> None:
    await forget_budget(99999)


class TestTheCounterInPractice:
    """The counter is the load, so prove a quick tool costs nothing."""

    @staticmethod
    def _activity(spec_kind: str = "tool"):
        from unittest.mock import AsyncMock, MagicMock

        from claude_code_core.frontend import ActivitySpec
        from claude_discord.surface import DiscordActivity

        message = MagicMock()
        message.id = 7
        message.channel.id = 1
        message.edit = AsyncMock()
        spec = ActivitySpec(kind=spec_kind, title="Bash", detail="ls")
        return DiscordActivity(message, spec), message

    async def test_a_tool_that_finishes_fast_never_edits_for_the_counter(self) -> None:
        activity, message = self._activity()
        # Finishes immediately, as most tool calls do.
        await activity.complete("done")
        await asyncio.sleep(0)

        # The only edit is the completion itself, never an elapsed tick.
        assert message.edit.await_count == 1
        for call in message.edit.await_args_list:
            assert "elapsed" not in str(call)

    async def test_a_cancelled_activity_leaves_no_timer_running(self) -> None:
        activity, _message = self._activity()
        await activity.cancel()
        await asyncio.sleep(0)
        timer = activity._timer
        assert timer is None or timer.cancelled() or timer.done()
