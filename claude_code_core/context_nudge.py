"""Suggest a fresh session before a long one goes bad.

A session that has filled half its context window is slower, costs more per
turn and starts losing track of early details — but it is also where the
person's whole working state lives, so "just start over" is not free. The
nudge makes the healthy move one tap: the old session writes a handoff file
while it still remembers everything, and a new thread starts from that file.

Frontend-agnostic rules live here; the Discord flow is
``claude_discord/cogs/context_nudge.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Fractions of the context window at which to suggest a fresh session. Each is
#: offered once per thread, so a "no" stays quiet until the next step.
NUDGE_STEPS = (50, 75, 90)

_PART_RE = re.compile(r"^(.*?) · part (\d+)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_THREAD_NAME = 100


def nudge_step(used: int | None, window: int | None, already: int) -> int | None:
    """The highest step newly crossed, or None when there is nothing to say."""
    if not used or not window or window <= 0:
        return None
    pct = used * 100 / window
    crossed = [s for s in NUDGE_STEPS if pct >= s and s > already]
    return max(crossed) if crossed else None


def handoff_path(workdir: Path, thread_name: str, date: str) -> Path:
    """``<workdir>/handoffs/<date>-<slug>.md`` — inside the project, easy to find."""
    slug = _SLUG_RE.sub("-", thread_name.lower()).strip("-")[:50] or "session"
    return workdir / "handoffs" / f"{date}-{slug}.md"


def handoff_prompt(path: Path) -> str:
    """What the old session is asked to write while it still remembers."""
    return "\n".join(
        [
            "This session is getting long, so we're continuing in a fresh one.",
            f"Write a handoff file at {path} (create the folder if needed) so a new "
            "session with no memory can pick up exactly where we are. Use these sections:",
            "## Goal — what we are ultimately trying to achieve",
            "## Where things stand — what is done, what is in progress, what is broken",
            "## Decisions — what was decided and why (so it is not re-asked)",
            "## Next steps — the very next thing to do, then the ones after",
            "## Key files — absolute paths worth reading first",
            "Write it for someone smart who was not here. Do not do any other work. "
            "When the file is saved, reply with one short sentence.",
        ]
    )


def starter_prompt(path: Path, previous: str) -> str:
    """The first message of the fresh session."""
    return (
        f"Continuing from {previous}. Read {path} first — it is the handoff from the "
        "previous session. Then tell me in a few plain sentences where we are and "
        "what the next step is, and ask before starting it."
    )


def next_thread_name(name: str) -> str:
    """``x`` → ``x · part 2``; ``x · part 2`` → ``x · part 3``; fits Discord's 100."""
    m = _PART_RE.match(name)
    base, n = (m.group(1), int(m.group(2)) + 1) if m else (name, 2)
    suffix = f" · part {n}"
    return base[: _MAX_THREAD_NAME - len(suffix)] + suffix
