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
    count_tasks,
    is_looks_good,
    list_plans,
    list_plans_across,
    plan_open_url,
    plan_try_checks,
    plan_try_command,
    project_for_thread,
    project_of,
    take_snapshot,
)
from claude_code_core.work_copy import (
    WorkCopy,
    WorkCopyError,
    commit_all,
    create_work_copy,
    keep_work,
)

from ..backend_settings import ALL_BACKENDS

if TYPE_CHECKING:
    from .claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

#: How long a question waits for a typed reply before the loop pauses.
ASK_TIMEOUT_SECONDS = 12 * 60 * 60
#: How long a finished build waits for "looks good" or a fix before it pauses.
VERDICT_TIMEOUT_SECONDS = 24 * 60 * 60
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
    preview: asyncio.subprocess.Process | None = None


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
        report = self._launch(record, thread, report_target)
        await report(f"▶️ Task loop started: {snap.unchecked} tasks to go")
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
        notify_user_id = record.notify_user_id

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
                if outcome.status != Status.COMPLETE:
                    break
                if await self._wrap_up(running) != "fix":
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
        """The ending: summary, a local copy to try, then keep it or fix it.

        Returns "kept", "fix", or None when nobody answered in time.
        """
        assert running.copy is not None
        target, report = running.report_target, running.report
        plan_text = running.copy.plan_path.read_text(encoding="utf-8", errors="replace")
        checked, unchecked = count_tasks(plan_text)
        mention = f" <@{running.notify_user_id}>" if running.notify_user_id else ""

        lines = [
            f"🏁 **{running.repo_dir.name} is ready to try** ({checked} of {checked} tasks done)"
        ]
        lines += [f"- {r}" for r in (running.recaps or [])[-10:]]
        with contextlib.suppress(discord.HTTPException):
            await target.send("\n".join(lines)[:1900])

        where = await self._start_preview(running, plan_text)
        checks = plan_try_checks(plan_text)[:3]
        ask = [f"**Try it:** {where}"]
        if checks:
            ask.append("**Check these:**")
            ask += [f"{i}. {c}" for i, c in enumerate(checks, 1)]
        ask.append(f"Then just type **looks good** to keep it, or tell me what's off.{mention}")
        with contextlib.suppress(discord.HTTPException):
            await target.send("\n".join(ask)[:1900])

        while True:
            reply = await self.wait_for_reply(
                running.report_channel_id, timeout=VERDICT_TIMEOUT_SECONDS
            )
            if reply is None:
                await self._stop_preview(running)
                with contextlib.suppress(discord.HTTPException):
                    await target.send(
                        "⏸️ Nobody answered, so I stopped the local copy. "
                        "The build is kept as it is."
                    )
                return None
            if not is_looks_good(reply):
                await self._stop_preview(running)
                append_fix_task(running.copy.plan_path, reply)
                await commit_all(running.copy.path, f"gowork: fix requested: {reply[:60]}")
                await report(f"🔧 Got it, fixing: “{reply[:200]}”")
                return "fix"
            await self._stop_preview(running)
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

    async def _start_preview(self, running: _Running, plan_text: str) -> str:
        """Start the plan's ``Try:`` command in the build's copy; say where to look."""
        assert running.copy is not None
        argv = plan_try_command(plan_text)
        url = plan_open_url(plan_text)
        if argv is None:
            return url or f"the finished files are in `{running.copy.path}`"
        try:
            running.preview = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(running.copy.path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            return f"I couldn't start it ({exc}); the files are in `{running.copy.path}`"
        await asyncio.sleep(2)
        if running.preview.returncode is not None:
            return f"it didn't stay running; the files are in `{running.copy.path}`"
        return f"{url} (running now)" if url else "it's running now"

    async def _stop_preview(self, running: _Running) -> None:
        proc, running.preview = running.preview, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)

    def stop_for(self, channel_id: int) -> _Running | None:
        """Ask the loop tied to *channel_id* (worker or report channel) to stop."""
        for running in self._running.values():
            if channel_id in (running.worker_thread_id, running.report_channel_id):
                running.loop.request_stop()
                return running
        return None

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

    async def _ask_harness(
        self, channel: Any, current: str | None
    ) -> tuple[str, str | None] | None:
        """Ask in plain words which harness and model; up to two tries."""
        example = f"`same` ({current})" if current else "`same`"
        prompt = (
            "Which harness and model should do the work? Just type it, e.g. "
            f"`claude sonnet`, `dsh deepseek-pro`, `codex`, or {example}."
        )
        for _ in range(2):
            await channel.send(prompt)
            reply = await self.wait_for_reply(channel.id, timeout=PICK_TIMEOUT_SECONDS)
            if reply is None:
                return None
            picked = parse_harness_reply(reply, current)
            if picked:
                return picked
            prompt = (
                f"I didn't catch a harness in “{reply[:80]}”. Type one of: "
                f"{', '.join(ALL_BACKENDS)} (plus a model if you like, e.g. `claude opus`)."
            )
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
