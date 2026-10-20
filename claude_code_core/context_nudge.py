"""Offer a fresh session as context grows, preserving the working state.

Capacity is a useful signal, not proof of forgetting or a guaranteed slowdown.
The nudge offers a choice: write a handoff, then continue from it in a new thread.

Frontend-agnostic rules live here; the Discord flow is
``claude_discord/cogs/context_nudge.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Fractions of the context window at which to suggest a fresh session. Each is
#: offered once per thread, so a "no" stays quiet until the next step.
NUDGE_STEPS = (50, 75, 90)

#: Backends whose context figure is a characters/4 estimate, not a measurement.
ESTIMATED_CONTEXT_BACKENDS = frozenset({"dsh"})

_PART_RE = re.compile(r"^(.*?) · part (\d+)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_THREAD_NAME = 100
_HANDOFF_SECTIONS = (
    "Goal",
    "Where things stand",
    "Decisions",
    "Next steps",
    "Key files",
    "Working state",
    "Verification",
)


def nudge_step(used: int | None, window: int | None, already: int) -> int | None:
    """The highest step newly crossed, or None when there is nothing to say."""
    if not used or not window or window <= 0:
        return None
    pct = used * 100 / window
    crossed = [s for s in NUDGE_STEPS if pct >= s and s > already]
    return max(crossed) if crossed else None


def context_label(pct: float, backend: str | None, *, estimated: bool = False) -> str:
    """``"67%"``, or ``"67% (estimate)"`` when the figure was not measured."""
    label = f"{round(pct)}%"
    if estimated or (backend or "") in ESTIMATED_CONTEXT_BACKENDS:
        label += " (estimate)"
    return label


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
            "## Working state — branch, commit, changed files and unfinished work",
            "## Verification — checks run, actual results and remaining checks",
            "Put substantive content below every heading; say none when appropriate. "
            "Preserve constraints, accepted authority and unresolved choices in Decisions.",
            "Write it for someone smart who was not here. Do not do any other work. "
            "When the file is saved, reply with one short sentence.",
        ]
    )


def starter_prompt(path: Path, previous: str) -> str:
    """The first message of the fresh session."""
    return (
        f"Continuing from {previous}. Read {path} first — it is the handoff from the "
        "previous session. Verify the current workspace, preserve its decisions and constraints, "
        "and continue the next action already authorized. Ask only for an unresolved decision, "
        "missing information or required authority. Keep updates short."
    )


def valid_handoff(path: Path, workdir: Path) -> bool:
    """Require a bounded project-local file with usable content in every section."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if not path.resolve().is_relative_to(workdir.resolve()):
            return False
        with path.open("rb") as stream:
            data = stream.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            return False
        text = data.decode("utf-8")
    except (OSError, UnicodeError, RuntimeError):
        return False
    sections = re.split(r"(?m)^##\s+", text)[1:]
    present = set()
    for section in sections:
        title, _, body = section.partition("\n")
        for name in _HANDOFF_SECTIONS:
            if re.fullmatch(re.escape(name) + r"(?:\s*[—:-].*)?\s*", title) and body.strip():
                present.add(name)
    return present == set(_HANDOFF_SECTIONS)


def next_thread_name(name: str) -> str:
    """``x`` → ``x · part 2``; ``x · part 2`` → ``x · part 3``; fits Discord's 100."""
    m = _PART_RE.match(name)
    base, n = (m.group(1), int(m.group(2)) + 1) if m else (name, 2)
    suffix = f" · part {n}"
    return base[: _MAX_THREAD_NAME - len(suffix)] + suffix
