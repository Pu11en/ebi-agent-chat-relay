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
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from claude_code_core.loop_store import LoopRecord, LoopStore
from claude_code_core.task_loop import (
    LoopOutcome,
    Status,
    TaskLoop,
    append_fix_task,
    checker_prompt,
    count_tasks,
    is_looks_good,
    list_plans,
    list_plans_across,
    needs_you,
    open_tasks,
    parked_choice,
    parse_check_results,
    plan_check_command,
    plan_try_checks,
    project_for_thread,
    project_of,
    run_check,
    short_label,
    skip_task,
    take_snapshot,
)
from claude_code_core.work_copy import (
    WorkCopy,
    WorkCopyError,
    commit_all,
    create_work_copy,
    keep_work,
    remove_work_copy,
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


def plan_card(plan_text: str, plan_name: str, harness: str | None, model: str | None) -> Any:
    """The "here's what I'm going to do" card: every step left, in plain words."""
    ai = " · ".join(x for x in (harness, model) if x) or "the thread's usual AI"
    lines = [f"**Plan:** {plan_name}", f"**AI doing the work:** {ai}", ""]
    for task in open_tasks(plan_text):
        dot = "🟡" if needs_you(task) else "🟢"
        tail = " — I'll ask you first" if dot == "🟡" else ""
        lines.append(f"{dot} **{short_label(task)}**{tail}")
    lines += ["", "🟢 I do it by myself   🟡 I stop and ask you"]
    return discord.Embed(
        title="📋 Here's what I'm going to do",
        description=_fit("\n".join(lines)),
        color=_BLUE,
    )


def finished_card(name: str, done: int, recaps: list[str], results: list[tuple[str, str]]) -> Any:
    """The last card: what got done, what the bot checked itself, what to type."""
    fails = [r for r in results if r[0] == "fail"]
    lines = [f"**All {done} steps are done.**"]
    if recaps:
        lines += ["", "**What got done**", *[f"• {r}" for r in recaps[-10:]]]
    if results:
        lines += ["", "**What I checked myself**"]
        lines += [f"{_MARK.get(kind, '⚪')} {what}" for kind, what in results]
    lines.append("")
    if fails:
        lines.append(
            f"**{len(fails)} check{'s' if len(fails) > 1 else ''} failed.** Type **fix** and "
            "I'll fix it, **looks good** to keep it anyway, or tell me what's wrong."
        )
    else:
        lines.append("Type **looks good** to keep it, or tell me what's wrong.")
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
        future = self._waiters.get(channel_id)
        if future is not None and not future.done():
            accept = self._accepts.get(channel_id)
            if accept is not None and not accept(text):
                future.set_exception(PickDeclinedError(text))
                return False
            future.set_result(text)
            return True
        for running in self._running.values():
            if running.worker_thread_id == channel_id and running.in_review:
                review = self._waiters.get(running.report_channel_id)
                if review is not None and not review.done():
                    review.set_result(text)
                    return True
            if running.worker_thread_id == channel_id:
                running.loop.add_note(text)
                with contextlib.suppress(Exception):
                    asyncio.get_running_loop().create_task(
                        message.channel.send("-# 📝 Got it, I'll pass that to the next task.")
                    )
                return True
        return False

    async def start_loop(
        self,
        channel: discord.TextChannel,
        plan_path: str,
        *,
        report_to: discord.abc.Messageable | None = None,
        notify_user_id: int | None = None,
        harness: str | None = None,
        model: str | None = None,
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
            report_channel_id=getattr(report_target, "id", channel.id),  # waits happen here
            notify_user_id=notify_user_id,
            harness=harness,
            model=model,
        )
        self._store.save(record)
        self._launch(record, thread, report_target)
        plan_text = copy.plan_path.read_text(encoding="utf-8", errors="replace")
        with contextlib.suppress(discord.HTTPException):
            await report_target.send(
                f"▶️ Started. The work happens in {thread.mention}; I'll ping you only if "
                "I need you, and when it's finished.",
                embed=plan_card(plan_text, plan.name, harness, model),
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
            with contextlib.suppress(discord.HTTPException):
                await report_target.send(f"{text} · {thread.mention}{ping}")

        rounds = 0
        recaps: list[str] = []

        async def run_round(prompt: str) -> tuple[str | None, str | None]:
            nonlocal rounds
            rounds += 1
            now = await take_snapshot(work_dir, work_plan)
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

        async def ask(question: str) -> str | None:
            mention = f"<@{notify_user_id}> " if notify_user_id else ""
            with contextlib.suppress(discord.HTTPException):
                await thread.send(f"❓ {mention}{question}\n-# Just type your answer here.")
            await report(f"❓ Needs your answer: {question}")
            return await self.wait_for_reply(thread.id, timeout=ASK_TIMEOUT_SECONDS)

        loop = TaskLoop(
            plan_path=work_plan, repo_dir=work_dir, run_round=run_round, ask=ask, report=report
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
        )
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

        self._resume_task = asyncio.create_task(resume_when_ready())

    async def _drive(self, running: _Running, report: Any) -> LoopOutcome:
        try:
            while True:
                outcome = await running.loop.run()
                if outcome.status == Status.COMPLETE:
                    if await self._wrap_up(running) == "fix":
                        continue
                    break
                # Stuck, paused or stopped: the build waits instead of being
                # forgotten, so "keep going" picks up where it left off.
                if await self._park(running, outcome) == "gone":
                    break
            self._store.remove(str(running.repo_dir))
            return outcome
        except asyncio.CancelledError:
            # Bot shutting down: keep the record so startup resumes this build.
            raise
        except Exception as exc:
            logger.exception("task loop crashed in %s", running.repo_dir)
            self._store.remove(str(running.repo_dir))
            with contextlib.suppress(Exception):
                await report(f"💥 Task loop crashed: {exc}")
            raise
        finally:
            self._running.pop(running.repo_dir, None)

    async def _wrap_up(self, running: _Running) -> str | None:
        """The ending: the bot checks the work itself, posts one card, waits for a verdict.

        Nothing to open: the card says what got done and what passed. "looks good"
        adds the work to the project on this computer (never GitHub); "fix" turns
        failed checks into a fix step; anything else becomes a fix step as typed.
        Returns "kept" or "fix".
        """
        assert running.copy is not None
        target, report = running.report_target, running.report
        plan_text = running.copy.plan_path.read_text(encoding="utf-8", errors="replace")
        checked, _ = count_tasks(plan_text)
        mention = f" <@{running.notify_user_id}>" if running.notify_user_id else ""

        results = await self._check_it_myself(running, plan_text)
        fails = [what for kind, what in results if kind == "fail"]
        with contextlib.suppress(discord.HTTPException):
            await target.send(
                f"🏁 **{running.repo_dir.name} is finished**{mention}",
                embed=finished_card(running.repo_dir.name, checked, running.recaps or [], results),
            )

        running.in_review = True
        try:
            while True:
                reply = await self.wait_for_reply(
                    running.report_channel_id, timeout=VERDICT_REMIND_SECONDS
                )
                if reply is None:
                    with contextlib.suppress(discord.HTTPException):
                        await target.send(
                            f"⏰ Still waiting: {running.repo_dir.name} is finished. Type "
                            f"**looks good** to keep it, or tell me what's wrong.{mention}"
                        )
                    continue
                verdict = parked_choice(reply)
                if verdict == "throw":
                    await remove_work_copy(running.copy)
                    with contextlib.suppress(discord.HTTPException):
                        await target.send("🗑️ Thrown away. Your real project was never touched.")
                    with contextlib.suppress(Exception):
                        await running.thread.delete()
                    return "kept"
                if not is_looks_good(reply) and verdict != "finish":
                    what = reply
                    if reply.strip().lower().rstrip(".!") in _FIX_WORDS and fails:
                        what = "make these checks pass: " + "; ".join(fails)
                    append_fix_task(running.copy.plan_path, what)
                    await commit_all(running.copy.path, f"gowork: fix requested: {what[:60]}")
                    await report(f"🔧 Got it, fixing: “{what[:200]}”")
                    return "fix"
                ok, message = await keep_work(running.copy)
                if not ok:
                    with contextlib.suppress(discord.HTTPException):
                        await target.send(
                            f"⚠️ I couldn't keep it yet: {message}. Your build is safe. "
                            "Sort that out and type **looks good** again."
                        )
                    continue
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
        }.get(outcome.status, "⏹️ **Stopped.**")
        ask = (
            f"{why}\nNothing is lost: the finished steps are kept.\n"
            "Type **keep going** to try again (add a hint if you have one), "
            "**skip** to skip this step, **wrap up** to keep what's done and end here, "
            f"or **throw it away** to delete this build.{mention}"
        )
        with contextlib.suppress(discord.HTTPException):
            await target.send(ask[:1900])
        reply = await self._wait_parked(running, ask)
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

    async def _wait_parked(self, running: _Running, ask: str) -> str | None:
        """Wait for keep going / skip / wrap up / throw it away.

        Anything else typed meanwhile goes to the normal chat, and the build keeps
        waiting. Returns None when a new plan in this project closes the build.
        """
        running.wake = running.wake or asyncio.Event()
        running.in_review = True
        try:
            while not running.auto_finish:
                reply_task = asyncio.ensure_future(
                    self.wait_for_reply(
                        running.report_channel_id,
                        timeout=VERDICT_REMIND_SECONDS,
                        accept=lambda text: parked_choice(text) is not None,
                    )
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
                    await running.report_target.send(
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
        checks = plan_try_checks(plan_text)
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
        try:
            return await self.start_loop(
                parent,
                plan_path,
                report_to=report_to,
                notify_user_id=notify_user_id,
                harness=harness,
                model=model,
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
        lines.append(f"**A)** Same as this thread ({current or 'its usual AI'})")
        for i, (harness, model, note) in enumerate(options, start=1):
            tail = f" — {note}" if note else ""
            lines.append(f"**{choice_letter(i)})** {harness} · `{model}`{tail}")
        for chunk in _chunks(lines):
            await channel.send(chunk)
        for _ in range(2):
            reply = await self.wait_for_reply(channel.id, timeout=PICK_TIMEOUT_SECONDS)
            if reply is None:
                return None
            m = _LETTER_REPLY_RE.match(reply)
            if m is not None:
                wanted = m.group(1).upper()
                if wanted == "A" and current:
                    return current, None
                for i, (harness, model, _note) in enumerate(options, start=1):
                    if choice_letter(i) == wanted:
                        return harness, model
            picked = parse_harness_reply(reply, current)
            if picked:
                return picked
            with contextlib.suppress(discord.HTTPException):
                await channel.send("Just type the letter of the AI you want, e.g. `B`.")
        return None

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
        try:
            thread = await self.start_loop(
                parent,
                plan,
                report_to=report_to,
                notify_user_id=interaction.user.id,
                harness=harness,
                model=model,
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
