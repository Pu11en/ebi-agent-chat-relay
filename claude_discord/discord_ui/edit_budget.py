"""One edit budget per thread, because that is how Discord meters edits.

Discord allows roughly five message edits per five seconds **per channel**, and a
thread is a channel. Every live display in a thread — the answer text growing,
each running tool's elapsed counter — edits its *own* message, and each one used
to pace itself on its own clock: 1.5s for the stream, 5s per tool timer. Nothing
knew about the others, so a thread running a dozen tools went roughly 3.7x over
the limit. The log held 6,012 rejections, up to 120 in a single minute, and every
rejection costs about 0.9s of backoff — which serialises the whole thread and is
why long answers crawled.

So the budget lives here, keyed by thread, and the displays submit to it instead
of deciding for themselves. The pacer itself is
:class:`claude_code_core.pacer.UpdatePacer`, shared with the Teams frontend,
which has exactly the same problem measured in a different unit.

The registry is module-level rather than per-surface on purpose: ``DiscordSurface``
is constructed in several places for the same thread (``RunConfig``,
``DiscordFrontend``, the context nudge), and a budget per surface would hand each
copy its own allowance — which is the bug, not the fix.
"""

from __future__ import annotations

from claude_code_core.pacer import UpdatePacer

__all__ = [
    "COUNTER_AFTER_SECONDS",
    "DISCORD_MIN_EDIT_INTERVAL",
    "budget_for",
    "forget_budget",
]

#: Seconds between edits in one thread, across everything that edits.
#:
#: Discord's limit works out to about one edit per second; this leaves headroom
#: rather than sitting on the line, because being rejected costs far more than
#: waiting — a 429 is a wasted request *and* a ~0.9s penalty, so pacing slightly
#: slow is strictly faster than pacing slightly fast.
DISCORD_MIN_EDIT_INTERVAL = 1.2

#: How long a tool must run before its elapsed counter starts.
#:
#: The counter is only information when something is slow: for a command that
#: finishes in under a second the number says nothing, and those are the common
#: case, so ticking them was most of the load for none of the value. Long enough
#: to skip ordinary calls, short enough that a stuck build does not look frozen.
COUNTER_AFTER_SECONDS = 10

_budgets: dict[int, UpdatePacer] = {}


def budget_for(thread_id: int) -> UpdatePacer:
    """The edit budget for ``thread_id``, creating it on first use."""
    pacer = _budgets.get(thread_id)
    if pacer is None:
        pacer = UpdatePacer(DISCORD_MIN_EDIT_INTERVAL)
        _budgets[thread_id] = pacer
    return pacer


async def forget_budget(thread_id: int) -> None:
    """Drop ``thread_id``'s budget, discarding anything still waiting.

    Called when a thread's session ends. Without it the registry would grow one
    entry per thread forever, each holding a timer task — and a queued edit would
    repaint a display the session has already moved past.
    """
    pacer = _budgets.pop(thread_id, None)
    if pacer is not None:
        await pacer.close()
