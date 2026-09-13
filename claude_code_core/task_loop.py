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


def usage_limit_message(text: str | None, error: str | None) -> str | None:
    """The provider's "you've hit your limit" line, or None for an ordinary result.

    A limit says nothing about the task, so it must never count as a failed try.
    A reply that ends with a status line is real work, even if it mentions limits.
    """
    candidates = [error or ""]
    if parse_status(text)[0] is Status.NONE:
        candidates.append(text or "")
    for blob in candidates:
        for line in blob.splitlines():
            if _LIMIT_RE.search(line):
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

    def resume(self) -> None:
        """Clear a stop request so the next ``run()`` carries on."""
        self._stop = False

    async def run(self) -> LoopOutcome:
        answer: tuple[str, str] | None = None
        retry_reason: str | None = None
        retries = 0
        rounds = 0
        while True:
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
                if await self._on_limit(limit):
                    continue
                return LoopOutcome(Status.ASK, f"usage limit: {limit}", rounds)
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
            if status == Status.PAUSE:
                await _git(self.repo_dir, "add", "-A")
                await _git(self.repo_dir, "commit", "-qm", "gowork: paused")
                return LoopOutcome(Status.PAUSE, detail or "paused", rounds)
            if status in (Status.SKIP, Status.PLAN):
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


def review_prompt(plan_path: Path, progress_path: Path, reply: str, fails: list[str]) -> str:
    """One round that reads the person's reply to the finished card and acts on it."""
    failed = "; ".join(fails) if fails else "none"
    return "\n".join(
        [
            "[gowork build worker — for this worker only] Every task in this build is "
            "done and the person was shown the finished card. They replied to the "
            f'finished build: "{reply}"',
            "",
            f"The plan is {plan_path}; the progress log is {progress_path}. Checks that "
            f"failed at the end: {failed}.",
            "",
            "Work out what they want and do only that:",
            "- A question: answer it in plain words from the plan, the progress log and "
            "the git log. Change nothing. End with `PAUSE: answered`.",
            "- Changes or fixes: add them as new unchecked tasks (`- [ ]`) at the end of "
            "the plan, commit, and end with `PLAN: <what you added>`. Don't do the work yet.",
            "- They're happy and want to keep it: end with `DONE`.",
            "- Unclear: explain your reading with a concrete example and end with "
            "`PAUSE: <your question>`.",
            "Never push, deploy or spend money.",
        ]
    )


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
    r"\b(throw (it |this )?away|scrap( it)?|cancel|give up|delete (it|the build))\b",
    re.IGNORECASE,
)


_FINISH_RE = re.compile(
    r"\b(wrap (it |this )?up|keep what'?s (done|finished)|finish (it |this )?(here|now)|"
    r"close (it |this )?out|stop here and keep|end it here)\b",
    re.IGNORECASE,
)
_KEEP_RE = re.compile(r"^\s*(keep going|continue|try again|go on|resume|retry|go)\b", re.IGNORECASE)


def parked_choice(reply: str) -> str | None:
    """What the person wants for a stopped build, or None when it isn't an answer.

    "keep" (try the step again), "skip", "finish" (keep the finished steps in the
    project and end), or "throw" (delete the build). Anything else — "can we do
    plan 6 now?" — is not an answer, so it goes to the normal chat instead.
    """
    text = reply or ""
    if _THROW_RE.search(text):
        return "throw"
    if _FINISH_RE.search(text):
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
