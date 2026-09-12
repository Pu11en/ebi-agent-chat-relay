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
    TaskLoop,
    count_tasks,
    list_plans,
    take_snapshot,
)
from claude_code_core.work_copy import WorkCopy, WorkCopyError, create_work_copy

from ..backend_settings import ALL_BACKENDS

if TYPE_CHECKING:
    from .claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

#: How long a question waits for a typed reply before the loop pauses.
ASK_TIMEOUT_SECONDS = 12 * 60 * 60
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


def parse_plan_reply(text: str, plans: list[Path]) -> Path | None:
    """A typed number ("2") or a word from the plan's name ("the fix one")."""
    stripped = text.strip()
    if stripped.isdigit():
        i = int(stripped) - 1
        return plans[i] if 0 <= i < len(plans) else None
    words = {w for w in _WORD_RE.findall(text.lower()) if len(w) >= 3 and w not in _FILLER}
    for plan in plans:
        name = plan.stem.lower()
        if any(w in name for w in words - {"plan", "one"}):
            return plan
    return None


@dataclass
class _Running:
    loop: TaskLoop
    repo_dir: Path
    worker_thread_id: int
    report_channel_id: int
    copy: WorkCopy | None = None
    task: asyncio.Task[LoopOutcome] | None = None


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

    def _chat(self) -> ClaudeChatCog:
        cog: Any = self.bot.cogs.get("ClaudeChatCog")
        if cog is None:
            raise RuntimeError("ClaudeChatCog is not loaded")
        return cog

    @property
    def running(self) -> list[_Running]:
        return list(self._running.values())

    async def wait_for_reply(self, channel_id: int, *, timeout: float) -> str | None:
        """Wait for the next message typed in *channel_id* (see take_message)."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._waiters[channel_id] = future
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            if self._waiters.get(channel_id) is future:
                self._waiters.pop(channel_id, None)

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
            report_channel_id=getattr(report_target, "id", channel.id),
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
        running = _Running(loop, repo_dir, thread.id, record.report_channel_id, copy=copy)
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
            outcome = await running.loop.run()
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

    def stop_for(self, channel_id: int) -> _Running | None:
        """Ask the loop tied to *channel_id* (worker or report channel) to stop."""
        for running in self._running.values():
            if channel_id in (running.worker_thread_id, running.report_channel_id):
                running.loop.request_stop()
                return running
        return None

    def _authorized(self, user_id: int) -> bool:
        return self._allowed_user_ids is None or user_id in self._allowed_user_ids

    async def _pick_plan(self, channel: Any) -> str | None:
        """The project's unfinished plans: one is used directly, several are listed."""
        record = None
        with contextlib.suppress(Exception):
            record = await self._chat().repo.get(channel.id)
        workdir = (record.working_dir if record else None) or getattr(
            self._chat().runner, "working_dir", None
        )
        if not isinstance(workdir, str) or not workdir:
            return None
        project = Path(workdir)
        plans = list_plans(project)[:5]
        if len(plans) <= 1:
            return str(plans[0]) if plans else None
        lines = ["Which plan? Just type the number or a word from its name:"]
        for i, plan in enumerate(plans, 1):
            try:
                checked, unchecked = count_tasks(plan.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                checked, unchecked = 0, 0
            try:
                name = str(plan.relative_to(project))
            except ValueError:
                name = plan.name
            lines.append(f"**{i}.** `{name}` · {unchecked} of {checked + unchecked} left")
        await channel.send("\n".join(lines))
        reply = await self.wait_for_reply(channel.id, timeout=PICK_TIMEOUT_SECONDS)
        picked = parse_plan_reply(reply or "", plans)
        return str(picked) if picked else None

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
        plan = plan or await self._pick_plan(channel)
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
