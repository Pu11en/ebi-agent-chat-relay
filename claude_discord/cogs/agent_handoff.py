"""AgentHandoffCog — the one listener that turns bot messages into work.

``ClaudeChatCog.on_message`` ignores every bot-authored message, and that
guard stays exactly as it is: any bot response could otherwise start another
model. This Cog is the deliberate exception, and it is narrow by
construction:

* it listens only in the configured ``agent-handoffs`` channel and the
  threads under it (:class:`~claude_discord.handoff_config.HandoffConfig`),
* it parses only the strict envelope (:mod:`claude_discord.handoff_discord`),
* it accepts an event only when the Discord author is the mapped bot for the
  packet's sender and the packet is addressed to this agent, and
* only a ``task`` event can create a job — acks, states, questions, answers
  and results are mirrored onto the ledger and can never start execution.

The ledger is written before anything is scheduled; the executor
(:mod:`claude_discord.handoff_executor`) is asked to run *after* the task is
stored and acknowledged, so a redelivered starter finds the existing job.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from discord.ext import commands

from claude_code_core.handoffs.protocol import HandoffEvent, HandoffEventKind, HandoffTask
from claude_code_core.handoffs.state import HandoffJob

from ..handoff_config import HandoffConfig, HandoffTrustError
from ..handoff_discord import ensure_job_thread, parse_event_message
from ..handoff_executor import HandoffExecutor, build_handoff_executor
from ..handoff_messages import HandoffEnvelopeError
from ..handoff_progress import HandoffProgressPoster

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from ..database.handoff_repo import HandoffRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HandoffReceipt:
    """What the Cog did with one protocol message."""

    event: HandoffEvent
    job: HandoffJob | None
    created: bool
    duplicate: bool = False


class AgentHandoffCog(commands.Cog):
    """Receive, acknowledge and start trusted handoffs in ``agent-handoffs``."""

    def __init__(
        self,
        bot: Bot,
        *,
        repo: HandoffRepository,
        config: HandoffConfig,
        executor: HandoffExecutor | None = None,
        poster: HandoffProgressPoster | None = None,
        chat: Any | None = None,
        fallback_lookup_root: str | None = None,
        start_loops: bool = True,
    ) -> None:
        self.bot = bot
        self._repo = repo
        self._config = config
        self._chat = chat
        self._poster = poster or HandoffProgressPoster(
            repo=repo,
            local_agent_id=config.local_agent_id,
            thread_lookup=self.lookup_channel,
        )
        self._executor = executor or build_handoff_executor(
            repo=repo,
            local_agent_id=config.local_agent_id,
            fallback_lookup_root=fallback_lookup_root,
            thread_lookup=self.lookup_channel,
            on_transition=self._poster,
        )
        self._start_loops = start_loops
        self._run_lock = asyncio.Lock()

    @property
    def config(self) -> HandoffConfig:
        return self._config

    @property
    def executor(self) -> HandoffExecutor:
        return self._executor

    # -- Discord plumbing ----------------------------------------------------

    async def lookup_channel(self, channel_id: int) -> Any | None:
        """A channel or thread by id, from cache first, then the API."""
        found = self.bot.get_channel(channel_id)
        if found is not None:
            return found
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception:
            return None

    def _chat_cog(self) -> Any | None:
        if self._chat is not None:
            return self._chat
        return self.bot.cogs.get("ClaudeChatCog")

    # -- the listener --------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: Any) -> None:
        if not getattr(getattr(message, "author", None), "bot", False):
            return
        if not self._config.in_handoff_scope(getattr(message, "channel", None)):
            return
        await self.handle_message(message)

    async def handle_message(
        self, message: Any, *, now: datetime | None = None
    ) -> HandoffReceipt | None:
        """Apply one bot message from the handoff scope; ``None`` when it is not protocol."""
        try:
            event = parse_event_message(getattr(message, "content", "") or "")
        except HandoffEnvelopeError as exc:
            logger.info("ignoring malformed handoff message %s: %s", message.id, exc)
            return None
        if event is None:
            return None
        try:
            self._config.verify_inbound(message, event)
        except HandoffTrustError as exc:
            logger.warning("refusing handoff event %s: %s", event.event_id, exc)
            return None
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        if event.kind is HandoffEventKind.TASK:
            return await self._receive_task(message, event, stamp)
        return await self._receive_event(message, event, stamp)

    async def _receive_task(
        self, message: Any, event: HandoffEvent, now: datetime
    ) -> HandoffReceipt | None:
        task = event.task
        if task is None:  # pragma: no cover — the protocol refuses this earlier
            return None
        created, job = await self._repo.record_task(task, now=now)
        event_new = await self._repo.record_event(event)
        if not created:
            logger.info("handoff %s redelivered; job is %s", task.task_id, job.state.value)
            await self._remember_job_thread(message, task)
            return HandoffReceipt(event=event, job=job, created=False, duplicate=not event_new)
        await self._remember_job_thread(message, task)
        await self._poster.announce_accepted(task, now=now)
        await self.run_executor(now=now)
        return HandoffReceipt(event=event, job=job, created=True)

    async def _receive_event(
        self, message: Any, event: HandoffEvent, now: datetime
    ) -> HandoffReceipt | None:
        """Non-task events are recorded for the audit trail; they never start work."""
        job = await self._repo.get_job(event.task_id, self._config.local_agent_id)
        if job is None:
            logger.info("handoff event %s for unknown task %s", event.event_id, event.task_id)
            return None
        if not await self._repo.record_event(event):
            return HandoffReceipt(event=event, job=job, created=False, duplicate=True)
        return HandoffReceipt(event=event, job=job, created=False)

    async def _remember_job_thread(self, message: Any, task: HandoffTask) -> None:
        """Bind the job to its thread: the one on the starter, or the one we are in."""
        local = self._config.local_agent_id
        if await self._repo.get_job_thread(task.task_id, local) is not None:
            return
        channel: Any = getattr(message, "channel", None)
        if channel is not None and getattr(channel, "parent_id", None) == self._config.channel_id:
            await self._repo.set_job_thread(task.task_id, local, int(channel.id))
            return
        try:
            thread = await ensure_job_thread(message, task.task_id, fetch_channel=self._fetch)
        except Exception:
            logger.warning("could not open the job thread for %s", task.task_id, exc_info=True)
            return
        await self._repo.set_job_thread(task.task_id, local, int(thread.id))

    async def _fetch(self, channel_id: int) -> Any:
        return await self.bot.fetch_channel(channel_id)

    # -- execution -----------------------------------------------------------

    async def run_executor(self, *, now: datetime | None = None) -> None:
        """Start whatever is accepted or queued, one pass, never concurrently."""
        chat = self._chat_cog()
        if chat is None:
            logger.warning("no ClaudeChatCog loaded; handoffs stay queued")
            return
        parent = await self.lookup_channel(self._config.channel_id)
        async with self._run_lock:
            try:
                await self._executor.run_ready(chat=chat, parent_channel=parent, now=now)
            except Exception:
                logger.exception("handoff executor pass failed")


__all__ = ["AgentHandoffCog", "HandoffReceipt"]
