"""Sequential task loop — one fresh session per task until the plan is done.

The "Ralph loop" pattern: the same prompt runs again and again, each time in a
brand-new session, and the only memory between rounds is the files — the plan
(``- [ ]`` checkboxes) and a progress log. Because the contract is files and a
status line, it behaves identically on every harness (Claude Code, Codex, DSH):
nothing here knows which one is running.

Two properties matter more than speed:

* **A claimed "done" is checked, not trusted.** After every ``DONE`` the loop
  looks at git itself: a new commit, a clean tree, and one more ticked box. A
  worker that reports success without committing is retried once with the
  reason, then the loop stops — the failure a human would otherwise find
  hours later.
* **The human is asked, not assumed.** A worker that needs a decision ends with
  ``ASK: <question>``; the loop pauses on it and carries the person's typed reply into
  the next round. Nothing is pushed, deployed or paid for without that.

This module is surface-agnostic: the frontend supplies ``run_round`` (run one
fresh session, return its final text), ``ask`` (a yes/no question) and
``report`` (post a line). See ``claude_discord/cogs/task_loop.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 40
DEFAULT_MAX_RETRIES = 1
#: A plan's own check (tests, a build, "the page loads") gets this long per run.
CHECK_TIMEOUT_SECONDS = 600

_TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)")
_CHECK_RE = re.compile(r"^\s*\**Check:\**\s*`?(.+?)`?\s*$", re.IGNORECASE)
_STATUS_RE = re.compile(r"^(DONE|COMPLETE|ASK:|STUCK:)\s*(.*)$")


class Status(Enum):
    DONE = "DONE"
    ASK = "ASK"
    STUCK = "STUCK"
    COMPLETE = "COMPLETE"
    #: No status line — or, as a loop outcome, stopped on request.
    NONE = "NONE"


def parse_status(text: str | None) -> tuple[Status, str]:
    """Read the worker's status from the last non-empty line of its reply."""
    lines = [ln.strip().strip("*_`").strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return Status.NONE, ""
    m = _STATUS_RE.match(lines[-1])
    if m is None:
        return Status.NONE, ""
    word = m.group(1).rstrip(":")
    return Status(word), m.group(2).strip()


def count_tasks(plan_text: str) -> tuple[int, int]:
    """Return ``(checked, unchecked)`` checkbox counts in *plan_text*."""
    checked = unchecked = 0
    for line in plan_text.splitlines():
        m = _TASK_RE.match(line)
        if m is None:
            continue
        if m.group(1) == " ":
            unchecked += 1
        else:
            checked += 1
    return checked, unchecked


def first_unchecked(plan_text: str) -> str | None:
    """The label of the first ``- [ ]`` task, or None when all are ticked."""
    for line in plan_text.splitlines():
        m = _TASK_RE.match(line)
        if m is not None and m.group(1) == " ":
            return m.group(2)
    return None


#: Where plans live, relative to the project: the root, docs/plans/, and
#: planning-with-files' .planning/<id>/.
_PLAN_GLOBS = ("*.md", "docs/plans/*.md", ".planning/*/*.md")


def list_plans(directory: Path) -> list[Path]:
    """Every plan in the project that still has unticked tasks, newest first.

    Only files whose name contains "plan" count, so a README with a stray
    checkbox is never mistaken for one.
    """
    found: list[Path] = []
    for pattern in _PLAN_GLOBS:
        for path in directory.glob(pattern):
            if "plan" not in path.name.lower() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if count_tasks(text)[1]:
                found.append(path)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def find_plan(directory: Path) -> Path | None:
    """The newest plan with open tasks, or None."""
    plans = list_plans(directory)
    return plans[0] if plans else None


def plan_check_command(plan_text: str) -> list[str] | None:
    """The plan's ``Check:`` line as an argv, or None when the plan has none.

    Split with shlex and run without a shell, so the check is one program with
    arguments — the same thing the worker could run, and nothing more.
    """
    for line in plan_text.splitlines():
        m = _CHECK_RE.match(line)
        if m:
            with contextlib.suppress(ValueError):
                argv = shlex.split(m.group(1))
                return argv or None
            return None
    return None


async def run_check(
    repo_dir: Path, argv: list[str], timeout: float = CHECK_TIMEOUT_SECONDS
) -> tuple[bool, str]:
    """Run the plan's check in *repo_dir*. Returns (passed, last lines of output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return False, f"could not start the check: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return False, f"the check took longer than {int(timeout)}s"
    tail = "\n".join(out.decode(errors="replace").strip().splitlines()[-15:])
    return proc.returncode == 0, tail


@dataclass(frozen=True)
class Snapshot:
    head: str | None
    dirty: bool
    checked: int
    unchecked: int
    next_task: str | None


async def _git(repo_dir: Path, *args: str) -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_dir),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode(errors="replace") if proc.returncode == 0 else None


async def take_snapshot(repo_dir: Path, plan_path: Path) -> Snapshot:
    """Record what "progress" is measured against: HEAD, dirtiness, checkboxes."""
    head = await _git(repo_dir, "rev-parse", "HEAD")
    # Untracked files are ignored: build caches or someone's draft folder must
    # not make every round look unfinished. Uncommitted edits to tracked files
    # still count — that is the "claimed done without committing" failure.
    status = await _git(repo_dir, "status", "--porcelain", "--untracked-files=no")
    try:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    checked, unchecked = count_tasks(text)
    return Snapshot(
        head=head.strip() if head else None,
        dirty=bool(status and status.strip()),
        checked=checked,
        unchecked=unchecked,
        next_task=first_unchecked(text),
    )


def check_done(before: Snapshot, after: Snapshot) -> list[str]:
    """Why a claimed ``DONE`` does not hold up — empty when it does."""
    problems: list[str] = []
    if after.head is None or after.head == before.head:
        problems.append("no new git commit was made")
    if after.unchecked >= before.unchecked:
        problems.append("the task's checkbox in the plan was not ticked")
    if after.dirty:
        problems.append("there are uncommitted changes left in the repo")
    return problems


def worker_prompt(
    plan_path: Path,
    progress_path: Path,
    *,
    answer: tuple[str, str] | None = None,
    retry_reason: str | None = None,
    notes: list[str] | None = None,
) -> str:
    """The prompt every round gets. Same text every time, by design."""
    parts = [
        "You are the worker in a sequential task loop. You start with no memory; "
        "the files below are the whole state.",
        "",
        f"1. Read the plan: {plan_path}",
        f"2. Read the progress log if it exists: {progress_path}",
        "3. Do the first unchecked task (`- [ ]`) ONLY. Do not start the next one.",
        "4. Check your work actually works: run the plan's `Check:` command if it has one "
        "(the bot runs it too, and a failure means the task is not done), and run the "
        "project's tests.",
        "5. Commit the work with git. Tick that task's box (`- [x]`) in the plan and "
        "append a short entry to the progress log (what you did, the commit, how you "
        "checked it, anything left open) — commit those too. Leave no uncommitted changes.",
        "",
        "Never push, deploy, delete data, spend money or use new API keys without asking "
        "first. To ask, stop and end with an ASK line; the human replies in their own words.",
        "",
        "Finish with a recap written for a non-technical reader:",
        "**Task N of M: <name> — done ✅** (or the honest status)",
        "What happened: <one or two plain sentences>",
        "What changed for you: <what the person will notice>",
        "Anything you need to do: <usually 'nothing'>",
        "",
        "The very last line must be exactly one of:",
        "DONE — the task is finished, committed and ticked",
        "ASK: <one simple question, plus a plain example of what each answer changes, e.g. 'Round division to 2 decimals? yes: 10/3 shows 3.33, no: 3.3333'> — you need a decision before continuing",
        "STUCK: <plain reason> — you cannot finish this task",
        "COMPLETE — every task in the plan is already ticked",
    ]
    if answer is not None:
        question, reply = answer
        parts += [
            "",
            f'Last round you asked: "{question}" — the human replied: "{reply}". '
            "If that answers it, continue the same task with that answer. If it is a "
            "question or unclear, explain in plain words with a concrete example and end "
            "with a new ASK line. Don't start the task until you have a clear answer.",
        ]
    if notes:
        parts += ["", "While the last task ran, the human wrote (take it into account):"]
        parts += [f'- "{n}"' for n in notes]
    if retry_reason:
        parts += [
            "",
            f"Last round said DONE but the check failed: {retry_reason}. "
            "Fix that for the same task.",
        ]
    return "\n".join(parts)


@dataclass(frozen=True)
class LoopOutcome:
    status: Status
    detail: str = ""
    rounds: int = 0


RunRound = Callable[[str], Awaitable[tuple[str | None, str | None]]]
Ask = Callable[[str], Awaitable[str | None]]
Report = Callable[[str], Awaitable[None]]


class TaskLoop:
    """Run fresh-session rounds against a plan until it is complete or blocked."""

    def __init__(
        self,
        *,
        plan_path: Path,
        repo_dir: Path,
        run_round: RunRound,
        ask: Ask,
        report: Report,
        progress_path: Path | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.plan_path = plan_path
        self.repo_dir = repo_dir
        self.progress_path = progress_path or plan_path.with_name(f"{plan_path.stem}.progress.md")
        self._run_round = run_round
        self._ask = ask
        self._report = report
        self.max_rounds = max_rounds
        self.max_retries = max_retries
        self._stop = False
        #: Things the person typed while a task was running.
        self._notes: list[str] = []

    async def _run_plan_check(self) -> list[str]:
        """The bot's own check after a claimed DONE — trust proof, not words."""
        try:
            text = self.plan_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        argv = plan_check_command(text)
        if argv is None:
            return []
        ok, tail = await run_check(self.repo_dir, argv)
        return [] if ok else [f"the plan's check failed ({' '.join(argv)}): {tail}"]

    def add_note(self, text: str) -> None:
        """Something the person typed mid-task; the next round reads it."""
        self._notes.append(text)

    def request_stop(self) -> None:
        """Stop after the round in flight — never mid-task."""
        self._stop = True

    async def run(self) -> LoopOutcome:
        answer: tuple[str, str] | None = None
        retry_reason: str | None = None
        retries = 0
        rounds = 0
        while True:
            before = await take_snapshot(self.repo_dir, self.plan_path)
            total = before.checked + before.unchecked
            if before.unchecked == 0:
                await self._report(f"🏁 All {total} tasks are done.")
                return LoopOutcome(Status.COMPLETE, rounds=rounds)
            if self._stop:
                await self._report("⏹️ Loop stopped.")
                return LoopOutcome(Status.NONE, "stopped", rounds)
            if rounds >= self.max_rounds:
                await self._report(f"🛑 Stopped after {rounds} rounds (the safety limit).")
                return LoopOutcome(Status.STUCK, "round limit reached", rounds)

            rounds += 1
            prompt = worker_prompt(
                self.plan_path,
                self.progress_path,
                answer=answer,
                retry_reason=retry_reason,
                notes=self._notes,
            )
            self._notes = []
            answer = retry_reason = None
            text, error = await self._run_round(prompt)
            status, detail = parse_status(text)
            after = await take_snapshot(self.repo_dir, self.plan_path)

            if status == Status.ASK and detail:
                reply = await self._ask(detail)
                if reply is None:
                    await self._report(f"⏸️ Paused, waiting for your answer: {detail}")
                    return LoopOutcome(Status.ASK, detail, rounds)
                answer = (detail, reply)
                continue
            if status == Status.STUCK:
                await self._report(f"🛑 Stuck on {before.next_task}: {detail}")
                return LoopOutcome(Status.STUCK, detail, rounds)
            if status == Status.COMPLETE and after.unchecked == 0:
                continue  # the top of the loop reports completion

            problems = (
                check_done(before, after)
                if status == Status.DONE
                else [error or "the worker ended without a status line"]
            )
            if not problems:
                problems = await self._run_plan_check()
            if not problems:
                retries = 0
                await self._report(f"✅ Task {after.checked} of {total} done: {before.next_task}")
                continue
            retries += 1
            reason = "; ".join(problems)
            if retries > self.max_retries:
                await self._report(f"🛑 Stuck on {before.next_task}: {reason}")
                return LoopOutcome(Status.STUCK, reason, rounds)
            logger.info("task loop retrying %s: %s", before.next_task, reason)
            retry_reason = reason
