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

from claude_code_core.gowork_plan import PlanValidationError, has_manifest, load_plan_tree
from claude_code_core.gowork_schedule import ReadyTask, ready_tasks
from claude_code_core.gowork_state import (
    BuildState,
    StaleAttemptError,
    TaskStatus,
    open_build_state,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 40
DEFAULT_MAX_RETRIES = 1
#: A plan's own check (tests, a build, "the page loads") gets this long per run.
CHECK_TIMEOUT_SECONDS = 600

_TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)")
_GOAL_RE = re.compile(r"^\s*\**Goal:\**\s*(.+?)\s*$", re.IGNORECASE)
_DONE_WHEN_RE = re.compile(r"^\s*\**Done when:\**\s*(.+?)\s*$", re.IGNORECASE)
_CHECK_RE = re.compile(r"^\s*\**Check:\**\s*`?(.+?)`?\s*$", re.IGNORECASE)
_CHOICE_LINE_RE = re.compile(r"^(\*\*)?[A-Ea-e][).:]")
_STATUS_RE = re.compile(r"^(DONE|COMPLETE|ASK:|STUCK:|PAUSE:|SKIP:|PLAN:)\s*(.*)$")


class Status(Enum):
    DONE = "DONE"
    ASK = "ASK"
    STUCK = "STUCK"
    COMPLETE = "COMPLETE"
    #: The person wants to hold (plan elsewhere, not yet): keep the work, wait for them.
    PAUSE = "PAUSE"
    #: The person said to skip this step.
    SKIP = "SKIP"
    #: The worker changed the plan itself because the person redirected it.
    PLAN = "PLAN"
    #: No status line — or, as a loop outcome, stopped on request.
    NONE = "NONE"


_LIMIT_RE = re.compile(
    r"(session|usage|weekly|daily|hourly|5-hour) limit|limit (reached|exceeded)|hit your .*limit|"
    r"exceeded your current quota|quota exceeded|insufficient_quota|too many requests|\b429\b",
    re.IGNORECASE,
)
#: In the AI's own reply only the provider's wording counts: a worker building rate
#: limiting, or hitting "recursion limit exceeded", is doing its job, not out of quota.
_LIMIT_IN_REPLY_RE = re.compile(
    r"(session|usage|weekly|daily|5-hour) limit|hit your .*limit|"
    r"exceeded your current quota|insufficient_quota",
    re.IGNORECASE,
)


def usage_limit_message(text: str | None, error: str | None) -> str | None:
    """The provider's "you've hit your limit" line, or None for an ordinary result.

    A limit says nothing about the task, so it must never count as a failed try.
    A reply that ends with a status line is real work, even if it mentions limits.
    """
    for line in (error or "").splitlines():
        if _LIMIT_RE.search(line):
            return line.strip()[:300]
    if parse_status(text)[0] is Status.NONE:
        for line in (text or "").splitlines():
            if _LIMIT_IN_REPLY_RE.search(line):
                return line.strip()[:300]
    return None


def parse_status(text: str | None) -> tuple[Status, str]:
    """Read the worker's status from the last non-empty line of its reply."""
    lines = [ln.strip().strip("*_`").strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return Status.NONE, ""
    m = _STATUS_RE.match(lines[-1])
    if m is None:
        # An AI often puts the lettered choices *under* its ASK line. Only choice
        # lines may follow it; anything else means the ASK wasn't the last word.
        end = len(lines)
        while end and _CHOICE_LINE_RE.match(lines[end - 1]):
            end -= 1
        if 0 < end < len(lines) and lines[end - 1].startswith("ASK:"):
            question = lines[end - 1][4:].strip()
            return Status.ASK, "\n".join([question, *lines[end:]])
        return Status.NONE, ""
    word = m.group(1).rstrip(":")
    return Status(word), m.group(2).strip()


def _task_blocks(lines: list[str]) -> list[tuple[int, int, bool, str]]:
    """Each task as (first line, end line, ticked, label); indented lines belong to it."""
    blocks: list[tuple[int, int, bool, str]] = []
    i = 0
    while i < len(lines):
        m = _TASK_RE.match(lines[i])
        if m is None:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j][:1] in (" ", "\t") and lines[j].strip():
            if _TASK_RE.match(lines[j]):
                break
            j += 1
        blocks.append((i, j, m.group(1) != " ", m.group(2).strip()))
        i = j
    return blocks


def merge_open_tasks(copy_text: str, old_real: str, new_real: str) -> str | None:
    """Apply what the planner changed in the real plan to the build's copy, or None.

    Only the difference between *old_real* (the real plan when the build last
    looked) and *new_real* is applied: steps the planner removed are dropped if
    still open, steps the planner added are inserted. Everything the build did
    to its own plan — ticks, skips, split steps, fix steps — stays as it is.
    """
    copy_lines = copy_text.splitlines(keepends=True)
    new_lines = new_real.splitlines(keepends=True)
    old_open = {
        label
        for _s, _e, ticked, label in _task_blocks(old_real.splitlines(keepends=True))
        if not ticked
    }
    new_blocks = _task_blocks(new_lines)
    new_open = {label for _s, _e, ticked, label in new_blocks if not ticked}
    copy_blocks = _task_blocks(copy_lines)
    in_copy = {label for _s, _e, _t, label in copy_blocks}
    removed = old_open - new_open
    added = [
        (s, e, label)
        for s, e, ticked, label in new_blocks
        if not ticked and label not in old_open and label not in in_copy
    ]
    if not removed and not added:
        return None

    drop = {
        i
        for s, e, ticked, label in copy_blocks
        if not ticked and label in removed
        for i in range(s, e)
    }
    # Where each added step goes: after the nearest earlier real-plan step the copy has.
    end_of: dict[str, int] = {label: e for _s, e, _t, label in copy_blocks}
    order = [label for _s, _e, _t, label in new_blocks]
    open_starts = [s for s, _e, ticked, _l in copy_blocks if not ticked and s not in drop]
    fallback = (
        open_starts[0] if open_starts else (copy_blocks[-1][1] if copy_blocks else len(copy_lines))
    )
    inserts: dict[int, list[str]] = {}
    for s, e, label in added:
        at = fallback
        for earlier in reversed(order[: order.index(label)]):
            if earlier in end_of and earlier not in removed:
                at = end_of[earlier]
                break
        block = new_lines[s:e]
        if block and not block[-1].endswith("\n"):
            block[-1] += "\n"
        inserts.setdefault(at, []).extend(block)
    out: list[str] = []
    for i, line in enumerate(copy_lines + [""]):
        out += inserts.get(i, [])
        if i < len(copy_lines) and i not in drop:
            out.append(line)
    merged = "".join(out)
    return None if merged == copy_text else merged


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


def plan_goal(plan_text: str) -> tuple[str | None, str | None]:
    """The plan's ``Goal:`` and ``Done when:`` lines — what the whole build is for."""
    goal = done = None
    for line in plan_text.splitlines():
        if goal is None and (m := _GOAL_RE.match(line)):
            goal = m.group(1).strip("* ")
        elif done is None and (m := _DONE_WHEN_RE.match(line)):
            done = m.group(1).strip("* ")
    return goal, done


def plan_check_command(plan_text: str) -> list[str] | None:
    """The plan's ``Check:`` line as an argv, or None when the plan has none.

    Split with shlex and run without a shell, so the check is one program with
    arguments — the same thing the worker could run, and nothing more.
    """
    for line in plan_text.splitlines():
        m = _CHECK_RE.match(line)
        if m:
            # "Check: `cmd args` (a note)" — only the part in backticks is the command.
            quoted = re.search(r"`([^`]+)`", line)
            command = quoted.group(1) if quoted else m.group(1)
            with contextlib.suppress(ValueError):
                argv = shlex.split(command)
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


_NOT_REPAIRABLE = ("cancelled", "interrupted by a restart", "required review unavailable")


def _is_repairable(reason: str) -> bool:
    """A failure of the work itself, not of the machinery around it."""
    lowered = reason.lower()
    return not any(marker in lowered for marker in _NOT_REPAIRABLE)


def worker_prompt(
    plan_path: Path,
    progress_path: Path,
    *,
    answer: tuple[str, str] | None = None,
    retry_reason: str | None = None,
    notes: list[str] | None = None,
) -> str:
    """The prompt every round gets. The person's words, when there are any, come first."""
    parts = [
        "[gowork build worker — for this worker only] You are the worker in a "
        "sequential task loop. You start with no memory; the files below are the "
        "whole state.",
    ]
    with contextlib.suppress(OSError):
        goal, done = plan_goal(plan_path.read_text(encoding="utf-8", errors="replace"))
        if goal:
            parts += [
                "",
                f"The goal of this whole build: {goal}"
                + (f" It's done when: {done}" if done else ""),
                "Every step should move toward that goal. If the step as written would "
                "miss it, say so in your recap.",
            ]
    heard: list[str] = []
    if answer is not None:
        question, reply = answer
        heard.append(f'You asked: "{question}" — they replied: "{reply}"')
    heard += [f'They wrote: "{n}"' for n in notes or []]
    if heard:
        parts += [
            "",
            "THE PERSON'S WORDS COME FIRST. Read them before anything else:",
            *[f"- {h}" for h in heard],
            "Work out what they actually want, and follow that over the plan. If they "
            "answered your question, carry on with that answer. If they asked something, "
            "answer it in plain words. If they changed direction (hold off, not yet, "
            "skip, do something else, save and stop), do that instead of the task, and "
            "end with PAUSE, SKIP or PLAN below. If their reply is unclear, explain in "
            "plain words with a concrete example and end with a new, clearer ASK line — "
            "never repeat a question they already answered.",
        ]
    parts += [
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
        "ASK: <one simple question, plus a plain example of what each answer changes, "
        "e.g. 'Round division to 2 decimals? yes: 10/3 shows 3.33, no: 3.3333'> "
        "— you need a decision before continuing",
        "STUCK: <plain reason> — you cannot finish this task",
        "COMPLETE — every task in the plan is already ticked",
        "PAUSE: <what they want, in plain words> — the person wants to hold or stop for "
        "now; save and commit what you have first. The build waits for them.",
        "SKIP: <why> — the person said to skip this task; the bot marks it skipped",
        "PLAN: <what you changed> — the person redirected the work, so you edited the "
        "plan file (added, removed, reordered or reworded tasks) and committed it",
    ]
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


@dataclass(frozen=True)
class ManifestResult:
    """What one manifest task's worker came back with."""

    task_id: str
    ok: bool
    detail: str = ""
    commit: str | None = None
    checks: tuple[str, ...] = ()
    #: The worker thread the attempt ran in, for archiving after the save (T14).
    thread_id: int | None = None


#: Runs one ready task to completion and reports it (T11b, per worker since T13).
ManifestWorker = Callable[[ReadyTask], Awaitable[ManifestResult]]
RunRound = Callable[[str], Awaitable[tuple[str | None, str | None]]]
#: Told the limit message; True once another AI was picked or the wait is over.
OnLimit = Callable[[str], Awaitable[bool]]
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
        on_limit: OnLimit | None = None,
        before_round: Callable[[], Awaitable[None]] | None = None,
        next_group: Callable[[list[str]], Awaitable[list[str]]] | None = None,
        run_group: Callable[[list[str]], Awaitable[list[tuple[str, bool, str]]]] | None = None,
        on_result: Callable[[str, str, str], Awaitable[None]] | None = None,
        review: Callable[[str, str | None], Awaitable[str | None]] | None = None,
        max_parallel: Callable[[], int] | None = None,
        manifest_worker: ManifestWorker | None = None,
        after_manifest_result: Callable[[ManifestResult], Awaitable[None]] | None = None,
        reconcile: Callable[[BuildState], Awaitable[None]] | None = None,
        state_path: Path | None = None,
        build_id: str = "",
    ) -> None:
        self.plan_path = plan_path
        self.repo_dir = repo_dir
        self.progress_path = progress_path or plan_path.with_name(f"{plan_path.stem}.progress.md")
        self._run_round = run_round
        self._ask = ask
        self._report = report
        self.max_rounds = max_rounds
        self.max_retries = max_retries
        self._on_limit = on_limit
        #: Runs before every step — the frontend pulls in the planner's plan edits.
        self._before_round = before_round
        #: Parallel steps (idea 2): which open steps can go together, and running them.
        self._next_group = next_group
        #: How many may go at once: measured capacity on the adaptive path, else ten.
        self._max_parallel = max_parallel or (lambda: MAX_PARALLEL)
        #: Told how every round ended — (step, result, detail) — for the records.
        self._on_result = on_result
        #: A different AI reviews each finished step (idea 5); None = approved.
        self._review = review
        #: Steps a review already sent back once; a second "changes" is only noted.
        self._bounced: set[str] = set()
        self._run_group = run_group
        #: Steps that failed in a group run on their own from then on.
        self._solo: set[str] = set()
        self._stop = False
        #: Things the person typed while a task was running.
        self._notes: list[str] = []
        #: The multi-plan path (T11b): a manifest plan is built from its ledger.
        self._manifest_worker = manifest_worker
        #: Runs after a worker's result is on disk (T14: archive its thread, never before).
        self._after_manifest_result = after_manifest_result
        #: Salvages attempts a crash left running (T17); anything still running after
        #: it is blocked, never handed to a new worker.
        self._reconcile = reconcile
        self.state_path = state_path
        self.build_id = build_id

    async def _run_manifest(self) -> LoopOutcome:
        """Build a manifest plan: start what is ready, record each worker as it finishes.

        Workers are independent tasks; the first to finish is saved at once and its
        dependents may start while its siblings are still running (T13). Cancelling
        the build cancels the workers and leaves their attempts as they were.
        """
        assert self._manifest_worker is not None and self.state_path is not None
        rounds = 0
        in_flight: dict[str, asyncio.Task[ManifestResult]] = {}
        reconciled = False
        try:
            while True:
                if self._before_round is not None and not in_flight:
                    try:
                        await self._before_round()
                    except Exception:
                        logger.warning("task loop: before-round hook failed", exc_info=True)
                try:
                    tree = load_plan_tree(self.plan_path)
                    state = open_build_state(self.state_path, tree, build_id=self.build_id)
                except (PlanValidationError, StaleAttemptError, OSError) as exc:
                    await self._report(f"🛑 The plan can't be built as written: {exc}")
                    return LoopOutcome(Status.STUCK, str(exc), rounds)

                if state.last_sync:
                    sync = state.last_sync
                    plans = ", ".join(f"{p} → v{v}" for p, v in sync.changed_plans.items())
                    await self._report(
                        f"📝 Plan changed ({plans}): "
                        + (
                            f"{len(sync.reworked_tasks)} task(s) will be reworked at the new "
                            f"version ({', '.join(sync.reworked_tasks)}); their dependents wait"
                            if sync.reworked_tasks
                            else "nothing built so far is affected"
                        )
                        + (
                            f"; new task(s): {', '.join(sync.added_tasks)}"
                            if sync.added_tasks
                            else ""
                        )
                    )
                if not reconciled:
                    reconciled = True
                    await self._reconcile_interrupted(state)
                if not in_flight:
                    if self._stop:
                        await self._report("⏹️ Loop stopped.")
                        return LoopOutcome(Status.NONE, "stopped", rounds)
                    if rounds >= self.max_rounds:
                        await self._report(f"🛑 Stopped after {rounds} rounds (the safety limit).")
                        return LoopOutcome(Status.STUCK, "round limit reached", rounds)

                room = max(1, self._max_parallel()) - len(in_flight)
                ready = (
                    list(ready_tasks(state, running=in_flight, limit=room))
                    if room > 0 and not self._stop
                    else []
                )
                if ready:
                    rounds += 1
                    for task in ready:
                        state.begin(task.task_id)
                        in_flight[task.task_id] = asyncio.ensure_future(self._manifest_worker(task))
                    await self._report(
                        f"⚡ Started {len(ready)} task(s) — " + ", ".join(t.task_id for t in ready)
                    )
                if not in_flight:
                    return await self._manifest_outcome(state, rounds)

                done, _pending = await asyncio.wait(
                    in_flight.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    task_id = next(k for k, v in in_flight.items() if v is finished)
                    del in_flight[task_id]
                    try:
                        result = finished.result()
                    except asyncio.CancelledError:
                        result = ManifestResult(task_id, False, "the worker was cancelled")
                    except Exception as exc:
                        logger.warning("gowork: worker for %s raised", task_id, exc_info=exc)
                        result = ManifestResult(task_id, False, f"the worker failed: {exc}")
                    await self._record_manifest_result(state, result)
                    if self._after_manifest_result is not None:
                        try:
                            await self._after_manifest_result(result)
                        except Exception:
                            logger.warning(
                                "gowork: after-result hook failed for %s", task_id, exc_info=True
                            )
        except asyncio.CancelledError:
            for pending in in_flight.values():
                pending.cancel()
            await asyncio.gather(*in_flight.values(), return_exceptions=True)
            raise

    async def _reconcile_interrupted(self, state: BuildState) -> None:
        """Attempts still 'running' when the loop starts belong to a crashed bot (T17)."""
        stale = [r.task_id for r in state.records if r.status is TaskStatus.RUNNING]
        if not stale:
            return
        if self._reconcile is not None:
            try:
                await self._reconcile(state)
            except Exception:
                logger.warning("gowork: reconciling interrupted work failed", exc_info=True)
        left = [r for r in state.records if r.status is TaskStatus.RUNNING]
        for record in left:
            state.block(
                record.task_id,
                f"interrupted by a restart before its work was saved (attempt "
                f"{record.attempt}); it was not restarted on its own — say so to run it again",
            )
        salvaged = [t for t in stale if state[t].accepted]
        if salvaged or left:
            await self._report(
                "🔁 After the restart: "
                + (f"kept finished work for {', '.join(salvaged)}; " if salvaged else "")
                + (
                    f"{len(left)} interrupted task(s) wait for you: "
                    + ", ".join(r.task_id for r in left)
                    if left
                    else "nothing was lost"
                )
            )

    async def _record_manifest_result(self, state: BuildState, result: ManifestResult) -> None:
        """Persist one worker's outcome the moment it is known (T13)."""
        if result.task_id not in state:
            return
        attempt = state[result.task_id].attempt_id
        if result.thread_id is not None:
            state.note_thread(result.task_id, attempt, thread_id=result.thread_id)
        if result.commit:
            # The commit is evidence either way: kept for repair when the combined
            # check failed (T15), accepted when it passed.
            state.submit_result(
                result.task_id,
                attempt,
                commit=result.commit,
                checks=result.checks or ("the worker reported DONE",),
            )
        if result.ok and result.commit:
            try:
                state.accept(result.task_id, attempt)
            except StaleAttemptError as exc:
                # The plan changed underneath this attempt (T19): the work is kept, and a
                # user-directed rework — not a repair — follows at the new version.
                state.rework(result.task_id, str(exc))
                await self._result(result.task_id, "reworked", str(exc))
                await self._report(f"📝 {result.task_id} finished, but the plan changed: {exc}")
                return
            await self._result(result.task_id, "done", result.detail)
            return
        reason = result.detail or "the worker did not finish"
        state.block(result.task_id, reason)
        await self._result(result.task_id, "stuck", reason)
        # Exactly one automatic repair per task (T18): a genuine failure earns a fresh
        # attempt that knows why; an interruption does not spend it; a second failure
        # is a blocker for a person.
        if _is_repairable(reason) and state.repairs_left(result.task_id) > 0:
            state.repair(result.task_id)
            await self._report(
                f"🔧 {result.task_id} failed — trying once more with the reason: {reason[:200]}"
            )

    async def _manifest_outcome(self, state: BuildState, rounds: int) -> LoopOutcome:
        records = state.records
        if all(r.accepted for r in records):
            await self._report(
                f"✔️ All {len(records)} tasks are accepted. Checking the finished work…"
            )
            return LoopOutcome(Status.COMPLETE, rounds=rounds)
        blocked = [r for r in records if r.status is TaskStatus.BLOCKED]
        if blocked:
            lines = "; ".join(f"{r.task_id}: {r.reason or 'blocked'}" for r in blocked)
            await self._report(f"🛑 Stuck — {len(blocked)} task(s) blocked: {lines}")
            return LoopOutcome(Status.STUCK, f"blocked: {lines}", rounds)
        waiting = [r.task_id for r in records if not r.accepted]
        detail = "nothing is ready and nothing is running: " + ", ".join(waiting)
        await self._report(f"🛑 {detail}")
        return LoopOutcome(Status.STUCK, detail, rounds)

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
        # The step was already committed and clean; anything the check itself rewrote
        # in tracked files (a log, a cache) is the check's mess, not unsaved work.
        await _git(self.repo_dir, "checkout", "--", ".")
        return [] if ok else [f"the plan's check failed ({' '.join(argv)}): {tail}"]

    async def _check_group(self, done: list[str], base: str | None) -> str | None:
        """Steps built side by side get the same checks as one built alone.

        The plan's check runs on the merged result, and each step gets its review.
        Anything that fails is unticked and runs again on its own, with the reason.
        """
        if not done:
            return None
        problems = await self._run_plan_check()
        sent_back: list[str] = []
        reasons: list[str] = []
        if problems:
            sent_back = list(done)
            reasons.append("; ".join(problems))
            await self._report(f"🛑 The steps built together broke the plan's check: {reasons[0]}")
        else:
            for step in done:
                concern = await self._reviewed(step, base)
                if concern is not None and step not in self._bounced:
                    self._bounced.add(step)
                    sent_back.append(step)
                    reasons.append(f"{step}: {concern}")
                    await self._report(f"🔍 A second AI reviewed it and sent it back: {concern}")
        if not sent_back:
            return None
        for step in sent_back:
            untick_task(self.plan_path, step)
            self._solo.add(step)
        await _git(self.repo_dir, "add", "-A")
        await _git(self.repo_dir, "commit", "-qm", "gowork: steps sent back after a group")
        return "after building steps together: " + " | ".join(reasons)

    async def _reviewed(self, step: str, base: str | None) -> str | None:
        if self._review is None:
            return None
        try:
            return await self._review(step, base)
        except Exception:
            logger.warning("task loop: the review failed; counting the step as done", exc_info=True)
            return None

    async def _result(self, step: str | None, result: str, detail: str) -> None:
        if self._on_result is None:
            return
        try:
            await self._on_result(step or "", result, detail or "")
        except Exception:
            logger.warning("task loop: saving a round's result failed", exc_info=True)

    async def _parallel_group(self) -> list[str]:
        """The open steps to run together next, or [] to run the next one alone."""
        if self._next_group is None or self._run_group is None or self._notes:
            return []  # a note from the person goes to one step, not a crowd
        try:
            text = self.plan_path.read_text(encoding="utf-8", errors="replace")
            steps = open_tasks(text)
            if len(steps) < 2 or steps[0] in self._solo:
                return []
            group = await self._next_group(steps)
        except Exception:
            logger.warning("task loop: grouping steps failed", exc_info=True)
            return []
        return [s for s in group if s not in self._solo][: max(1, self._max_parallel())]

    def add_note(self, text: str) -> None:
        """Something the person typed mid-task; the next round reads it."""
        self._notes.append(text)

    def request_stop(self) -> None:
        """Stop after the round in flight — never mid-task."""
        self._stop = True

    def resume(self) -> None:
        """Clear a stop request so the next ``run()`` carries on."""
        self._stop = False

    async def run(self) -> LoopOutcome:
        if self._manifest_worker is not None and self.state_path is not None:
            try:
                text = self.plan_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if has_manifest(text):
                return await self._run_manifest()
        answer: tuple[str, str] | None = None
        retry_reason: str | None = None
        retries = 0
        rounds = 0
        while True:
            if self._before_round is not None:
                try:
                    await self._before_round()
                except Exception:
                    logger.warning("task loop: before-round hook failed", exc_info=True)
            before = await take_snapshot(self.repo_dir, self.plan_path)
            total = before.checked + before.unchecked
            if before.unchecked == 0:
                await self._report(f"✔️ All {total} tasks are done. Checking the finished work…")
                return LoopOutcome(Status.COMPLETE, rounds=rounds)
            if self._stop:
                await self._report("⏹️ Loop stopped.")
                return LoopOutcome(Status.NONE, "stopped", rounds)
            if rounds >= self.max_rounds:
                await self._report(f"🛑 Stopped after {rounds} rounds (the safety limit).")
                return LoopOutcome(Status.STUCK, "round limit reached", rounds)

            # A pending answer or retry reason belongs to one step, never a crowd.
            group = await self._parallel_group() if answer is None and retry_reason is None else []
            if len(group) > 1 and self._run_group is not None:
                rounds += 1
                results = await self._run_group(group)
                done = [label for label, ok, _ in results if ok]
                again = [label for label, ok, _ in results if not ok]
                self._solo.update(again)
                line = f"⚡ Ran {len(group)} steps at the same time: {len(done)} done"
                if again:
                    line += f", {len(again)} will run again on their own"
                await self._report(line)
                retry_reason = await self._check_group(done, before.head)
                continue

            rounds += 1
            prompt = worker_prompt(
                self.plan_path,
                self.progress_path,
                answer=answer,
                retry_reason=retry_reason,
                notes=self._notes,
            )
            notes, self._notes = self._notes, []
            pending = answer, retry_reason
            answer = retry_reason = None
            text, error = await self._run_round(prompt)
            limit = usage_limit_message(text, error) if self._on_limit else None
            if limit and self._on_limit is not None:
                # Not the task's fault: don't count the round or a try, keep the notes.
                rounds -= 1
                self._notes = notes + self._notes
                answer, retry_reason = pending
                await self._result(before.next_task, "limit", limit)
                if await self._on_limit(limit):
                    continue
                return LoopOutcome(Status.ASK, f"usage limit: {limit}", rounds)
            status, detail = parse_status(text)
            after = await take_snapshot(self.repo_dir, self.plan_path)

            if status == Status.ASK and detail:
                await self._result(before.next_task, "asked", detail)
                reply = await self._ask(detail)
                if reply is None:
                    await self._report(f"⏸️ Paused, waiting for your answer: {detail}")
                    return LoopOutcome(Status.ASK, detail, rounds)
                answer = (detail, reply)
                continue
            if status == Status.STUCK:
                await self._result(before.next_task, "stuck", detail)
                await self._report(f"🛑 Stuck on {before.next_task}: {detail}")
                return LoopOutcome(Status.STUCK, detail, rounds)
            if status == Status.PAUSE:
                await self._result(before.next_task, "paused", detail)
                await _git(self.repo_dir, "add", "-A")
                await _git(self.repo_dir, "commit", "-qm", "gowork: paused")
                return LoopOutcome(Status.PAUSE, detail or "paused", rounds)
            if status in (Status.SKIP, Status.PLAN):
                await self._result(
                    before.next_task, "skipped" if status == Status.SKIP else "replanned", detail
                )
                if status == Status.SKIP:
                    skip_task(self.plan_path)
                    await self._report(f"⏭️ Skipped {before.next_task}: {detail}")
                else:
                    await self._report(f"📝 Plan changed: {detail}")
                await _git(self.repo_dir, "add", "-A")
                await _git(self.repo_dir, "commit", "-qm", f"gowork: {status.value.lower()}")
                retries = 0
                continue
            if status == Status.COMPLETE and after.unchecked == 0:
                continue  # the top of the loop reports completion

            problems = (
                check_done(before, after)
                if status == Status.DONE
                else [error or "the worker ended without a status line"]
            )
            if (
                problems == ["the task's checkbox in the plan was not ticked"]
                and before.next_task
                and not await self._run_plan_check()
            ):
                # The work is committed and the check passes: a forgotten checkbox is
                # tidiness, not failure. Tick it and say so, rather than retry.
                tick_task(self.plan_path, before.next_task)
                await _git(self.repo_dir, "add", "-A")
                await _git(self.repo_dir, "commit", "-qm", "gowork: ticked the finished step")
                await self._report(f"✅ It forgot the checkbox, so I ticked it: {before.next_task}")
                problems = []
                after = await take_snapshot(self.repo_dir, self.plan_path)
            if not problems:
                problems = await self._run_plan_check()
            if not problems and before.next_task:
                concern = await self._reviewed(before.next_task, before.head)
                if concern is not None and before.next_task not in self._bounced:
                    self._bounced.add(before.next_task)
                    untick_task(self.plan_path, before.next_task)
                    await _git(self.repo_dir, "add", "-A")
                    await _git(self.repo_dir, "commit", "-qm", "gowork: a review sent it back")
                    await self._result(before.next_task, "review: changes", concern)
                    await self._report(f"🔍 A second AI reviewed it and sent it back: {concern}")
                    retry_reason = (
                        f"a reviewer (a different AI) checked this step and found: {concern}"
                    )
                    continue
                if concern is not None:
                    with (
                        contextlib.suppress(OSError),
                        self.progress_path.open("a", encoding="utf-8") as fh,
                    ):
                        fh.write(
                            f"\n- Reviewer still had concerns about {before.next_task}: {concern}\n"
                        )
                    await _git(self.repo_dir, "add", "-A")
                    await _git(self.repo_dir, "commit", "-qm", "gowork: review concerns noted")
            if not problems:
                retries = 0
                await self._result(before.next_task, "done", "")
                await self._report(f"✅ Task {after.checked} of {total} done: {before.next_task}")
                continue
            retries += 1
            reason = "; ".join(problems)
            await self._result(
                before.next_task, "stuck" if retries > self.max_retries else "retry", reason
            )
            if retries > self.max_retries:
                await self._report(f"🛑 Stuck on {before.next_task}: {reason}")
                return LoopOutcome(Status.STUCK, reason, rounds)
            logger.info("task loop retrying %s: %s", before.next_task, reason)
            retry_reason = reason


# ---------------------------------------------------------------------------
# The ending: try it locally, then keep it or fix it
# ---------------------------------------------------------------------------

_TRY_RE = re.compile(r"^\s*\**Try:\**\s*`?(.+?)`?\s*$", re.IGNORECASE)
_OPEN_RE = re.compile(r"^\s*\**Open:\**\s*<?(\S+?)>?\s*$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\])(.*\S)")
_LOOKS_GOOD_RE = re.compile(
    r"^\s*(yes|yep|yeah|y|ok|okay|good|great|perfect|lgtm|keep( it)?|ship( it)?|"
    r"looks (good|great|fine|perfect)|all good|works|it works)\b[\s!.]*$",
    re.IGNORECASE,
)


def plan_try_command(plan_text: str) -> list[str] | None:
    """The plan's ``Try:`` line (how to start a local copy) as an argv."""
    for line in plan_text.splitlines():
        m = _TRY_RE.match(line)
        if m:
            with contextlib.suppress(ValueError):
                return shlex.split(m.group(1)) or None
            return None
    return None


def plan_open_url(plan_text: str) -> str | None:
    """The plan's ``Open:`` line — the address to open once the copy runs."""
    for line in plan_text.splitlines():
        m = _OPEN_RE.match(line)
        if m:
            return m.group(1)
    return None


def goal_interview_prompt(
    plan_path: Path, progress_path: Path, history: list[tuple[str, str]]
) -> str:
    """One round of agreeing the build's goal with the person, before any step runs.

    The flow follows .planning/harness-research/goal-elicitation.md: look first,
    open with guesses, one question at a time with lettered choices, at most five
    questions, then an approval step. The answers so far travel in *history*
    because every round is a fresh session.
    """
    asked = len(history)
    parts = [
        "[gowork goal interview — for this session only] Before this build starts, "
        "agree the goal of the build with the person. You start with no memory.",
        "",
        f"First look, quietly: the plan {plan_path}, the progress log {progress_path} "
        "if it exists, and `git log --oneline -10`. Never ask what these already answer. "
        "Only read; never edit a file here.",
        "",
        "Rules for every message:",
        "- Plain words for a non-technical person, at most 5 short sentences.",
        "- ONE question, with 4–5 lettered choices (A–E). Put your recommendation first, "
        "marked (recommended), with a one-line reason. The last choice is always "
        "'something else, in your own words'.",
        f"- At most 5 questions in total. Asked so far: {asked}.",
        "- The first question offers your 2–3 best guesses at the goal (from what you "
        "read), plus 'something has been bugging me about this' and 'I'll say it in a "
        "few words'.",
        "- Ask about the problem and what a win looks like, not about code.",
        "",
        "When you know enough (or after 5 questions), show the draft and ask for approval:",
        "Goal: <one sentence, what will be true for the person>",
        "Done when: <one check the bot can prove by running something, that the "
        "person could also check in 30 seconds>",
        "with the choices: A) approve (recommended), B) make the done test stricter, "
        "C) make the goal smaller, D) change the goal, E) start over.",
        "",
        "When they approve: change no files. End your reply with exactly these three "
        "lines — `Goal: …`, `Done when: …`, then `DONE` — and the bot writes them into "
        "the plan.",
        "Otherwise end with: ASK: <your question with its lettered choices>",
    ]
    if history:
        parts += ["", "The conversation so far:"]
        for question, answer in history:
            parts += [f'- You asked: "{question}"', f'  They answered: "{answer}"']
    return "\n".join(parts)


def step_ai_prompt(
    step: str,
    goal: str | None,
    options: list[tuple[str, str, str]],
    track: list[str] | None = None,
) -> str:
    """Ask a quick, cheap AI which of *options* should do *step*."""
    lines = [
        "Pick the AI that should do one step of a software build. Choose the cheapest, "
        "fastest one that will still do this step well: simple edits, docs and "
        "config → a fast model; tricky code, debugging or design → the most capable.",
        "",
        f"The step: {step}",
    ]
    if goal:
        lines.append(f"The build's goal: {goal}")
    lines += ["", "The AIs:"]
    for i, (harness, model, note) in enumerate(options):
        tail = f" — {note}" if note else ""
        lines.append(f"{chr(ord('A') + i)}) {harness} · {model}{tail}")
    if track:
        lines += ["", "How each AI did on past build steps (use it):", *[f"- {t}" for t in track]]
    lines += [
        "",
        "Answer with the letter first, then a few plain words why, e.g. "
        "'B — a tricky change to the login code'. Nothing else.",
    ]
    return "\n".join(lines)


_PICK_RE = re.compile(r"^\W*([A-Za-z])(?![A-Za-z])")


def parse_pick(reply: str | None, count: int) -> int | None:
    """The index the picker chose from its first letter, or None."""
    m = _PICK_RE.match(reply or "")
    if m is None:
        return None
    index = ord(m.group(1).upper()) - ord("A")
    return index if 0 <= index < count else None


#: At most this many steps of a build run at the same time.
MAX_PARALLEL = 10


def group_prompt(open_steps: list[str]) -> str:
    """Ask a quick AI which open steps can be built at the same time."""
    lines = [
        "These are the steps still to do in a software build, in order. Group the steps "
        "that can be built at the same time by different people: a step can only join "
        "a group if it doesn't need the result of any step in or before that group, and "
        "the steps in a group shouldn't change the same files. When unsure, keep a step "
        "on its own. Keep the order.",
        "",
        *[f"{i}. {step}" for i, step in enumerate(open_steps, start=1)],
        "",
        "Answer with one line only, the step numbers, commas inside a group and | between "
        "groups, e.g. `1,2 | 3 | 4,5`.",
    ]
    return "\n".join(lines)


_GROUPS_LINE_RE = re.compile(r"^[\s`]*\d+(\s*[,|]\s*\d+)*[\s`]*$")


def parse_groups(text: str | None, count: int) -> list[list[int]]:
    """``1,2 | 3`` → ``[[0, 1], [2]]``; anything odd → every step on its own."""
    alone = [[i] for i in range(count)]
    for line in (text or "").splitlines():
        if not _GROUPS_LINE_RE.match(line):
            continue
        groups = [
            [int(n) - 1 for n in part.split(",") if n.strip()]
            for part in line.strip(" `").split("|")
        ]
        if [i for g in groups for i in g] == list(range(count)):
            return groups
        return alone
    return alone


def parallel_prompt(plan_path: Path, step: str) -> str:
    """The prompt for one step of a group that runs at the same time as others."""
    parts = [
        "[gowork build worker — for this worker only] You are one of several workers "
        "building steps of a plan at the same time, each in its own copy. You start "
        "with no memory.",
    ]
    with contextlib.suppress(OSError):
        goal, done = plan_goal(plan_path.read_text(encoding="utf-8", errors="replace"))
        if goal:
            parts += [
                "",
                f"The goal of this whole build: {goal}"
                + (f" It's done when: {done}" if done else ""),
            ]
    parts += [
        "",
        f"Read the plan for context: {plan_path}",
        f"Do exactly this one step, nothing else: {step}",
        "The other steps are being built right now by others: don't do them, and don't "
        "edit the plan file or its progress log (the bot ticks the box and writes the "
        "note).",
        "Check your work actually works (run the project's tests), then commit it with "
        "git. Leave no uncommitted changes.",
        "",
        "Never push, deploy, delete data, spend money or use new API keys. If the step "
        "needs any of that, don't do it: end with STUCK and say why.",
        "",
        "Finish with one or two plain sentences on what you did, for a non-technical "
        "reader. The very last line must be exactly one of:",
        "DONE — the step is finished and committed",
        "STUCK: <plain reason> — you cannot finish it this way",
    ]
    return "\n".join(parts)


def split_prompt(plan_path: Path, step: str, why: str) -> str:
    """A step got stuck twice: replace it in the plan with smaller steps."""
    return "\n".join(
        [
            "[gowork unsticking — for this session only] One step of this build got stuck "
            f"twice, the second time on the strongest AI. The step: {step}",
            f"Why it got stuck: {why}",
            "",
            f"Read the plan {plan_path}, its progress log and `git log --oneline -10`. "
            "Replace that one step in the plan with 2–3 smaller steps, in the same place, "
            "each small enough for one fresh session and each saying how to check it. "
            "Keep the `- [ ]` format. Change nothing else, and do not do the work.",
            "If the step needs something only the person can give (a key, a login, money, "
            "a decision), don't split it: end with `STUCK: <what you need from them>`.",
            "Commit the plan and end with: PLAN: <the new steps, in a few words>",
        ]
    )


def review_step_prompt(plan_path: Path, step: str, base: str | None) -> str:
    """A different AI checks one finished step before it counts (idea 5)."""
    diff = f"`git diff {base}..HEAD`" if base else "`git log -3 -p`"
    parts = [
        "[gowork review — for this session only] You are reviewing work another AI just "
        "finished. You didn't write it. Change no files, commit nothing.",
    ]
    with contextlib.suppress(OSError):
        goal, done = plan_goal(plan_path.read_text(encoding="utf-8", errors="replace"))
        if goal:
            parts.append(f"The build's goal: {goal}" + (f" Done when: {done}" if done else ""))
    parts += [
        f"The step: {step}",
        f"The plan: {plan_path}. What changed: {diff}.",
        "",
        "Check, plainly: does the change really do the step as written, not an easier "
        "version of it? Is there a test or a check that proves it, and does it pass (run "
        "the project's tests)? Did it break or delete anything it shouldn't have?",
        "Only real problems count, not style or taste.",
        "",
        "The very last line must be exactly one of:",
        "APPROVE",
        "CHANGES: <what is wrong, in plain words, specific enough to fix>",
    ]
    return "\n".join(parts)


def hard_step_prompt(step: str, goal: str | None) -> str:
    """Ask a quick AI whether one step is hard enough to be worth a second AI's review."""
    return "\n".join(
        [
            "Is this step of a software build hard or easy? Hard: tricky logic, security, "
            "sign-in, payments, data that could be lost, many files, or easy to get "
            "subtly wrong. Easy: text, styling, docs, config, a small obvious change.",
            f"The step: {step}",
            *([f"The build's goal: {goal}"] if goal else []),
            "Answer HARD or EASY first, then a few words why. Nothing else.",
        ]
    )


def parse_hard(reply: str | None) -> bool:
    """True only for a clear HARD — anything else means no review (the cheap side)."""
    return (reply or "").strip().upper().startswith("HARD")


_REVIEW_RE = re.compile(r"^(APPROVE|CHANGES:)\s*(.*)$")


def parse_review_verdict(text: str | None) -> tuple[str, str]:
    """("approve" | "changes" | "none", detail) — "none" when the reviewer gave no verdict.

    Unlike ``parse_review``, silence is reported, not read as approval: a *required*
    review that is missing or broken must block, never accept (T16).
    """
    lines = [ln.strip().strip("*_`").strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "none", "the reviewer gave no verdict"
    m = _REVIEW_RE.match(lines[-1])
    if m is None:
        return "none", "the reviewer gave no verdict"
    if m.group(1) == "APPROVE":
        return "approve", ""
    return "changes", m.group(2).strip() or "the reviewer didn't say what"


def parse_review(text: str | None) -> str | None:
    """None for APPROVE (or no verdict at all — a broken review never blocks)."""
    lines = [ln.strip().strip("*_`").strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    m = _REVIEW_RE.match(lines[-1])
    if m is None or m.group(1) == "APPROVE":
        return None
    return m.group(2).strip() or "the reviewer didn't say what"


def untick_task(plan_path: Path, label: str) -> bool:
    """Untick the ticked task with exactly this *label* (a review sent it back)."""
    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _TASK_RE.match(line)
        if m is not None and m.group(1) != " " and m.group(2).strip() == label.strip():
            lines[i] = re.sub(r"\[[xX]\]", "[ ]", line, count=1)
            plan_path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


def tick_task(plan_path: Path, label: str) -> bool:
    """Tick the open task with exactly this *label*. False when there is none."""
    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _TASK_RE.match(line)
        if m is not None and m.group(1) == " " and m.group(2).strip() == label.strip():
            lines[i] = line.replace("[ ]", "[x]", 1)
            plan_path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


def finished_checks(plan_text: str) -> list[str]:
    """What the bot checks at the end: the goal's done test first, then "How to try it"."""
    _goal, done = plan_goal(plan_text)
    goal_check = [f"The goal is met: {done}"] if done else []
    return goal_check + plan_try_checks(plan_text)


def plan_try_checks(plan_text: str) -> list[str]:
    """The bullets under the plan's "How to try it" heading."""
    checks: list[str] = []
    inside = False
    for line in plan_text.splitlines():
        if line.lstrip().startswith("#"):
            inside = "how to try" in line.lower()
            continue
        if inside:
            m = _BULLET_RE.match(line)
            if m:
                checks.append(m.group(1))
    return checks


def is_looks_good(reply: str) -> bool:
    """True for "looks good", "yes", "keep it"… — anything else is something to fix."""
    return bool(_LOOKS_GOOD_RE.match(reply or ""))


def missing_steps_prompt(plan_path: Path, why: str) -> str:
    """Every step is done but the goal's done test failed: what steps are missing?"""
    return "\n".join(
        [
            "[gowork goal check — for this session only] Every step of this build is "
            f"done, but the build's goal isn't met yet. The check said: {why}",
            f"Read the plan {plan_path}, its progress log and `git log --oneline -10`.",
            "Propose the smallest set of new steps (at most 5) that would meet the goal, "
            "each one small enough for one fresh session, written as plan lines:",
            "- [ ] <step, in plain words, with how to check it>",
            "Change no files. After the steps, one plain sentence on why these are missing.",
            "The very last line must be: DONE",
        ]
    )


def parse_new_steps(text: str | None) -> list[str]:
    """The ``- [ ] …`` lines an AI proposed, as step labels."""
    steps: list[str] = []
    for line in (text or "").splitlines():
        m = _TASK_RE.match(line)
        if m is not None and m.group(1) == " ":
            steps.append(m.group(2).strip())
    return steps


def append_tasks(plan_path: Path, steps: list[str]) -> None:
    """Add *steps* as unticked tasks at the end of the plan."""
    text = plan_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    plan_path.write_text(text + "".join(f"- [ ] {s}\n" for s in steps), encoding="utf-8")


def append_fix_task(plan_path: Path, what: str) -> None:
    """Turn "something's off" into one more unticked task at the end of the plan."""
    text = plan_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    plan_path.write_text(text + f"- [ ] Fix: {what.strip()}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Which project a thread is about
# ---------------------------------------------------------------------------


def project_of(path: Path) -> Path:
    """A session copy (``<project>/.worktrees/wt-N``) belongs to ``<project>``."""
    parts = path.parts
    if ".worktrees" in parts:
        return Path(*parts[: parts.index(".worktrees")])
    return path


def project_for_thread(root: Path, thread_id: int) -> Path | None:
    """Find the project whose session copy is named after *thread_id*.

    Survives a cleared session: clearing forgets the thread's folder, but its
    ``.worktrees/wt-<thread_id>`` copy still says which project it was.
    """
    for candidate in root.glob(f"*/.worktrees/wt-{thread_id}"):
        if candidate.is_dir():
            return candidate.parent.parent
    return None


def list_plans_across(root: Path) -> list[Path]:
    """Unfinished plans in every project under *root*, newest first."""
    found: list[Path] = []
    for project in root.iterdir():
        if project.is_dir() and not project.name.startswith("."):
            with contextlib.suppress(OSError):
                found.extend(list_plans(project))
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# The cards: which steps need the person, and the bot checking the result itself
# ---------------------------------------------------------------------------

#: Words that mark a step the worker must stop and ask about (going live,
#: publishing, spending money, or a step that is the person's own).
_NEEDS_YOU_WORDS = (
    "tries it",
    "try it on",
    "you try",
    "deploy",
    "railway",
    "go live",
    "goes live",
    "put it live",
    "push",
    "github",
    "publish",
    "💲",
    "ask:",
)


def needs_you(task: str) -> bool:
    """True for a step the worker will stop and ask about before doing."""
    text = task.lower()
    return any(word in text for word in _NEEDS_YOU_WORDS)


def open_tasks(plan_text: str) -> list[str]:
    """The labels of every unticked task, in order."""
    labels: list[str] = []
    for line in plan_text.splitlines():
        m = _TASK_RE.match(line)
        if m is not None and m.group(1) == " ":
            labels.append(m.group(2))
    return labels


_LABEL_PREFIX_RE = re.compile(r"^\s*(?:task\s*\d+\s*[:.]\s*|[A-Z]\d+\s+)", re.IGNORECASE)


def short_label(label: str, limit: int = 80) -> str:
    """ "**A3 Brand it PropertyStack.** Name, logo…" → "Brand it PropertyStack"."""
    text = re.sub(r"\*\*|__|`", "", label)
    text = _LABEL_PREFIX_RE.sub("", text).split("_(")[0]
    text = re.split(r"\.\s|\s\(\d\)", text, maxsplit=1)[0].strip().rstrip(".")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def checker_prompt(plan_path: Path, checks: list[str]) -> str:
    """The prompt for the bot's own check of the finished work."""
    lines = [
        "You are checking finished work, not building it. You start with no memory.",
        f"The plan is {plan_path}. Its `Try:` line says how to start the project on "
        "this computer; start it if a check needs it.",
        "Do each check below yourself, like a person would: open the pages (curl, or a "
        "headless browser such as Playwright), click, type, read the result.",
        "Do not edit, commit or push anything. Do not spend money. Stop everything "
        "you started before you finish.",
        "",
        "Checks:",
        *[f"- {c}" for c in checks],
        "",
        "For each check write exactly one line, in plain words a non-technical person understands:",
        "PASS: <the check> — <what you saw>",
        "FAIL: <the check> — <what went wrong>",
        "SKIP: <the check> — <why you couldn't check it, e.g. it needs a real Google "
        "login or would cost money>",
        "The very last line must be: DONE",
    ]
    return "\n".join(lines)


_RESULT_RE = re.compile(r"^(PASS|FAIL|SKIP):\s*(.+)$", re.IGNORECASE)


def parse_check_results(text: str | None) -> list[tuple[str, str]]:
    """PASS/FAIL/SKIP lines from the checker → [("pass", "Open the page — ok"), …]."""
    results: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        m = _RESULT_RE.match(line.strip().strip("*_`").strip())
        if m:
            results.append((m.group(1).lower(), m.group(2).strip().strip("*_`").strip()))
    return results


# ---------------------------------------------------------------------------
# A stopped build waits: keep going, skip the step, or throw it away
# ---------------------------------------------------------------------------

_SKIP_RE = re.compile(r"^\s*skip\b", re.IGNORECASE)
_THROW_RE = re.compile(
    r"(throw (it |this )?away|scrap( it)?|cancel( it)?|give up|delete (it|the build))",
    re.IGNORECASE,
)


_FINISH_RE = re.compile(
    r"(wrap (it |this )?up|keep what'?s (done|finished)|finish (it |this )?(here|now)|"
    r"close (it |this )?out|stop here and keep|end it here)",
    re.IGNORECASE,
)
_FILLER_START = re.compile(r"^((ok(ay)?|yes|yeah|just|please|let'?s|lets)[,\s]+)+", re.IGNORECASE)
_FILLER_END = re.compile(r"([,\s]+(please|now|then|thanks))+$", re.IGNORECASE)


def clear_reply(reply: str) -> str:
    """The reply without filler ("ok", "please", punctuation), lower-cased.

    Actions that end or delete a build only fire when this *whole* string is
    the command. Anything longer is a sentence, and the AI reads sentences.
    """
    text = (reply or "").strip().lower().rstrip(".!?")
    text = _FILLER_START.sub("", text)
    return _FILLER_END.sub("", text).strip()


_KEEP_RE = re.compile(r"^\s*(keep going|continue|try again|go on|resume|retry|go)\b", re.IGNORECASE)


def parked_choice(reply: str) -> str | None:
    """What the person wants for a stopped build, or None when it isn't an answer.

    "keep" (try the step again), "skip", "finish" (keep the finished steps in the
    project and end), or "throw" (delete the build). Anything else — "can we do
    plan 6 now?" — is not an answer, so it goes to the normal chat instead.
    """
    text = reply or ""
    said = clear_reply(text)
    if _THROW_RE.fullmatch(said):
        return "throw"
    if _FINISH_RE.fullmatch(said):
        return "finish"
    if _SKIP_RE.match(text):
        return "skip"
    if _KEEP_RE.match(text):
        return "keep"
    return None


def skip_task(plan_path: Path) -> None:
    """Tick the first open step, marked as skipped, so the build moves past it."""
    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _TASK_RE.match(line)
        if m is not None and m.group(1) == " ":
            ending = "\n" if line.endswith("\n") else ""
            lines[i] = line.rstrip("\n").replace("[ ]", "[x]", 1) + " _(skipped)_" + ending
            break
    plan_path.write_text("".join(lines), encoding="utf-8")
