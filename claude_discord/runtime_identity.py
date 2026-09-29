"""Which code this process is running, captured once when it starts.

A checkout can move after the bot has loaded its modules, so the Git revision
on disk is not proof of what is running. The identity below is read once, at
import during startup, and never refreshed; health reports it next to the
current disk revision so a mismatch is visible instead of assumed away.
Anything Git cannot tell us is reported as ``"unknown"``, never guessed.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = ["BOOT_IDENTITY", "UNKNOWN", "RuntimeIdentity", "capture_identity", "disk_revision"]

UNKNOWN = "unknown"
_GIT_TIMEOUT_SECONDS = 2.0
_SOURCE_ROOT = Path(__file__).resolve().parent.parent

#: ``(argv) -> stdout``; raises on failure. Injectable for tests.
GitRunner = Callable[[list[str]], str]


def _run_git(argv: list[str]) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        argv, capture_output=True, text=True, check=True, timeout=_GIT_TIMEOUT_SECONDS
    ).stdout


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """The process's own identity: fixed for its lifetime."""

    pid: int
    started_at: str
    commit: str
    #: Uncommitted tracked changes at startup; None when Git could not say.
    dirty: bool | None

    def as_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "commit": self.commit,
            "dirty": self.dirty,
        }


def _git_argv(root: Path, *args: str) -> list[str]:
    # --no-optional-locks: reading must never rewrite the checkout's index.
    return ["git", "--no-optional-locks", "-C", str(root), *args]


def _head(root: Path, git: GitRunner) -> str:
    try:
        return git(_git_argv(root, "rev-parse", "HEAD")).strip() or UNKNOWN
    except Exception:
        return UNKNOWN


def _revision(root: Path, git: GitRunner) -> tuple[str, bool | None]:
    commit = _head(root, git)
    if commit == UNKNOWN:
        return UNKNOWN, None
    try:
        status = git(_git_argv(root, "status", "--porcelain", "--untracked-files=no"))
    except Exception:
        return commit, None
    return commit, bool(status.strip())


def capture_identity(root: Path = _SOURCE_ROOT, *, git: GitRunner = _run_git) -> RuntimeIdentity:
    commit, dirty = _revision(root, git)
    return RuntimeIdentity(
        pid=os.getpid(),
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        commit=commit,
        dirty=dirty,
    )


async def disk_revision(root: Path = _SOURCE_ROOT, *, git: GitRunner = _run_git) -> str:
    """The revision checked out on disk now, which may differ from what is running."""
    return await asyncio.to_thread(_head, root, git)


#: Captured at import, which happens during startup.
BOOT_IDENTITY = capture_identity()
