"""Suppresses the console window Windows flashes for spawned child processes.

Every subprocess this bot launches (git, gh, node, claude, codex, grep) is a
console-subsystem executable. The bot itself normally runs under a hidden
scheduled task with no console window of its own, and on Windows that means
each child process gets a *new* console allocated -- which briefly flashes
on screen and then closes when the process exits. Passing CREATE_NO_WINDOW
suppresses that allocation. No-op on other platforms.
"""

from __future__ import annotations

import subprocess
import sys

NO_WINDOW: dict[str, int] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)
