"""TaskLoopCog — run a plan one task at a time, a fresh session per task.

The Discord side of :mod:`claude_code_core.task_loop`. A planner (a person with
``/gowork``, or a planner session via ``POST /api/loops`` after the
person said yes) points it at a plan file. The cog opens one worker thread next
to the planner and runs rounds there: each round is a new session on whatever
harness the thread uses, so the loop works the same on Claude Code, Codex and
DSH. Progress lines go back to where the loop was started.

Nothing here uses buttons. Every question is a plain message, and whatever the
person types next in that channel or thread is the answer — Drew answers in his
own words ("claude sonnet", "yes", "what does that mean"), and the flow adapts
to that rather than the other way round. Typing in a worker thread while a task
runs is passed to the next task as a note instead of starting a side chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from claude_code_core.build_queue import BuildQueue, QueueItem, morning_summary
from claude_code_core.gowork_records import (
    append_record,
    lessons_prompt,
    read_records,
    track_record,
)
from claude_code_core.loop_store import LoopRecord, LoopStore
from claude_code_core.task_loop import (
    LoopOutcome,
    Status,
    TaskLoop,
    append_fix_task,
    append_tasks,
    checker_prompt,
    clear_reply,
    count_tasks,
    finished_checks,
    first_unchecked,
    goal_interview_prompt,
    group_prompt,
    is_looks_good,
    list_plans,
    list_plans_across,
    merge_open_tasks,
    missing_steps_prompt,
    needs_you,
    open_tasks,
    parallel_prompt,
    parked_choice,
    parse_check_results,
    parse_groups,
    parse_new_steps,
    parse_pick,
    parse_review,
    parse_status,
    plan_check_command,
    plan_goal,
    project_for_thread,
    project_of,
    review_step_prompt,
    run_check,
    short_label,
    skip_task,
    split_prompt,
    step_ai_prompt,
    take_snapshot,
    tick_task,
)
from claude_code_core.work_copy import (
    DEFAULT_ROOT,
    WorkCopy,
    WorkCopyError,
    commit_all,
    create_side_copy,
    create_work_copy,
    keep_work,
    merge_side_copy,
    remove_side_copy,
    remove_work_copy,
    side_has_new_work,
)

from ..backend_settings import ALL_BACKENDS

if TYPE_CHECKING:
    from .claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

#: How long a question waits for a typed reply before the loop pauses.
ASK_TIMEOUT_SECONDS = 12 * 60 * 60
#: A finished build waits for "looks good" as long as it takes; this is how
#: often it reminds the person that it is still waiting.
VERDICT_REMIND_SECONDS = 24 * 60 * 60
#: How long starting a new plan waits for the project's open build to close.
SWITCH_TIMEOUT_SECONDS = 45 * 60
#: How long starting a build waits for the harness/model or plan reply.
PICK_TIMEOUT_SECONDS = 10 * 60
#: After "wait" on a usage limit, try the same AI again this much later.
LIMIT_WAIT_SECONDS = 30 * 60
#: The start list's first choice: a quick AI picks the AI for each step.
PER_STEP = "per-step"
#: How long the quick step-AI picker may take before the build keeps its AI.
PICK_AI_TIMEOUT_SECONDS = 60
#: The build queue's morning summary posts after this hour (local time).
MORNING_HOUR = 8
#: When the goal isn't met, add the missing steps and keep going this many times.
GOAL_AUTO_ROUNDS = 3
#: The goal interview: up to 5 questions, the approval, and a couple of changes.
GOAL_INTERVIEW_ROUNDS = 9
#: Replies to a usage limit that mean "wait for it to reset".
_WAIT_WORDS = {"wait", "a", "wait for it", "wait for reset", "same", "later"}

#: Report lines that need the person: a question, a stop, the end. Progress
#: ("✅ Task 3 of 9 done") posts quietly — Drew chose pings only when needed.
_PING_PREFIXES = ("❓", "🛑", "🏁", "⏸️", "💥")

#: Words that name a harness in a typed reply.
_HARNESS_WORDS = {
    "claude": "claude",
    "codex": "codex",
    "dsh": "dsh",
    "deepseek": "dsh",
    "local": "local",
    "ollama": "local",
    "agui": "agui",
}
_SAME_WORDS = {"same", "current", "default", "whatever"}
#: Typed where a build was started, ends that channel's chat session.
_CLOSE_WORDS = {"close", "close session", "close this session"}
_FILLER = {"use", "with", "please", "the", "model", "and", "on", "a", "it", "for", "harness"}
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]*")


def parse_harness_reply(text: str, current: str | None) -> tuple[str, str | None] | None:
    """Read "claude sonnet", "dsh deepseek-pro", "codex" or "same" from a typed reply."""
    words = _WORD_RE.findall(text.lower())
    if words and words[0] in _SAME_WORDS and current:
        return current, None
    for i, word in enumerate(words):
        backend = _HARNESS_WORDS.get(word)
        if backend is None or backend not in ALL_BACKENDS:
            continue
        rest = [w for w in words[i + 1 :] if w not in _FILLER]
        return backend, (rest[0] if rest else None)
    return None


class PickDeclinedError(Exception):
    """The person typed something other than a choice, so the normal chat answers it."""


def choice_letter(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA … — however many choices there are."""
    letters = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


_LETTER_REPLY_RE = re.compile(r"^\s*(?:option\s+)?\(?([a-z]{1,2})\)?[\s.!)]*$", re.IGNORECASE)


def parse_plan_reply(text: str, plans: list[Path]) -> Path | None:
    """A typed letter ("B", "b)", "option c") → that plan."""
    m = _LETTER_REPLY_RE.match(text or "")
    if not m:
        return None
    wanted = m.group(1).upper()
    for i, plan in enumerate(plans):
        if choice_letter(i) == wanted:
            return plan
    return None


def _chunks(lines: list[str], limit: int = 1900) -> list[str]:
    """Split a long list into Discord-sized messages without breaking a line."""
    out: list[str] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > limit:
            out.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        out.append(current)
    return out


_BLUE, _GREEN, _AMBER = 0x2D6A86, 0x2F7D4F, 0xE8A317
_MARK = {"pass": "✅", "fail": "❌", "skip": "⚪"}
_FIX_WORDS = {"fix", "fix it", "fix them", "fix those", "fix that"}
_ADD_WORDS = {"add them", "add", "add those", "add these", "yes add them", "add the steps"}


_STOP_WORDS = {
    "close",
    "close it",
    "close this",
    "stop",
    "stop it",
    "wrap up",
    "wrap it up",
    "end it",
    "pause",
    "save and close",
    "save and stop",
}


def wants_close(text: str) -> bool:
    """Typed in a build's thread: keep the finished steps and end the build.

    Only the bare command counts. A longer sentence ("save what you have and
    close") goes to the AI, which saves and ends with PAUSE.
    """
    return clear_reply(text) in _STOP_WORDS


def _match_ai(
    reply: str, options: list[tuple[str, str, str]], current: str | None, first: int = 1
) -> tuple[str, str | None] | None:
    """A lettered choice from *options* (letter *first* is the first) or a typed AI name."""
    m = _LETTER_REPLY_RE.match(reply)
    if m is not None:
        wanted = m.group(1).upper()
        for i, (harness, model, _note) in enumerate(options, start=first):
            if choice_letter(i) == wanted:
                return harness, model
    return parse_harness_reply(reply, current)


def plan_card(
    plan_text: str,
    plan_name: str,
    harness: str | None,
    model: str | None,
    groups: list[list[str]] | None = None,
) -> Any:
    """The "here's what I'm going to do" card: every step left, in plain words."""
    ai = " · ".join(x for x in (harness, model) if x) or "the thread's usual AI"
    lines = [f"**Plan:** {plan_name}", f"**AI doing the work:** {ai}", ""]
    for task in open_tasks(plan_text):
        dot = "🟡" if needs_you(task) else "🟢"
        tail = " — I'll ask you first" if dot == "🟡" else ""
        lines.append(f"{dot} **{short_label(task)}**{tail}")
    together = [g for g in groups or [] if len(g) > 1]
    if together:
        lines += ["", "⚡ **Built at the same time:**"]
        lines += [f"• {' + '.join(short_label(s, 40) for s in g)}" for g in together]
    lines += ["", "🟢 I do it by myself   🟡 I stop and ask you"]
    return discord.Embed(
        title="📋 Here's what I'm going to do",
        description=_fit("\n".join(lines)),
        color=_BLUE,
    )


def finished_card(
    name: str,
    done: int,
    recaps: list[str],
    results: list[tuple[str, str]],
    proposed: list[str] | None = None,
    lessons: list[str] | None = None,
) -> Any:
    """The last card: what got done, what the bot checked itself, what to type."""
    fails = [r for r in results if r[0] == "fail"]
    lines = [f"**All {done} steps are done.**"]
    if recaps:
        lines += ["", "**What got done**", *[f"• {r}" for r in recaps[-10:]]]
    if results:
        lines += ["", "**What I checked myself**"]
        lines += [f"{_MARK.get(kind, '⚪')} {what}" for kind, what in results]
    if lessons:
        lines += ["", "**Next time**", *[f"• {b}" for b in lessons]]
    lines.append("")
    if proposed:
        lines += [
            "🎯 **The goal isn't met yet.** These steps would get there:",
            *[f"• {step}" for step in proposed],
            "Type **add them** to add these steps and keep going.",
            "",
        ]
    if fails:
        lines.append(
            f"**{len(fails)} check{'s' if len(fails) > 1 else ''} failed.** Type **fix** and "
            "I'll fix it, or **looks good** to keep it anyway."
        )
    else:
        lines.append("Type **looks good** to keep it.")
    lines.append(
        "This thread is a normal chat now: ask me anything, test it with me, or ask for changes."
    )
    return discord.Embed(
        title=f"🏁 {name} is finished",
        description=_fit("\n".join(lines)),
        color=_AMBER if fails else _GREEN,
    )


def _fit(text: str, limit: int = 4000) -> str:
    """Embeds hold 4,096 characters; cut at a line, never mid-word."""
    if len(text) <= limit:
        return text
    return text[: text.rfind("\n", 0, limit - 2)] + "\n…"


@dataclass
class _Running:
    loop: TaskLoop
    repo_dir: Path
    worker_thread_id: int
    report_channel_id: int
    copy: WorkCopy | None = None
    task: asyncio.Task[LoopOutcome] | None = None
    thread: Any = None
    report_target: Any = None
    report: Any = None
    notify_user_id: int | None = None
    #: One plain-English line per finished task, for the summary at the end.
    recaps: list[str] | None = None
    #: Runs one fresh session in the worker thread (used for the bot's own check).
    run_session: Any = None
    #: True while the finished card waits for "looks good" or a fix.
    in_review: bool = False
    #: A new plan was started in this project: close this build by itself,
    #: keeping its finished steps, instead of asking.
    auto_finish: bool = False
    #: Wakes a stopped build that is waiting for the person (see auto_finish).
    wake: asyncio.Event | None = None
    #: Agree a goal with the person first when the plan has none.
    ask_goal: bool = False
    #: A quick AI picks the AI for every step.
    per_step_ai: bool = False
    #: AIs that hit a usage limit during this build; the picker skips them.
    limited: set[str] = field(default_factory=set)
    #: How far unsticking went per step: 1 = stronger AI tried, 2 = split tried.
    unstuck: dict[str, int] = field(default_factory=dict)
    #: (step, harness, model) while a stuck step runs on a stronger AI.
    boosted: tuple[str, str, str | None] | None = None
    #: Started from the build queue; True while it waits for the person.
    queued: bool = False
    waiting_for_person: bool = False
    #: Rounds of missing steps added by themselves when the goal wasn't met.
    goal_rounds: int = 0
    #: This build's step records, and when the current round started and on which AI.
    records: list[dict[str, Any]] = field(default_factory=list)
    round_started: tuple[float, str] | None = None
    #: The step groups the quick AI made (labels), for parallel steps.
    groups: list[list[str]] = field(default_factory=list)
    #: On a usage limit, switch to this AI by itself instead of asking.
    fallback: tuple[str, str | None] | None = None


def _recap_line(text: str | None) -> str | None:
    """ "Task 3: CLI — What happened: …" from a worker's plain-English recap."""
    title = happened = None
    for line in (text or "").splitlines():
        clean = line.strip().strip("*").strip()
        if title is None and clean.lower().startswith("task "):
            title = clean.split("—")[0].strip()
        if happened is None and clean.lower().startswith("what happened:"):
            happened = clean.split(":", 1)[1].strip()
    if not (title or happened):
        return None
    return (f"{title}: {happened}" if title and happened else title or happened or "")[:200]


async def _restore_tracked(path: Path) -> None:
    """Throw away edits to tracked files in the build's own copy (never the project)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
        "checkout",
        "--",
        ".",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def resolve_repo(plan_path: Path) -> Path:
    """Validate *plan_path* and return the git repository that contains it."""
    if not plan_path.is_absolute():
        raise ValueError("plan_path must be an absolute path")
    if plan_path.suffix.lower() != ".md" or not plan_path.is_file():
        raise ValueError(f"plan file not found (must be a .md file): {plan_path}")
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(plan_path.parent),
        "rev-parse",
        "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0 or not out.strip():
        raise ValueError(f"the plan must live inside a git repository: {plan_path}")
    return Path(out.decode().strip())


class TaskLoopCog(commands.Cog):
    """``/gowork`` / ``/stopwork`` and the engine behind ``POST /api/loops``."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        allowed_user_ids: set[int] | None = None,
        work_root: Path | None = None,
        store: LoopStore | None = None,
    ) -> None:
        self.bot = bot
        self._allowed_user_ids = allowed_user_ids
        #: Where each build's own copy of the project is made (None = default).
        self._work_root = work_root
        self._running: dict[Path, _Running] = {}
        #: Running builds on disk, so a bot restart resumes them.
        self._store = store or LoopStore()
        #: Channels/threads waiting for the person's next typed message.
        self._waiters: dict[int, asyncio.Future[str]] = {}
        #: Waiters that only take a matching reply; anything else goes to the chat.
        self._accepts: dict[int, Any] = {}
        #: A stuck step tries a stronger AI, then smaller steps, before asking (idea 4).
        self.smart_unstick = True
        #: A different AI reviews every finished step before it counts (idea 5).
        self.smart_review = True
        #: Every step of every build, for the picker's track record (idea 7).
        self._records_path = (work_root or DEFAULT_ROOT) / "step-records.jsonl"
        #: Builds waiting in line (idea 6), and where each asked to be reported.
        self._queue = BuildQueue((work_root or DEFAULT_ROOT) / "queue.json")
        self._queue_reports: dict[int, Any] = {}
        self._queue_lock = asyncio.Lock()

    def _chat(self) -> ClaudeChatCog:
        cog: Any = self.bot.cogs.get("ClaudeChatCog")
        if cog is None:
            raise RuntimeError("ClaudeChatCog is not loaded")
        return cog

    @property
    def running(self) -> list[_Running]:
        return list(self._running.values())

    async def wait_for_reply(
        self, channel_id: int, *, timeout: float, accept: Any = None
    ) -> str | None:
        """Wait for the next message typed in *channel_id* (see take_message).

        With *accept*, a reply it rejects is left for the normal chat and this
        raises PickDeclinedError, so a question typed instead of a choice gets answered.
        """
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._waiters[channel_id] = future
        if accept is not None:
            self._accepts[channel_id] = accept
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            if self._waiters.get(channel_id) is future:
                self._waiters.pop(channel_id, None)
                self._accepts.pop(channel_id, None)

    def take_message(self, message: Any) -> bool:
        """Claim a typed message for /gowork. True means the chat cog must ignore it.

        A pending question takes it as the answer. In a worker thread with no
        question pending, it becomes a note for the next task, so it never starts
        a side chat that races the loop.
        """
        channel_id = getattr(message.channel, "id", None)
        text = (getattr(message, "content", "") or "").strip()
        if channel_id is None or not text:
            return False
        for running in self._running.values():
            if running.worker_thread_id == channel_id and wants_close(text):
                self._close_build(running)
                return True
        future = self._waiters.get(channel_id)
        if future is not None and not future.done():
            accept = self._accepts.get(channel_id)
            if accept is not None and not accept(text):
                future.set_exception(PickDeclinedError(text))
                return False
            future.set_result(text)
            return True
        for running in self._running.values():
            if running.worker_thread_id == channel_id:
                running.loop.add_note(text)
                with contextlib.suppress(Exception):
                    asyncio.get_running_loop().create_task(
                        message.channel.send("-# 📝 Got it, I'll pass that to the next task.")
                    )
                return True
            if running.report_channel_id == channel_id and text.lower() in _CLOSE_WORDS:
                # "close" where the build was started ends that chat session, so
                # it stops chiming in; the build keeps going in its own thread.
                with contextlib.suppress(Exception):
                    asyncio.get_running_loop().create_task(
                        self._chat().close_session(message.channel)
                    )
                return True
        return False

    def _close_build(self, running: _Running) -> None:
        """ "close" in the build's thread: finish the round, keep what's done, end."""
        running.auto_finish = True
        running.loop.request_stop()
        running.wake = running.wake or asyncio.Event()
        running.wake.set()
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().create_task(
                running.thread.send(
                    "-# 🗂️ Closing: I'll keep the finished steps in the project and end "
                    "this build once the current step stops."
                )
            )

    async def start_loop(
        self,
        channel: discord.TextChannel,
        plan_path: str,
        *,
        report_to: discord.abc.Messageable | None = None,
        notify_user_id: int | None = None,
        harness: str | None = None,
        model: str | None = None,
        fallback_harness: str | None = None,
        fallback_model: str | None = None,
        ask_goal: bool = False,
        per_step_ai: bool = False,
        queued: bool = False,
    ) -> discord.Thread:
        """Open the worker thread and start the loop in the background."""
        plan = Path(plan_path).expanduser()
        repo_dir = await resolve_repo(plan)
        if repo_dir in self._running:
            raise ValueError(f"a task loop is already running in {repo_dir}")
        chat = self._chat()
        snap = await take_snapshot(repo_dir, plan)
        if snap.checked + snap.unchecked == 0:
            raise ValueError("the plan has no `- [ ]` tasks to work through")

        # The build works in its own copy: the real project is untouched until
        # Drew has tried the result, and other sessions in the same folder
        # can't collide with it.
        try:
            copy = await create_work_copy(repo_dir, plan, root=self._work_root)
        except WorkCopyError as exc:
            raise ValueError(f"couldn't make a separate copy of the project: {exc}") from exc
        work_dir = copy.path

        thread = await chat.spawn_session(
            channel,
            f"🔁 **Task loop** for `{plan}`\n"
            f"{snap.unchecked} of {snap.checked + snap.unchecked} tasks left. "
            "Each task runs in a fresh session on a separate copy of the project; "
            "I'll ask here if I need you; just type your answer.",
            thread_name=f"🔁 Task loop · {repo_dir.name}",
            auto_start=False,
            working_dir=str(work_dir),
        )
        # The harness and model picked at start stick to the worker thread for
        # every round (per-thread settings, so other threads are unaffected).
        settings = getattr(chat, "_backend_settings", None)
        if settings is not None and harness:
            await settings.set_backend(harness, thread_id=thread.id)
            if model:
                await settings.set_model(harness, model, thread_id=thread.id)
        report_target: discord.abc.Messageable = report_to or channel
        record = LoopRecord(
            repo_dir=str(repo_dir),
            plan_path=str(plan),
            copy_path=str(copy.path),
            copy_plan=str(copy.plan_path),
            branch=copy.branch,
            worker_thread_id=thread.id,
            report_channel_id=getattr(report_target, "id", channel.id),
            notify_user_id=notify_user_id,
            harness=harness,
            model=model,
            fallback_harness=fallback_harness,
            fallback_model=fallback_model,
            ask_goal=ask_goal,
            per_step_ai=per_step_ai,
            queued=queued,
        )
        self._store.save(record)
        plan_text = copy.plan_path.read_text(encoding="utf-8", errors="replace")
        groups = await self._groups_for(open_tasks(plan_text))
        self._launch(record, thread, report_target)
        self._running[repo_dir].groups = groups  # set before the loop's first step
        with contextlib.suppress(discord.HTTPException):
            await report_target.send(
                f"▶️ Started. Everything about this build happens in {thread.mention}: each "
                "step, and any question for you (I'll ping you there). This channel only "
                "gets the final result.",
                embed=plan_card(
                    plan_text,
                    plan.name,
                    harness or ("the best AI per step" if per_step_ai else None),
                    model,
                    groups,
                ),
            )
        return thread

    def _quiet(self, thread_id: int) -> None:
        """Worker threads get no start-fresh nudge and no reply-needed ping."""
        chat: Any = self.bot.cogs.get("ClaudeChatCog")
        nudger = getattr(chat, "context_nudger", None)
        if nudger is not None:
            nudger.skip_thread_ids.add(thread_id)
        dashboard = None
        with contextlib.suppress(Exception):
            dashboard = chat._get_dashboard()
        if dashboard is not None:
            dashboard.quiet_thread_ids.add(thread_id)

    def _launch(self, record: LoopRecord, thread: Any, report_target: Any) -> Any:
        """Run *record*'s loop in *thread*. Returns the report function."""
        chat = self._chat()
        work_dir, work_plan = Path(record.copy_path), Path(record.copy_plan)
        notify_user_id = record.notify_user_id or self._only_user()

        async def report(text: str) -> None:
            ping = (
                f" <@{notify_user_id}>"
                if notify_user_id and text.startswith(_PING_PREFIXES)
                else ""
            )
            # Everything about the build is said in the build's own thread; the
            # starting channel only gets the start and the final result.
            with contextlib.suppress(discord.HTTPException):
                await thread.send(f"{text}{ping}")

        rounds = 0
        recaps: list[str] = []

        async def run_round(prompt: str) -> tuple[str | None, str | None]:
            nonlocal rounds
            rounds += 1
            now = await take_snapshot(work_dir, work_plan)
            if holder and holder[0].per_step_ai and now.next_task:
                await self._pick_step_ai(holder[0], now.next_task)
            if holder:
                holder[0].round_started = (time.monotonic(), await self._ai_label(holder[0]))
            seed = await thread.send(f"-# 🔁 Round {rounds} · next task: {now.next_task}")
            result: dict[str, str | None] = {}

            async def sink(text: str | None, error: str | None) -> None:
                result["text"], result["error"] = text, error

            await chat.run_fresh_turn(
                seed, thread, prompt, working_dir=str(work_dir), result_sink=sink
            )
            if not result:
                return None, "the session ended without a result"
            recap = _recap_line(result.get("text"))
            if recap:
                recaps.append(recap)
            if parse_status(result.get("text"))[0] == Status.DONE:
                # The bot's own check can take a while; say so, so it never looks stuck.
                with contextlib.suppress(discord.HTTPException):
                    await thread.send("-# 🔎 Checking the work, then I'll start the next step…")
            return result.get("text"), result.get("error")

        async def run_session(prompt: str, label: str) -> tuple[str | None, str | None]:
            seed = await thread.send(f"-# {label}")
            result: dict[str, str | None] = {}

            async def sink(text: str | None, error: str | None) -> None:
                result["text"], result["error"] = text, error

            await chat.run_fresh_turn(
                seed, thread, prompt, working_dir=str(work_dir), result_sink=sink
            )
            return result.get("text"), result.get("error") or (
                None if result else "the session ended without a result"
            )

        holder: list[_Running] = []

        async def ask(question: str) -> str | None:
            mention = f"<@{notify_user_id}> " if notify_user_id else ""
            with contextlib.suppress(discord.HTTPException):
                await thread.send(f"❓ {mention}{question}\n-# Just type your answer here.")
            if not holder:
                return await self.wait_for_reply(thread.id, timeout=ASK_TIMEOUT_SECONDS)
            self._queue_waiting(holder[0], f"waiting for your answer: {question[:150]}")
            reply, _woken = await self._wait_or_wake(holder[0], thread.id, ASK_TIMEOUT_SECONDS)
            holder[0].waiting_for_person = False
            self._queue_note(holder[0], "running")
            return reply  # None when a new plan closes this build

        real_plan = Path(record.plan_path)

        def read_real() -> str | None:
            try:
                return real_plan.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None

        last_seen = [read_real()]

        async def pull_plan_changes() -> None:
            """The planning session edits the real plan; bring its open steps across."""
            if holder:
                await self._end_boost(holder[0])
            real_text = read_real()
            if real_text is None or real_text == last_seen[0]:
                return
            last_seen[0] = real_text
            copy_text = work_plan.read_text(encoding="utf-8", errors="replace")
            merged = merge_open_tasks(copy_text, real_text)
            if merged is None:
                return
            work_plan.write_text(merged, encoding="utf-8")
            await commit_all(work_dir, "gowork: plan changes from the planning session")
            with contextlib.suppress(discord.HTTPException):
                await thread.send("-# 📝 Picked up changes to the steps from the planning session.")

        async def on_limit(message: str) -> bool:
            return await self._limit_hit(holder[0], message) if holder else False

        loop = TaskLoop(
            plan_path=work_plan,
            repo_dir=work_dir,
            run_round=run_round,
            ask=ask,
            report=report,
            on_limit=on_limit,
            before_round=pull_plan_changes,
            next_group=lambda steps: self._next_group(holder[0], steps),
            on_result=lambda step, result, detail: self._record(holder[0], step, result, detail),
            review=lambda step, base: self._review_step(holder[0], step, base),
            run_group=lambda steps: self._run_group(holder[0], steps),
        )
        repo_dir = Path(record.repo_dir)
        copy = WorkCopy(
            source_repo=repo_dir, path=work_dir, branch=record.branch, plan_path=work_plan
        )
        running = _Running(
            loop,
            repo_dir,
            thread.id,
            record.report_channel_id,
            copy=copy,
            thread=thread,
            report_target=report_target,
            report=report,
            notify_user_id=notify_user_id,
            recaps=recaps,
            run_session=run_session,
            ask_goal=record.ask_goal,
            per_step_ai=record.per_step_ai,
            queued=record.queued,
            fallback=(
                (record.fallback_harness, record.fallback_model)
                if record.fallback_harness
                else None
            ),
        )
        holder.append(running)
        self._running[repo_dir] = running
        self._quiet(thread.id)
        running.task = asyncio.create_task(self._drive(running, report))
        return report

    async def resume_all(self) -> int:
        """Pick up every build that was running when the bot stopped."""
        resumed = 0
        for record in self._store.all():
            repo_dir = Path(record.repo_dir)
            if repo_dir in self._running:
                continue
            thread: Any = self.bot.get_channel(record.worker_thread_id)
            if thread is None:
                with contextlib.suppress(Exception):
                    thread = await self.bot.fetch_channel(record.worker_thread_id)
            if thread is None or not Path(record.copy_plan).is_file():
                logger.warning("gowork: can't resume %s (thread or copy gone)", repo_dir)
                self._store.remove(record.repo_dir)
                continue
            report_target: Any = (
                self.bot.get_channel(record.report_channel_id)
                or getattr(thread, "parent", None)
                or thread
            )
            snap = await take_snapshot(Path(record.copy_path), Path(record.copy_plan))
            total = snap.checked + snap.unchecked
            report = self._launch(record, thread, report_target)
            await report(
                f"🔁 Resuming after a restart: Task {min(snap.checked + 1, total)} of {total}"
            )
            resumed += 1
        return resumed

    async def cog_load(self) -> None:
        async def resume_when_ready() -> None:
            with contextlib.suppress(Exception):
                await self.bot.wait_until_ready()
                await self.resume_all()
                await self._advance_queue()
            while True:  # the morning summary
                await asyncio.sleep(300)
                with contextlib.suppress(Exception):
                    await self._maybe_morning_summary(datetime.datetime.now())

        self._resume_task = asyncio.create_task(resume_when_ready())

    async def _drive(self, running: _Running, report: Any) -> LoopOutcome:
        try:
            if running.ask_goal:
                await self._goal_interview(running)
            while True:
                outcome = await running.loop.run()
                if outcome.status == Status.COMPLETE:
                    if await self._wrap_up(running) == "fix":
                        continue
                    break
                # Stuck, paused or stopped: the build waits instead of being
                # forgotten, so "keep going" picks up where it left off.
                handled, outcome = await self._unstick(running, outcome)
                if handled:
                    continue
                if await self._park(running, outcome) == "gone":
                    break
            self._store.remove(str(running.repo_dir))
            return outcome
        except asyncio.CancelledError:
            # Bot shutting down: keep the record so startup resumes this build.
            raise
        except discord.NotFound:
            # The worker thread was deleted: end here, keeping the finished steps.
            logger.info("gowork: worker thread gone for %s, wrapping up", running.repo_dir)
            running.auto_finish = True
            with contextlib.suppress(Exception):
                await self._finish_early(running)
            self._store.remove(str(running.repo_dir))
            return LoopOutcome(Status.NONE, "worker thread deleted")
        except Exception as exc:
            logger.exception("task loop crashed in %s", running.repo_dir)
            self._store.remove(str(running.repo_dir))
            with contextlib.suppress(Exception):
                await running.report_target.send(f"💥 The build crashed: {exc}")
            raise
        finally:
            self._running.pop(running.repo_dir, None)
            if running.queued:
                asyncio.get_running_loop().create_task(self._advance_queue())

    async def _goal_interview(self, running: _Running) -> None:
        """A plan with no goal: agree one with the person in the build's thread first.

        Each round is a fresh session that asks one lettered question; the answers
        so far travel in the prompt. Ends when the approved goal is in the plan,
        or quietly when nobody answers — the build then runs without a goal.
        """
        assert running.copy is not None
        plan = running.copy.plan_path

        def goal_now() -> tuple[str | None, str | None]:
            return plan_goal(plan.read_text(encoding="utf-8", errors="replace"))

        if goal_now()[0]:
            return
        progress = plan.with_name(f"{plan.stem}.progress.md")
        history: list[tuple[str, str]] = []
        for _ in range(GOAL_INTERVIEW_ROUNDS):
            if running.run_session is None:
                return
            text, _error = await running.run_session(
                goal_interview_prompt(plan, progress, history), "🎯 Working out the goal…"
            )
            goal, done = goal_now()
            if goal:
                await commit_all(running.copy.path, "gowork: the build's goal")
                with contextlib.suppress(discord.HTTPException):
                    await running.thread.send(
                        f"🎯 **Goal:** {goal}\n**Done when:** {done or '—'}\n-# Starting the steps."
                    )
                return
            status, detail = parse_status(text)
            if status != Status.ASK or not detail:
                return  # the interview didn't work out; build without a goal
            reply, _woken = await self._wait_or_wake(
                running, running.worker_thread_id, ASK_TIMEOUT_SECONDS
            )
            if reply is None:
                return
            history.append((detail, reply))

    async def _missing_steps(self, running: _Running, fails: list[str]) -> list[str]:
        """When the goal's done test failed, ask a fresh session which steps are missing."""
        assert running.copy is not None
        why = next((f for f in fails if f.lower().startswith("the goal is met")), None)
        if why is None or running.run_session is None:
            return []
        try:
            text, _error = await running.run_session(
                missing_steps_prompt(running.copy.plan_path, why),
                "🎯 The goal isn't met yet; working out what's missing…",
            )
        except Exception:
            logger.warning("gowork: couldn't work out the missing steps", exc_info=True)
            return []
        return parse_new_steps(text)[:5]

    async def _wrap_up(self, running: _Running) -> str | None:
        """The ending: the bot checks the work itself, posts one card, waits for a verdict.

        Nothing to open: the card says what got done and what passed. "looks good"
        adds the work to the project on this computer (never GitHub); "fix" turns
        failed checks into a fix step. Anything else is a normal chat in the thread,
        working on the build's copy. Returns "kept" or "fix".
        """
        assert running.copy is not None
        # The card and the "looks good?" wait live in the build's own thread; the
        # starting channel only gets a pointer, and the result once the thread is gone.
        target, report, here = running.report_target, running.report, running.thread
        plan_text = running.copy.plan_path.read_text(encoding="utf-8", errors="replace")
        checked, _ = count_tasks(plan_text)
        mention = f" <@{running.notify_user_id}>" if running.notify_user_id else ""

        results = await self._check_it_myself(running, plan_text)
        fails = [what for kind, what in results if kind == "fail"]
        proposed = await self._missing_steps(running, fails)
        if proposed and running.goal_rounds < GOAL_AUTO_ROUNDS:
            # Drew's pick: add the missing steps and keep going, a few rounds at most.
            running.goal_rounds += 1
            append_tasks(running.copy.plan_path, proposed)
            await commit_all(running.copy.path, "gowork: steps toward the goal")
            with contextlib.suppress(discord.HTTPException):
                await here.send(
                    f"🎯 The goal isn't met yet, so I'm adding {len(proposed)} "
                    f"step{'s' if len(proposed) != 1 else ''} and keeping going "
                    f"(round {running.goal_rounds} of {GOAL_AUTO_ROUNDS}):\n"
                    + "\n".join(f"• {short_label(p)}" for p in proposed)
                )
            return "fix"
        lessons = await self._lessons(running)
        if lessons:
            progress = running.copy.plan_path.with_name(
                f"{running.copy.plan_path.stem}.progress.md"
            )
            with progress.open("a", encoding="utf-8") as fh:
                fh.write("\n## Next time (from how this build went)\n")
                fh.writelines(f"- {b}\n" for b in lessons)
            await commit_all(running.copy.path, "gowork: what to do differently next time")
        with contextlib.suppress(discord.HTTPException):
            await here.send(
                f"🏁 **{running.repo_dir.name} is finished**{mention}",
                embed=finished_card(
                    running.repo_dir.name,
                    checked,
                    running.recaps or [],
                    results,
                    proposed,
                    lessons,
                ),
            )

        def is_verdict(text: str) -> bool:
            """Keep, throw away or a bare "fix". Anything else is a normal chat."""
            return (
                is_looks_good(text)
                or parked_choice(text) in ("throw", "finish")
                or clear_reply(text) in _FIX_WORDS
                or (bool(proposed) and clear_reply(text) in _ADD_WORDS)
            )

        self._queue_waiting(
            running,
            "finished — waiting for your **looks good**"
            + (" (the goal isn't met yet)" if proposed else ""),
        )
        running.in_review = True
        try:
            while True:
                try:
                    reply, woken = await self._wait_or_wake(
                        running, running.worker_thread_id, VERDICT_REMIND_SECONDS, is_verdict
                    )
                except PickDeclinedError:
                    continue  # a normal chat message; the chat answers it in this thread
                if woken:
                    reply = "looks good"  # a new plan was started: keep this finished one
                if reply is None:
                    with contextlib.suppress(discord.HTTPException):
                        await here.send(
                            f"⏰ Still waiting: {running.repo_dir.name} is finished. Type "
                            f"**looks good** to keep it, or just ask me here.{mention}"
                        )
                    continue
                verdict = parked_choice(reply)
                if verdict == "throw":
                    await remove_work_copy(running.copy)
                    with contextlib.suppress(discord.HTTPException):
                        await target.send("🗑️ Thrown away. Your real project was never touched.")
                    self._queue_note(running, "thrown away 🗑️")
                    with contextlib.suppress(Exception):
                        await running.thread.delete()
                    return "kept"
                if proposed and clear_reply(reply) in _ADD_WORDS:
                    append_tasks(running.copy.plan_path, proposed)
                    await commit_all(running.copy.path, "gowork: steps toward the goal")
                    await report(f"🎯 Added {len(proposed)} steps toward the goal. Keeping going.")
                    return "fix"
                if clear_reply(reply) in _FIX_WORDS:
                    what = (
                        "make these checks pass: " + "; ".join(fails)
                        if fails
                        else "fix what the last checks found"
                    )
                    append_fix_task(running.copy.plan_path, what)
                    await commit_all(running.copy.path, f"gowork: fix requested: {what[:60]}")
                    await report(f"🔧 Got it, fixing: “{what[:200]}”")
                    return "fix"
                # Changes made while chatting after the build are part of the build.
                await commit_all(running.copy.path, "gowork: changes from the chat afterwards")
                ok, message = await keep_work(running.copy)
                if not ok:
                    with contextlib.suppress(discord.HTTPException):
                        await here.send(
                            f"⚠️ I couldn't keep it yet: {message}. Your build is safe. "
                            "Sort that out and type **looks good** again."
                        )
                    continue
                self._queue_note(running, "kept in your project ✅")
                with contextlib.suppress(discord.HTTPException):
                    await target.send(f"✅ Kept: {message}. I cleaned up the worker thread.")
                with contextlib.suppress(Exception):
                    await running.thread.delete()
                return "kept"
        finally:
            running.in_review = False

    async def _park(self, running: _Running, outcome: LoopOutcome) -> str:
        """A build that stopped short waits for the person. Returns "again" or "gone"."""
        assert running.copy is not None
        target = running.report_target
        mention = f" <@{running.notify_user_id}>" if running.notify_user_id else ""
        if running.auto_finish:
            return await self._finish_early(running)
        why = {
            Status.STUCK: f"🛑 **I'm stuck.** {outcome.detail}",
            Status.ASK: f"⏸️ **Paused, waiting for your answer:** {outcome.detail}",
            Status.PAUSE: f"⏸️ **Paused.** {outcome.detail}",
        }.get(outcome.status, "⏹️ **Stopped.**")
        ask = (
            f"{why}\nNothing is lost: the finished steps are kept.\n"
            "Just tell me what you want in your own words and I'll carry on from there. "
            "Or type **close** to keep what's done and end here, or **throw it away** "
            f"to delete this build.{mention}"
        )
        with contextlib.suppress(discord.HTTPException):
            await running.thread.send(ask[:1900])
        self._queue_waiting(running, why.splitlines()[0].replace("**", "")[:200])
        reply = await self._wait_parked(running, ask)
        running.waiting_for_person = False
        if reply is None:  # a new plan was started in this project
            return await self._finish_early(running)
        choice = parked_choice(reply)
        if choice == "throw":
            await remove_work_copy(running.copy)
            with contextlib.suppress(discord.HTTPException):
                await target.send(
                    "🗑️ Thrown away. Your real project was never touched, "
                    "and I deleted the worker thread."
                )
            with contextlib.suppress(Exception):
                await running.thread.delete()
            return "gone"
        if choice == "finish":
            return await self._finish_early(running)
        if choice == "skip":
            skip_task(running.copy.plan_path)
            await commit_all(running.copy.path, "gowork: step skipped")
            await running.report("⏭️ Skipped that step, moving on.")
        else:
            if outcome.status == Status.ASK:
                running.loop.add_note(f"Answer to your question ({outcome.detail}): {reply}")
            elif reply.strip().lower().rstrip(".!") not in {"keep going", "try again", "go"}:
                running.loop.add_note(reply)
            await running.report("▶️ Keeping going.")
        running.loop.resume()
        return "again"

    async def _wait_or_wake(
        self, running: _Running, channel_id: int, timeout: float, accept: Any = None
    ) -> tuple[str | None, bool]:
        """Wait for a typed reply, or give way when a new plan closes this build.

        Returns (reply, woken). ``woken`` is True when a plan switch interrupted.
        """
        if running.wake is None:
            running.wake = asyncio.Event()
        if running.auto_finish:
            return None, True
        reply_task = asyncio.ensure_future(
            self.wait_for_reply(channel_id, timeout=timeout, accept=accept)
        )
        wake_task = asyncio.ensure_future(running.wake.wait())
        done, _ = await asyncio.wait({reply_task, wake_task}, return_when=asyncio.FIRST_COMPLETED)
        wake_task.cancel()
        if reply_task not in done:
            reply_task.cancel()
            return None, True
        return reply_task.result(), False

    async def _wait_parked(self, running: _Running, ask: str) -> str | None:
        """Wait for keep going / skip / wrap up / throw it away.

        The wait is in the build's own thread, so anything typed there is for the
        build: a word that isn't a choice is a hint, and means keep going.
        Returns None when a new plan in this project closes the build.
        """
        running.wake = running.wake or asyncio.Event()
        running.in_review = True
        try:
            while not running.auto_finish:
                reply_task = asyncio.ensure_future(
                    self.wait_for_reply(running.worker_thread_id, timeout=VERDICT_REMIND_SECONDS)
                )
                wake_task = asyncio.ensure_future(running.wake.wait())
                done, _ = await asyncio.wait(
                    {reply_task, wake_task}, return_when=asyncio.FIRST_COMPLETED
                )
                wake_task.cancel()
                if reply_task not in done:
                    reply_task.cancel()
                    break
                try:
                    reply = reply_task.result()
                except PickDeclinedError:
                    continue  # the chat answers it; still waiting
                if reply is not None:
                    return reply
                with contextlib.suppress(discord.HTTPException):
                    await running.thread.send(
                        f"⏰ Still waiting on {running.repo_dir.name}.\n{ask}"[:1900]
                    )
            return None
        finally:
            running.in_review = False

    async def _finish_early(self, running: _Running) -> str:
        """Keep the finished steps in the project and end the build ("wrap up")."""
        assert running.copy is not None
        target = running.report_target
        checked, unchecked = count_tasks(
            running.copy.plan_path.read_text(encoding="utf-8", errors="replace")
        )
        ok, message = await keep_work(running.copy)
        if not ok:
            running.auto_finish = False
            with contextlib.suppress(discord.HTTPException):
                await target.send(
                    f"⚠️ I couldn't keep {running.repo_dir.name}'s finished steps yet: {message}. "
                    "The build is safe. Type **wrap up** again once that's sorted, or "
                    "**throw it away**."
                )
            return await self._park(running, LoopOutcome(Status.NONE, "stopped"))
        self._queue_note(running, f"wrapped up: kept {checked} finished steps")
        with contextlib.suppress(discord.HTTPException):
            await target.send(
                f"✅ Wrapped up: kept {checked} finished step{'s' if checked != 1 else ''} in "
                f"the project ({unchecked} not done). I cleaned up the worker thread."
            )
        with contextlib.suppress(Exception):
            await running.thread.delete()
        return "gone"

    async def _close_for_switch(self, plan_path: str, report_to: Any) -> None:
        """Starting a new plan closes the project's open build, keeping its finished steps.

        No stopping by hand: the open build finishes the step it is on, its finished
        steps go into the project, and then the new plan starts.
        """
        with contextlib.suppress(ValueError, OSError):
            repo_dir = await resolve_repo(Path(plan_path).expanduser())
            existing = self._running.get(repo_dir)
            if existing is None or existing.copy is None:
                return
            if existing.copy.plan_path.name == Path(plan_path).name and not existing.in_review:
                return  # the same plan is already running; start_loop will say so
            old = existing.copy.plan_path.name
            with contextlib.suppress(discord.HTTPException):
                await report_to.send(
                    f"🔀 Switching to `{Path(plan_path).name}`: I'm closing the `{old}` build "
                    "first (it finishes the step it's on), keeping its finished steps in the "
                    "project. Nothing for you to do."
                )
            existing.auto_finish = True
            existing.loop.request_stop()
            if existing.wake is None:
                existing.wake = asyncio.Event()
            existing.wake.set()
            if existing.task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(existing.task), SWITCH_TIMEOUT_SECONDS)

    async def _check_it_myself(self, running: _Running, plan_text: str) -> list[tuple[str, str]]:
        """Run the plan's test, then have a fresh session do the "How to try it" checks."""
        assert running.copy is not None
        results: list[tuple[str, str]] = []
        argv = plan_check_command(plan_text)
        if argv is not None:
            ok, tail = await run_check(running.copy.path, argv)
            last = tail.strip().splitlines()[-1] if tail.strip() else ""
            results.append(
                ("pass", "The plan's own test passes")
                if ok
                else ("fail", f"The plan's own test fails: {last[:150]}")
            )
        checks = finished_checks(plan_text)
        if not checks or running.run_session is None:
            return results
        text, error = await running.run_session(
            checker_prompt(running.copy.plan_path, checks), "🔎 Checking the finished work"
        )
        found = parse_check_results(text)
        if not found:
            reason = error or "the check session didn't report back"
            results.append(("skip", f"I couldn't do the checks myself ({reason[:150]})"))
        results += found
        # The checker must not change anything; undo it if it did.
        await _restore_tracked(running.copy.path)
        return results

    @commands.Cog.listener()
    async def on_session_stopped(self, channel_id: int) -> None:
        """The Stop button or /stop in a build's worker thread stops the whole build.

        Otherwise the loop sees a step that didn't finish, calls it a failure and
        runs it again — the person pressed Stop to stop, not to retry.
        """
        for running in self._running.values():
            if running.worker_thread_id == channel_id:
                running.loop.request_stop()

    def is_worker_thread(self, channel_id: int) -> bool:
        """True when *channel_id* is the thread a running build works in."""
        return any(r.worker_thread_id == channel_id for r in self._running.values())

    def stop_for(self, channel_id: int) -> _Running | None:
        """Ask the loop tied to *channel_id* (worker or report channel) to stop."""
        for running in self._running.values():
            if channel_id in (running.worker_thread_id, running.report_channel_id):
                running.loop.request_stop()
                return running
        return None

    def _only_user(self) -> int | None:
        """The one person allowed to use the bot — who to ping when nobody was named."""
        users = self._allowed_user_ids or set()
        return next(iter(users)) if len(users) == 1 else None

    def _authorized(self, user_id: int) -> bool:
        return self._allowed_user_ids is None or user_id in self._allowed_user_ids

    async def _thread_project(self, channel: Any) -> Path | None:
        """The project this thread works in — even after its session was cleared.

        Clearing a session forgets its folder, so fall back to the thread's own
        session copy, ``<project>/.worktrees/wt-<thread id>``.
        """
        chat = self._chat()
        record = None
        with contextlib.suppress(Exception):
            record = await chat.repo.get(channel.id)
        if record is not None and record.working_dir:
            return project_of(Path(record.working_dir))
        root_dir = getattr(chat.runner, "working_dir", None)
        if not isinstance(root_dir, str) or not root_dir:
            return None
        return project_for_thread(Path(root_dir), channel.id)

    async def _pick_plan(self, channel: Any) -> str | None:
        """Every plan this thread's project could run, as lettered text choices.

        One plan is used directly. When the thread's project can't be told (or has
        no open plan), the unfinished plans of all projects are listed instead.
        The person types a letter — no buttons, no paths.
        """
        project = await self._thread_project(channel)
        base = project
        plans = list_plans(project) if project is not None else []
        if not plans:
            root_dir = getattr(self._chat().runner, "working_dir", None)
            if isinstance(root_dir, str) and root_dir:
                base = Path(root_dir)
                plans = list_plans_across(base)
        if not plans:
            return None
        if len(plans) == 1:
            return str(plans[0])
        lines = ["Which plan should I run? Just type its letter:"]
        for i, plan in enumerate(plans):
            try:
                checked, unchecked = count_tasks(plan.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                checked, unchecked = 0, 0
            try:
                name = str(plan.relative_to(base)) if base is not None else plan.name
            except ValueError:
                name = plan.name
            lines.append(
                f"**{choice_letter(i)})** `{name}` · {unchecked} of {checked + unchecked} left"
            )
        for chunk in _chunks(lines):
            await channel.send(chunk)
        try:
            reply = await self.wait_for_reply(
                channel.id,
                timeout=PICK_TIMEOUT_SECONDS,
                accept=lambda text: parse_plan_reply(text, plans) is not None,
            )
        except PickDeclinedError:
            with contextlib.suppress(discord.HTTPException):
                await channel.send("-# Okay, no build for now. Answering that instead.")
            raise
        picked = parse_plan_reply(reply or "", plans)
        return str(picked) if picked is not None else None

    async def start_asking(
        self,
        report_to: Any,
        plan_path: str,
        *,
        notify_user_id: int | None = None,
        harness: str | None = None,
        model: str | None = None,
        fallback_harness: str | None = None,
        fallback_model: str | None = None,
    ) -> discord.Thread | None:
        """Start a build from outside a slash command (the REST API, a planner).

        When no harness was given, ask in *report_to* in plain words and wait
        for the typed reply, exactly like ``/gowork`` does.
        """
        parent: Any = report_to.parent if isinstance(report_to, discord.Thread) else report_to
        await self._close_for_switch(plan_path, report_to)
        if harness in _SAME_WORDS:
            # "Use whatever this thread uses" — started by words, no model named.
            settings = getattr(self._chat(), "_backend_settings", None)
            current = (
                await settings.current_backend(getattr(parent, "id", None)) if settings else None
            )
            harness, model = current or "claude", None
        if harness is None:
            settings = getattr(self._chat(), "_backend_settings", None)
            current = (
                await settings.current_backend(getattr(parent, "id", None)) if settings else None
            )
            with contextlib.suppress(discord.HTTPException):
                await report_to.send(f"📋 Ready to build `{plan_path}`.")
            picked = await self._ask_harness(report_to, current)
            if picked is None:
                with contextlib.suppress(discord.HTTPException):
                    await report_to.send("Not started: I didn't get a harness to use.")
                return None
            harness, model = picked
        per_step = harness == PER_STEP
        if per_step:
            harness, model = None, None
        try:
            return await self.start_loop(
                parent,
                plan_path,
                report_to=report_to,
                notify_user_id=notify_user_id,
                harness=harness,
                model=model,
                fallback_harness=fallback_harness,
                fallback_model=fallback_model,
                ask_goal=True,
                per_step_ai=per_step,
            )
        except (ValueError, RuntimeError) as exc:
            with contextlib.suppress(discord.HTTPException):
                await report_to.send(f"Could not start: {exc}")
            return None

    async def _ai_choices(self) -> list[tuple[str, str, str]]:
        """Every AI and model the bot can run: (harness, model, short note).

        The same catalog as ``/switch`` when that cog is loaded (live model
        lists), otherwise the built-in suggestions.
        """
        backend_cog: Any = self.bot.cogs.get("BackendCommandCog")
        catalog: dict[str, list[tuple[str, str]]] = {}
        loader = getattr(backend_cog, "_switch_catalog", None)
        if loader is not None:
            with contextlib.suppress(Exception):
                catalog = await loader()
        if not catalog:
            from .backend_command import SUGGESTED_MODELS

            catalog = {k: v for k, v in SUGGESTED_MODELS.items() if k in ALL_BACKENDS}
        return [
            (harness, model, note) for harness, models in catalog.items() for model, note in models
        ]

    async def _ask_harness(
        self, channel: Any, current: str | None
    ) -> tuple[str, str | None] | None:
        """List every AI and model as lettered choices; the person types a letter.

        Typing a name ("claude sonnet", "codex") still works too.
        """
        options = await self._ai_choices()
        lines = ["Which AI should do the work? Just type its letter:"]
        lines.append(
            "**A)** Let the bot pick the best AI for each step: fast and cheap for easy "
            "steps, the strongest for hard ones (recommended)"
        )
        lines.append(f"**B)** Same as this thread ({current or 'its usual AI'})")
        for i, (harness, model, note) in enumerate(options, start=2):
            tail = f" — {note}" if note else ""
            lines.append(f"**{choice_letter(i)})** {harness} · `{model}`{tail}")
        for chunk in _chunks(lines):
            await channel.send(chunk)
        for _ in range(2):
            reply = await self.wait_for_reply(channel.id, timeout=PICK_TIMEOUT_SECONDS)
            if reply is None:
                return None
            m = _LETTER_REPLY_RE.match(reply)
            if m is not None and m.group(1).upper() == "A":
                return PER_STEP, None
            if m is not None and m.group(1).upper() == "B" and current:
                return current, None
            picked = _match_ai(reply, options, current, first=2)
            if picked:
                return picked
            with contextlib.suppress(discord.HTTPException):
                await channel.send("Just type the letter of the AI you want, e.g. `B`.")
        return None

    async def _quick_ai(self, prompt: str) -> str | None:
        """One short answer from a fast, cheap Claude — the per-step AI picker.

        Never raises: any failure is None, and the build keeps the AI it has.
        """
        runner: Any = getattr(self._chat(), "runner", None)
        command = getattr(runner, "command", None) or "claude"
        env = None
        with contextlib.suppress(Exception):
            env = runner._build_env()  # the same keys and overlay as real sessions
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                "-p",
                "--model",
                "haiku",
                "--",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                out, _err = await asyncio.wait_for(proc.communicate(), PICK_AI_TIMEOUT_SECONDS)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return None
        except Exception:
            logger.warning("gowork: the step-AI picker couldn't run", exc_info=True)
            return None
        if proc.returncode != 0:
            return None
        return out.decode(errors="replace").strip() or None

    async def _pick_step_ai(self, running: _Running, step: str) -> None:
        """Before a step: a quick AI picks which AI does it, and the thread switches."""
        assert running.copy is not None
        settings = getattr(self._chat(), "_backend_settings", None)
        options = [
            o
            for o in await self._ai_choices()
            if o[0] != "local" and "vision" not in o[1] and o[0] not in running.limited
        ]
        if not options:
            return
        goal, _done = plan_goal(
            running.copy.plan_path.read_text(encoding="utf-8", errors="replace")
        )
        track = track_record(read_records(self._records_path))
        reply = await self._quick_ai(step_ai_prompt(step, goal, options, track))
        index = parse_pick(reply, len(options))
        if index is None:
            return  # keep the AI the build has
        harness, model, _note = options[index]
        if settings is not None:
            await settings.set_backend(harness, thread_id=running.worker_thread_id)
            await settings.set_model(harness, model, thread_id=running.worker_thread_id)
        why = re.sub(r"^\W*[A-Za-z]\W*", "", (reply or "").splitlines()[0]).strip()
        with contextlib.suppress(discord.HTTPException):
            await running.thread.send(
                f"-# 🤖 This step: {harness} · {model}" + (f" — {why[:120]}" if why else "")
            )

    async def _strongest(self, running: _Running) -> tuple[str, str] | None:
        """The most capable AI to bring in, the same kind as the build's if possible."""
        settings = getattr(self._chat(), "_backend_settings", None)
        if settings is None:
            return None
        harness = await settings.current_backend(running.worker_thread_id)
        model = await settings.current_model(harness, running.worker_thread_id)
        strong = [
            (h, m)
            for h, m, note in await self._ai_choices()
            if ("most capable" in note.lower() or "strongest" in note.lower())
            and h not in running.limited
            and h != "local"
        ]
        strong.sort(key=lambda hm: hm[0] != harness)  # same kind of AI first
        for h, m in strong:
            if (h, m) != (harness, model):
                return h, m
        return None

    async def _unstick(self, running: _Running, outcome: LoopOutcome) -> tuple[bool, LoopOutcome]:
        """Before asking the person about a stuck step: a stronger AI, then smaller steps.

        Returns (handled, outcome). Not handled means ask the person — with the
        outcome saying what was already tried.
        """
        if not self.smart_unstick:
            return False, outcome
        try:
            return await self._try_unstick(running, outcome)
        except Exception:
            logger.warning("gowork: unsticking failed; asking the person", exc_info=True)
            return False, outcome

    async def _try_unstick(
        self, running: _Running, outcome: LoopOutcome
    ) -> tuple[bool, LoopOutcome]:
        assert running.copy is not None
        if outcome.status != Status.STUCK or outcome.detail == "round limit reached":
            return False, outcome
        plan = running.copy.plan_path
        before = plan.read_text(encoding="utf-8", errors="replace")
        step = first_unchecked(before)
        if step is None:
            return False, outcome
        settings = getattr(self._chat(), "_backend_settings", None)
        tries = running.unstuck.get(step, 0)
        if tries == 0:
            running.unstuck[step] = 1
            strong = await self._strongest(running)
            if strong is not None and settings is not None:
                harness = await settings.current_backend(running.worker_thread_id)
                model = await settings.current_model(harness, running.worker_thread_id)
                running.boosted = running.boosted or (step, harness, model)
                await settings.set_backend(strong[0], thread_id=running.worker_thread_id)
                await settings.set_model(strong[0], strong[1], thread_id=running.worker_thread_id)
                running.loop.add_note(
                    f"The last try at this step got stuck: {outcome.detail}. You're a "
                    "stronger AI brought in to finish it — take a different approach."
                )
                with contextlib.suppress(discord.HTTPException):
                    await running.thread.send(
                        f"-# 💪 That step got stuck, so I'm trying it again on "
                        f"{strong[0]} · {strong[1]}."
                    )
                running.loop.resume()
                return True, outcome
            tries = 1
        if tries == 1 and running.run_session is not None:
            running.unstuck[step] = 2
            text, _error = await running.run_session(
                split_prompt(plan, step, outcome.detail), "✂️ Splitting the stuck step…"
            )
            after = plan.read_text(encoding="utf-8", errors="replace")
            if parse_status(text)[0] == Status.PLAN and step not in open_tasks(after):
                await commit_all(running.copy.path, "gowork: split a stuck step")
                for new in set(open_tasks(after)) - set(open_tasks(before)):
                    running.unstuck[new] = 2  # a part that gets stuck goes to the person
                await self._end_boost(running, force=True)
                with contextlib.suppress(discord.HTTPException):
                    await running.thread.send(
                        "-# ✂️ Split the stuck step into smaller ones and carrying on."
                    )
                running.loop.resume()
                return True, outcome
        return False, LoopOutcome(
            outcome.status,
            f"{outcome.detail} (I already tried a stronger AI and splitting it into "
            "smaller steps.)",
            outcome.rounds,
        )

    async def _end_boost(self, running: _Running, *, force: bool = False) -> None:
        """Once the stuck step is done (or split), the build goes back to its usual AI."""
        if running.boosted is None or running.copy is None:
            return
        step, harness, model = running.boosted
        now = first_unchecked(running.copy.plan_path.read_text(encoding="utf-8", errors="replace"))
        if now == step and not force:
            return
        running.boosted = None
        settings = getattr(self._chat(), "_backend_settings", None)
        if settings is None:
            return
        await settings.set_backend(harness, thread_id=running.worker_thread_id)
        if model:
            await settings.set_model(harness, model, thread_id=running.worker_thread_id)

    # -- the build queue (idea 6) ------------------------------------------------

    async def enqueue(
        self,
        report_to: Any,
        plan_path: str,
        *,
        notify_user_id: int | None = None,
        harness: str | None = None,
        model: str | None = None,
    ) -> int:
        """ "Queue it": put a plan in line. It starts when the builds ahead are done or waiting."""
        item = QueueItem(
            plan_path=plan_path,
            report_id=getattr(report_to, "id", 0),
            notify_user_id=notify_user_id,
            harness=harness,
            model=model,
        )
        self._queue_reports[item.report_id] = report_to
        place = self._queue.add(item)
        with contextlib.suppress(discord.HTTPException):
            await report_to.send(
                f"📥 Queued `{Path(plan_path).name}`: number {place} in line. It starts when "
                "the builds ahead of it are done or waiting for you, and you'll get a summary "
                "at 8 am."
            )
        await self._advance_queue()
        return place

    async def _queue_report(self, report_id: int) -> Any:
        report: Any = self._queue_reports.get(report_id) or self.bot.get_channel(report_id)
        if report is None:
            with contextlib.suppress(Exception):
                report = await self.bot.fetch_channel(report_id)
        return report

    async def _advance_queue(self) -> None:
        """Start the next queued build unless a queued one is still busy working."""
        async with self._queue_lock:
            if any(r.queued and not r.waiting_for_person for r in self._running.values()):
                return
            for item in list(self._queue.state.waiting):
                try:
                    repo = await resolve_repo(Path(item.plan_path).expanduser())
                except ValueError:
                    self._queue.take(item)
                    continue
                if repo in self._running:
                    continue  # that project already has a build open; try the next one
                report = await self._queue_report(item.report_id)
                if report is None:
                    self._queue.take(item)
                    continue
                parent: Any = report.parent if isinstance(report, discord.Thread) else report
                self._queue.take(item)
                try:
                    thread = await self.start_loop(
                        parent,
                        item.plan_path,
                        report_to=report,
                        notify_user_id=item.notify_user_id,
                        harness=item.harness,
                        model=item.model,
                        per_step_ai=item.harness is None,
                        queued=True,
                    )
                except (ValueError, RuntimeError) as exc:
                    with contextlib.suppress(discord.HTTPException):
                        await report.send(
                            f"Couldn't start queued `{Path(item.plan_path).name}`: {exc}"
                        )
                    continue
                self._queue.started(item, repo.name, thread.id)
                return

    def _queue_note(self, running: _Running, state: str) -> None:
        if running.queued:
            self._queue.note(running.worker_thread_id, state)

    def _queue_waiting(self, running: _Running, state: str) -> None:
        """A queued build now waits for the person: note why and let the line move on."""
        if not running.queued:
            return
        running.waiting_for_person = True
        self._queue.note(running.worker_thread_id, state)
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._advance_queue())

    async def _maybe_morning_summary(self, now: datetime.datetime) -> None:
        """At 8 am, once a day: what the queue did, posted where builds were queued."""
        today = now.date().isoformat()
        if now.hour < MORNING_HOUR or self._queue.state.last_summary == today:
            return
        entries = self._queue.unreported()
        if not entries:
            return
        text = morning_summary(entries, len(self._queue.state.waiting))
        report = await self._queue_report(int(entries[-1].get("report_id") or 0))
        if report is not None:
            with contextlib.suppress(discord.HTTPException):
                await report.send(text)
        self._queue.mark_reported(today)

    async def _reviewer_for(self, running: _Running) -> tuple[str, str] | None:
        """A different AI than the builder: another kind if possible, the strongest first."""
        settings = getattr(self._chat(), "_backend_settings", None)
        if settings is None:
            return None
        harness = await settings.current_backend(running.worker_thread_id)
        model = await settings.current_model(harness, running.worker_thread_id)
        options = [
            (h, m, note)
            for h, m, note in await self._ai_choices()
            if h != "local" and "vision" not in m and h not in running.limited
        ]

        def rank(o: tuple[str, str, str]) -> tuple[bool, bool]:
            strong = "most capable" in o[2].lower() or "strongest" in o[2].lower()
            return (o[0] == harness, not strong)

        for h, m, _note in sorted(options, key=rank):
            if (h, m) != (harness, model):
                return h, m
        return None

    async def _review_step(self, running: _Running, step: str, base: str | None) -> str | None:
        """A second AI reviews a finished step. None = approved (or no review possible)."""
        if not self.smart_review or running.copy is None or running.run_session is None:
            return None
        settings = getattr(self._chat(), "_backend_settings", None)
        reviewer = await self._reviewer_for(running)
        if reviewer is None or settings is None:
            return None
        tid = running.worker_thread_id
        harness = await settings.current_backend(tid)
        model = await settings.current_model(harness, tid)
        await settings.set_backend(reviewer[0], thread_id=tid)
        await settings.set_model(reviewer[0], reviewer[1], thread_id=tid)
        try:
            text, _error = await running.run_session(
                review_step_prompt(running.copy.plan_path, step, base),
                f"🔍 A second AI ({reviewer[0]} · {reviewer[1]}) is reviewing this step…",
            )
        finally:
            await settings.set_backend(harness, thread_id=tid)
            if model:
                await settings.set_model(harness, model, thread_id=tid)
        return parse_review(text)

    async def _ai_label(self, running: _Running, thread_id: int | None = None) -> str:
        """ "harness · model" the thread runs on, for the records."""
        settings = getattr(self._chat(), "_backend_settings", None)
        if settings is None:
            return "the thread's AI"
        try:
            tid = thread_id or running.worker_thread_id
            harness = await settings.current_backend(tid)
            model = await settings.current_model(harness, tid)
        except Exception:
            return "the thread's AI"
        return " · ".join(str(x) for x in (harness, model) if x) or "the thread's AI"

    async def _record(
        self,
        running: _Running,
        step: str,
        result: str,
        detail: str,
        *,
        ai: str | None = None,
        seconds: float | None = None,
    ) -> None:
        """Save how one step went — kept across builds for the picker and the lessons."""
        started = running.round_started
        record = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "repo": running.repo_dir.name,
            "plan": running.copy.plan_path.name if running.copy else "",
            "step": step,
            "ai": ai or (started[1] if started else "the thread's AI"),
            "seconds": round(
                seconds
                if seconds is not None
                else (time.monotonic() - started[0] if started else 0)
            ),
            "result": result,
            "detail": detail[:200],
        }
        running.records.append(record)
        append_record(self._records_path, record)

    async def _lessons(self, running: _Running) -> list[str]:
        """2–3 "next time" bullets from this build's records, by a quick AI."""
        if not running.records:
            return []
        reply = await self._quick_ai(lessons_prompt(running.records, running.recaps or []))
        bullets = [
            line.strip()[2:].strip()
            for line in (reply or "").splitlines()
            if line.strip().startswith(("- ", "• "))
        ]
        return [b for b in bullets if b][:3]

    async def _groups_for(self, steps: list[str]) -> list[list[str]]:
        """Ask the quick AI which of *steps* can be built at the same time."""
        if len(steps) < 2:
            return [[s] for s in steps]
        reply = await self._quick_ai(group_prompt(steps))
        return [[steps[i] for i in g] for g in parse_groups(reply, len(steps))]

    async def _next_group(self, running: _Running, steps: list[str]) -> list[str]:
        """The group the first open step belongs to (asks again after plan changes)."""
        for group in running.groups:
            if group and group[0] == steps[0] and all(s in steps for s in group):
                return group
        running.groups = await self._groups_for(steps)
        return next((g for g in running.groups if g and g[0] == steps[0]), steps[:1])

    async def _run_group(self, running: _Running, steps: list[str]) -> list[tuple[str, bool, str]]:
        """Build *steps* at the same time, each in its own copy and thread, then merge.

        A step that fails or doesn't combine is left unticked, and the loop runs it
        again on its own — parallel is a speed-up, never a new way to fail.
        """
        assert running.copy is not None
        copy = running.copy
        chat = self._chat()
        settings = getattr(chat, "_backend_settings", None)
        parent: Any = getattr(running.thread, "parent", None) or running.report_target
        with contextlib.suppress(discord.HTTPException):
            await running.thread.send(
                "-# ⚡ Building these at the same time: " + "; ".join(short_label(s) for s in steps)
            )

        async def one(index: int, step: str) -> tuple[str, bool, str, Any, Any, str, float]:
            side = await create_side_copy(copy, f"p{index + 1}")
            sub: Any = await chat.spawn_session(
                parent,
                f"⚡ One step of the {running.repo_dir.name} build, running alongside "
                f"others: {short_label(step)}",
                thread_name=f"⚡ {short_label(step)[:80]}",
                auto_start=False,
                working_dir=str(side.path),
            )
            self._quiet(sub.id)
            if settings is not None:
                with contextlib.suppress(Exception):
                    harness = await settings.current_backend(running.worker_thread_id)
                    model = await settings.current_model(harness, running.worker_thread_id)
                    await settings.set_backend(harness, thread_id=sub.id)
                    if model:
                        await settings.set_model(harness, model, thread_id=sub.id)
            result: dict[str, str | None] = {}

            async def sink(text: str | None, error: str | None) -> None:
                result["text"], result["error"] = text, error

            ai = await self._ai_label(running, sub.id)
            started = time.monotonic()
            seed = await sub.send(f"-# ⚡ Working on: {step}")
            await chat.run_fresh_turn(
                seed,
                sub,
                parallel_prompt(copy.plan_path, step),
                working_dir=str(side.path),
                result_sink=sink,
            )
            status, detail = parse_status(result.get("text"))
            ok = status == Status.DONE
            return (
                step,
                ok,
                detail or result.get("error") or "",
                side,
                sub,
                ai,
                time.monotonic() - started,
            )

        outcomes = await asyncio.gather(
            *(one(i, s) for i, s in enumerate(steps)), return_exceptions=True
        )
        results: list[tuple[str, bool, str]] = []
        progress = copy.plan_path.with_name(f"{copy.plan_path.stem}.progress.md")
        for step, outcome in zip(steps, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning("gowork: a parallel step failed to run", exc_info=outcome)
                results.append((step, False, "it couldn't start"))
                continue
            _step, ok, detail, side, sub, ai, seconds = outcome
            landed = ok and await side_has_new_work(copy, side)
            landed = landed and await merge_side_copy(copy, side)
            await self._record(
                running,
                step,
                "done alongside others" if landed else "didn't combine",
                detail,
                ai=ai,
                seconds=seconds,
            )
            if not landed:
                await remove_side_copy(copy, side)
            if landed and tick_task(copy.plan_path, step):
                with progress.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n## {step} (built alongside other steps)\n- {detail}\n")
                await commit_all(copy.path, f"gowork: {short_label(step)[:60]} (parallel)")
                if running.recaps is not None:
                    running.recaps.append(f"{short_label(step)}: done alongside other steps")
                with contextlib.suppress(discord.HTTPException):
                    await running.thread.send(f"✅ Done alongside others: {short_label(step)}")
                results.append((step, True, detail))
            else:
                results.append((step, False, detail or "it didn't combine with the others"))
            with contextlib.suppress(Exception):
                await sub.delete()
        return results

    async def _limit_hit(self, running: _Running, message: str) -> bool:
        """The build's AI hit a usage limit: ask in its thread to switch AI or wait.

        True once another AI is set for the worker thread or the wait is over;
        False when nobody answered (the build then parks like any pause).
        """
        thread = running.thread
        settings = getattr(self._chat(), "_backend_settings", None)
        current = None
        if settings is not None:
            with contextlib.suppress(Exception):
                harness = await settings.current_backend(thread.id)
                model = await settings.current_model(harness, thread.id)
                current = " · ".join(x for x in (harness, model) if x)
                running.limited.add(harness)  # the step picker leaves it out from now on
        fb = running.fallback
        if fb is not None and settings is not None:
            fb_label = " · ".join(x for x in fb if x)
            if current != fb_label:
                await settings.set_backend(fb[0], thread_id=thread.id)
                if fb[1]:
                    await settings.set_model(fb[0], fb[1], thread_id=thread.id)
                with contextlib.suppress(discord.HTTPException):
                    await thread.send(
                        f"-# 🔀 {current or 'The AI'} hit its usage limit; switched to "
                        f"{fb_label} by itself (set when the build started)."
                    )
                return True
        options = await self._ai_choices()
        mention = f" <@{running.notify_user_id}>" if running.notify_user_id else ""
        lines = [
            f"⏳ **{current or 'The AI'} hit its usage limit.**{mention}\n-# {message}",
            "It didn't count as a try. Type a letter to switch AI, or **wait**:",
            "**A)** Wait, and try the same AI again in 30 minutes",
        ]
        for i, (harness, model, note) in enumerate(options, start=1):
            tail = f" — {note}" if note else ""
            lines.append(f"**{choice_letter(i)})** {harness} · `{model}`{tail}")
        for chunk in _chunks(lines):
            with contextlib.suppress(discord.HTTPException):
                await thread.send(chunk)
        self._queue_waiting(running, "waiting: its AI hit a usage limit")
        while True:
            reply, _woken = await self._wait_or_wake(running, thread.id, ASK_TIMEOUT_SECONDS)
            running.waiting_for_person = False
            if reply is None:
                return False
            if reply.strip().lower().rstrip(".!)") in _WAIT_WORDS:
                with contextlib.suppress(discord.HTTPException):
                    await thread.send("-# ⏳ OK, I'll try again in 30 minutes.")
                await asyncio.sleep(LIMIT_WAIT_SECONDS)
                return True
            picked = _match_ai(reply, options, None)
            if picked is None or settings is None:
                with contextlib.suppress(discord.HTTPException):
                    await thread.send("Type **wait**, or the letter of the AI to switch to.")
                continue
            harness, model = picked
            await settings.set_backend(harness, thread_id=thread.id)
            if model:
                await settings.set_model(harness, model, thread_id=thread.id)
            with contextlib.suppress(discord.HTTPException):
                await thread.send(f"-# 🔀 Switched to {harness}{f' · {model}' if model else ''}.")
            return True

    @app_commands.command(name="gowork", description="Work through the plan, one task at a time")
    @app_commands.describe(plan="Plan .md file (optional — found automatically in this project)")
    async def gowork(self, interaction: discord.Interaction, plan: str | None = None) -> None:
        if not self._authorized(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        channel = interaction.channel
        parent = channel.parent if isinstance(channel, discord.Thread) else channel
        if not isinstance(parent, discord.TextChannel):
            await interaction.response.send_message(
                "Run this in a text channel or one of its threads.", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)
        await interaction.followup.send("🔁 Getting ready…")
        try:
            plan = plan or await self._pick_plan(channel)
        except PickDeclinedError:
            return
        if not plan:
            await interaction.followup.send(
                "I couldn't find a plan with `- [ ]` tasks in this project. "
                "Ask the planner to write one, or give me the file."
            )
            return
        report_to: Any = channel
        await interaction.followup.send(f"📋 Plan: `{plan}`")
        await self._close_for_switch(plan, report_to)
        settings = getattr(self._chat(), "_backend_settings", None)
        current = await settings.current_backend(parent.id) if settings else None
        picked = await self._ask_harness(report_to, current)
        if picked is None:
            await interaction.followup.send("Not started: I didn't get a harness to use.")
            return
        harness, model = picked
        per_step = harness == PER_STEP
        if per_step:
            harness, model = None, None
        try:
            thread = await self.start_loop(
                parent,
                plan,
                report_to=report_to,
                notify_user_id=interaction.user.id,
                harness=harness,
                model=model,
                ask_goal=True,
                per_step_ai=per_step,
            )
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(f"Could not start: {exc}")
            return
        await interaction.followup.send(
            f"Working on `{plan}` with {harness}{f' · {model}' if model else ''}. "
            f"Worker thread: {thread.mention}"
        )

    @app_commands.command(name="stopwork", description="Stop /gowork after the current task")
    async def stopwork(self, interaction: discord.Interaction) -> None:
        if not self._authorized(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        running = self.stop_for(interaction.channel_id or 0)
        if running is None:
            await interaction.response.send_message("Nothing is working here.", ephemeral=True)
            return
        await interaction.response.send_message("Stopping after the current task finishes.")
