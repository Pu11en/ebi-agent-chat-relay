"""TaskLoopCog — run a plan one task at a time, a fresh session per task.

The Discord side of :mod:`claude_code_core.task_loop`. A planner (a person with
``/gowork``, or a planner session via ``POST /api/loops`` after the
person said yes) points it at a plan file. The cog opens one worker thread next
to the planner and runs rounds there: each round is a new session on whatever
harness the thread uses, so the loop works the same on Claude Code, Codex and
DSH. Progress lines go back to where the loop was started; yes/no questions are
asked in the worker thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from claude_code_core.frontend import Choice, ChoicePrompt, FormField, FormPrompt
from claude_code_core.task_loop import LoopOutcome, TaskLoop, find_plan, take_snapshot
from claude_code_core.work_copy import WorkCopy, WorkCopyError, create_work_copy

from ..backend_settings import ALL_BACKENDS
from ..model_catalog import claude_model_choices, codex_model_choices, dsh_model_choices
from ..surface import DiscordSurface
from .backend_command import SUGGESTED_MODELS

if TYPE_CHECKING:
    from .claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

#: How long a yes/no question waits before the loop pauses instead.
ASK_TIMEOUT_SECONDS = 12 * 60 * 60

#: Report lines that need the person: a question, a stop, the end. Progress
#: ("✅ Task 3 of 9 done") posts quietly — Drew chose pings only when needed.
_PING_PREFIXES = ("❓", "🛑", "🏁", "⏸️", "💥")

#: Choice value for "none of these — let me type the model name".
TYPE_OWN = "__type_own__"
#: How long the harness/model pickers wait for a tap.
PICK_TIMEOUT_SECONDS = 10 * 60
_HARNESS_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "dsh": "DeepSeek Harness (DSH)",
    "local": "Local model",
    "agui": "AG-UI",
}

_YES = "yes"
_NO = "no"


@dataclass
class _Running:
    loop: TaskLoop
    repo_dir: Path
    worker_thread_id: int
    report_channel_id: int
    copy: WorkCopy | None = None
    task: asyncio.Task[LoopOutcome] | None = None


def harness_prompt(current: str | None) -> ChoicePrompt:
    """Step one of starting a build: which harness does the work."""
    choices = tuple(
        Choice(
            value=b,
            label=f"{_HARNESS_LABELS.get(b, b)}{' (current)' if b == current else ''}",
            style="positive" if b == current else "default",
        )
        for b in ALL_BACKENDS
    )
    return ChoicePrompt(
        question="Which harness should do the work?",
        header="🔁 Start the build: 1 of 2",
        choices=choices,
        timeout_seconds=PICK_TIMEOUT_SECONDS,
    )


def model_prompt(backend: str, options: list[tuple[str, str]]) -> ChoicePrompt:
    """Step two: which model, from that harness's list, or typed by hand."""
    choices = [
        Choice(value=value, label=value[:80], description=(desc or None) and desc[:100])
        for value, desc in options[:23]
    ]
    choices.append(Choice(value=TYPE_OWN, label="✏️ Type another model"))
    return ChoicePrompt(
        question=f"Which model should {_HARNESS_LABELS.get(backend, backend)} use?",
        header="🔁 Start the build: 2 of 2",
        choices=tuple(choices),
        timeout_seconds=PICK_TIMEOUT_SECONDS,
    )


async def model_options(backend: str) -> list[tuple[str, str]]:
    """The same model list `/model` suggests for *backend*."""
    fallback = list(SUGGESTED_MODELS.get(backend, []))
    if backend == "claude":
        return await claude_model_choices(fallback=fallback)
    if backend == "codex":
        return codex_model_choices(fallback=fallback)
    if backend == "dsh":
        return await dsh_model_choices(fallback=fallback)
    return fallback


async def pick_harness_and_model(
    surface: DiscordSurface, current: str | None
) -> tuple[str, str | None] | None:
    """Ask with buttons. Returns (harness, model or None for its default), or None."""
    picked = await surface.prompt_choice(harness_prompt(current))
    if not picked:
        return None
    backend = picked[0]
    options = await model_options(backend)
    picked_model = await surface.prompt_choice(model_prompt(backend, options))
    if not picked_model:
        return None
    if picked_model[0] != TYPE_OWN:
        return backend, picked_model[0]
    form = await surface.prompt_form(
        FormPrompt(
            title="Which model?",
            fields=(
                FormField(
                    key="model",
                    label="Model name",
                    kind="text",
                    required=True,
                    placeholder="e.g. glm-5.3-flash",
                ),
            ),
            timeout_seconds=PICK_TIMEOUT_SECONDS,
        )
    )
    typed = ((form or {}).get("model") or "").strip()
    return (backend, typed) if typed else None


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
    ) -> None:
        self.bot = bot
        self._allowed_user_ids = allowed_user_ids
        #: Where each build's own copy of the project is made (None = default).
        self._work_root = work_root
        self._running: dict[Path, _Running] = {}

    def _chat(self) -> ClaudeChatCog:
        cog: Any = self.bot.cogs.get("ClaudeChatCog")
        if cog is None:
            raise RuntimeError("ClaudeChatCog is not loaded")
        return cog

    @property
    def running(self) -> list[_Running]:
        return list(self._running.values())

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
        work_dir, work_plan = copy.path, copy.plan_path

        thread = await chat.spawn_session(
            channel,
            f"🔁 **Task loop** for `{plan}`\n"
            f"{snap.unchecked} of {snap.checked + snap.unchecked} tasks left. "
            "Each task runs in a fresh session on a separate copy of the project; "
            "I'll ask here if a yes/no is needed.",
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
        # Workers start a fresh session every round; a "start fresh?" nudge
        # there would only interrupt the loop.
        nudger = getattr(chat, "context_nudger", None)
        if nudger is not None:
            nudger.skip_thread_ids.add(thread.id)
        dashboard = None
        with contextlib.suppress(Exception):
            dashboard = chat._get_dashboard()
        if dashboard is not None:
            dashboard.quiet_thread_ids.add(thread.id)
        report_target: discord.abc.Messageable = report_to or channel
        report_id = getattr(report_target, "id", channel.id)

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

        async def ask(question: str) -> bool | None:
            await report(f"❓ Needs your yes/no: {question}")
            answer = await DiscordSurface(thread).prompt_choice(
                ChoicePrompt(
                    question=question,
                    header="🔁 Yes or no?",
                    choices=(
                        Choice(value=_YES, label="Yes", style="positive"),
                        Choice(value=_NO, label="No", style="destructive"),
                    ),
                    timeout_seconds=ASK_TIMEOUT_SECONDS,
                )
            )
            if answer is None:
                return None
            return _YES in answer

        loop = TaskLoop(
            plan_path=work_plan, repo_dir=work_dir, run_round=run_round, ask=ask, report=report
        )
        running = _Running(loop, repo_dir, thread.id, report_id, copy=copy)
        self._running[repo_dir] = running
        running.task = asyncio.create_task(self._drive(running, report))
        await report(f"▶️ Task loop started: {snap.unchecked} tasks to go")
        return thread

    async def _drive(self, running: _Running, report: Any) -> LoopOutcome:
        try:
            return await running.loop.run()
        except Exception as exc:
            logger.exception("task loop crashed in %s", running.repo_dir)
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

    async def _default_plan(self, channel: Any) -> str | None:
        """Find a plan in the project this thread is bound to."""
        record = None
        with contextlib.suppress(Exception):
            record = await self._chat().repo.get(channel.id)
        workdir = (record.working_dir if record else None) or getattr(
            self._chat().runner, "working_dir", None
        )
        if not isinstance(workdir, str) or not workdir:
            return None
        found = find_plan(Path(workdir))
        return str(found) if found else None

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
        plan = plan or await self._default_plan(channel)
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
        picked = await pick_harness_and_model(DiscordSurface(report_to), current)
        if picked is None:
            await interaction.followup.send("Not started: no harness/model was picked.")
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
