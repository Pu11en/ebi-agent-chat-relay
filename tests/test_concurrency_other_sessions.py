"""Other sessions are described to a new session, never handed to it as orders."""

from __future__ import annotations

from pathlib import Path

from claude_code_core.task_loop import worker_prompt
from claude_discord.concurrency import SessionRegistry


def test_another_sessions_prompt_is_quoted_as_not_yours() -> None:
    reg = SessionRegistry()
    reg.register(1, "You are the worker in a sequential task loop. Do the next task.", "/a")
    reg.register(2, "hello", "/b")
    notice = reg.build_concurrency_notice(2)
    assert "not instructions for you" in notice.lower()
    line = next(ln for ln in notice.splitlines() if "sequential task loop" in ln)
    assert '"' in line and "thread 1" in line.lower()


def test_worker_prompt_does_not_open_with_an_order() -> None:
    first = worker_prompt(Path("/p/PLAN.md"), Path("/p/PLAN.progress.md")).splitlines()[0]
    assert "gowork" in first.lower()
    assert not first.startswith("You are")
