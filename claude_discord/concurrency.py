"""Concurrency awareness for multiple simultaneous Claude Code sessions.

Layer 1: Every session receives a generic concurrency warning in its prompt.
Layer 2: An in-memory registry tracks active sessions so each one knows
         what others are doing and can avoid conflicts.

See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/52
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Layer 2: Active Session Registry
# ---------------------------------------------------------------------------


@dataclass
class ActiveSession:
    """Tracks a single active Claude Code session."""

    thread_id: int
    description: str
    working_dir: str | None = None
    execution_state: str = "running"


_BASE_CONCURRENCY_NOTICE = """\
[CONCURRENCY NOTICE — MANDATORY] You may be one of multiple Claude Code sessions \
running simultaneously via Discord. Your thread ID is {thread_id}. \
Messages marked [this thread] in the AI Lounge are YOUR earlier posts from \
this same thread — not from other sessions. After context compaction you may \
see your own lounge messages; do NOT treat them as another session's work. \
You MUST follow these rules to avoid destroying each other's work:

1. **Git**: {git_guidance}
2. **Files**: Another session may be editing the same files RIGHT NOW. \
Check `git status` and recent file modification times before overwriting.
3. **Ports & processes**: Shared network ports or lock files may already be in use.
4. **Resources**: Shared databases, APIs with rate limits, or singleton processes \
may be accessed concurrently.
5. **Working directory does NOT persist between messages**: Each of your \
Discord replies runs in a FRESH process that starts in the base working \
directory. A `cd` only lasts for the current message — it is gone by your next \
reply, and the shell resets. So a relative-path script you set up in one \
message will silently run in the WRONG directory later. ALWAYS use absolute \
paths (for scripts, `os.chdir` to an absolute path at startup); for long jobs \
or large output, write results to an absolute-path log file and read it back.

CRITICAL: Do not create loose `wt-*` directories beside projects.\
"""

_DIRECT_GIT_GUIDANCE = """\
No other active session is registered in your assigned project. If \
`.worktrees/wt-{thread_id}` exists, enter it and continue there. Otherwise, if \
the assigned project is a Git checkout, run `git worktree list` and continue an \
existing `session/{thread_id}` worktree if one is listed. If branch \
`session/{thread_id}` already exists without a worktree, restore it with \
`git worktree add .worktrees/wt-{thread_id} session/{thread_id}`. Otherwise, \
work directly in your assigned project directory; do not create a disposable \
worktree. Keep `/.worktrees/` in the repository's local `.git/info/exclude`. \
Always commit before finishing."""

_ISOLATED_GIT_GUIDANCE = """\
Another active session is using your assigned project, so isolation is REQUIRED. \
If `.worktrees/wt-{thread_id}` exists, enter it and continue there. Otherwise, \
if the assigned project is a Git checkout, run `git worktree list` and continue \
an existing `session/{thread_id}` worktree if one is listed. If branch \
`session/{thread_id}` already exists without a worktree, restore it with \
`git worktree add .worktrees/wt-{thread_id} session/{thread_id}`. Otherwise \
create `.worktrees/wt-{thread_id}` on a new `session/{thread_id}` branch. Ensure \
`/.worktrees/` is present in the repository's local `.git/info/exclude` before \
creating it. Work only inside that worktree and commit before finishing."""

_OTHER_SESSIONS_HEADER = """
⚠️ ACTIVE SESSIONS RIGHT NOW (you MUST avoid conflicts with these):
"""


class SessionRegistry:
    """Thread-safe registry of active Claude Code sessions.

    Designed to be shared across all Cogs in a single bot instance.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, ActiveSession] = {}
        self._lock = threading.Lock()

    def register(
        self,
        thread_id: int,
        description: str,
        working_dir: str | None = None,
    ) -> None:
        """Register or replace an active session."""
        with self._lock:
            self._sessions[thread_id] = ActiveSession(
                thread_id=thread_id,
                description=description,
                working_dir=working_dir,
            )

    def unregister(self, thread_id: int) -> None:
        """Remove a session from the registry."""
        with self._lock:
            self._sessions.pop(thread_id, None)

    def update(
        self,
        thread_id: int,
        *,
        description: str | None = None,
        working_dir: str | None = None,
        execution_state: str | None = None,
    ) -> None:
        """Update fields of an existing session. No-op if not registered."""
        with self._lock:
            session = self._sessions.get(thread_id)
            if session is None:
                return
            if description is not None:
                session.description = description
            if working_dir is not None:
                session.working_dir = working_dir
            if execution_state is not None:
                session.execution_state = execution_state

    def list_active(self) -> list[ActiveSession]:
        """Return all active sessions."""
        with self._lock:
            return list(self._sessions.values())

    def list_others(self, thread_id: int) -> list[ActiveSession]:
        """Return all active sessions except the given thread."""
        with self._lock:
            return [s for s in self._sessions.values() if s.thread_id != thread_id]

    def build_concurrency_notice(self, thread_id: int) -> str:
        """Build the full concurrency notice for a session.

        Combines the base Layer 1 warning with Layer 2 context about
        other active sessions.
        """
        current = next((s for s in self.list_active() if s.thread_id == thread_id), None)
        others = self.list_others(thread_id)
        current_dir = (
            os.path.normcase(os.path.normpath(current.working_dir))
            if current and current.working_dir
            else None
        )
        shares_project = bool(
            current_dir
            and any(
                other.working_dir
                and os.path.normcase(os.path.normpath(other.working_dir)) == current_dir
                for other in others
            )
        )
        git_guidance = _ISOLATED_GIT_GUIDANCE if shares_project else _DIRECT_GIT_GUIDANCE
        notice = _BASE_CONCURRENCY_NOTICE.format(
            thread_id=thread_id,
            git_guidance=git_guidance.format(thread_id=thread_id),
        )
        if others:
            notice += _OTHER_SESSIONS_HEADER
            for s in others:
                line = f"- {s.description}"
                if s.working_dir:
                    line += f" (working in {s.working_dir})"
                notice += line + "\n"
            if shares_project:
                notice += (
                    "\nA session above is in this same project. Continue or create the "
                    "project-local worktree described above before editing.\n"
                )
        return notice
