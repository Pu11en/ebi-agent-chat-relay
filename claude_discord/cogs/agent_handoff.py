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

from discord.ext import commands, tasks

from claude_code_core.handoffs.protocol import HandoffEvent, HandoffEventKind, HandoffTask
from claude_code_core.handoffs.state import HandoffJob, HandoffStateError, apply_event

from ..handoff_config import HandoffConfig, HandoffTrustError
from ..handoff_discord import (
    channel_in_guild,
    ensure_job_thread,
    parse_event_message,
    post_task_starter,
    short_task_id,
)
from ..handoff_executor import HandoffExecutor, build_handoff_executor
from ..handoff_messages import HandoffEnvelopeError
from ..handoff_progress import HandoffProgressPoster
from ..handoff_return import deliver_pending_handoff_results

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from ..database.handoff_repo import HandoffRepository
    from ..handoff_projects import ProjectResolver

logger = logging.getLogger(__name__)


SCAN_LIMIT = 200
MAX_ORIGIN_LINE_CHARS = 400
DELIVERY_INTERVAL_SECONDS = 60
# A peer's event on a job *we* own is stored in the same ledger our own
# events draw their sequence numbers from. A sequence far ahead of the
# conversation (a QUESTION at MAX_SEQUENCE) would leave next_sequence()
# nothing to mint and wedge the job; anything past this gap is refused.
MAX_PEER_SEQUENCE_GAP = 100


@dataclass(frozen=True)
class ReconnectReport:
    """What startup reconciliation found and did."""

    requeued: list[str]
    discovered: list[str]


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
        project_resolver: ProjectResolver | None = None,
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
            resolver=project_resolver,
        )
        if self._executor.on_transition is None:
            # An injected executor still reports through this Cog's poster,
            # otherwise the job thread would show the ack and nothing after it.
            self._executor.on_transition = self._poster
        self._start_loops = start_loops
        self._run_lock = asyncio.Lock()
        self._restart_reconciled = False

    async def cog_load(self) -> None:
        if self._start_loops and not self.delivery_loop.is_running():
            self.delivery_loop.start()

    async def cog_unload(self) -> None:
        self.delivery_loop.cancel()

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
        """Ack, state, question, answer and result events: mirrored, never executed.

        Two ledgers can hold this task id. As the *origin* we hold
        ``(task_id, recipient=event.sender)`` for a task we created, and the
        sender's events move that mirror. As the *recipient* we hold
        ``(task_id, local)``, and nothing a remote agent says may move it —
        only the task's own sender may add a question or an answer to it.
        """
        local = self._config.local_agent_id
        origin_job = await self._repo.get_job(event.task_id, event.sender)
        origin_task = await self._repo.get_task(event.task_id, event.sender)
        if origin_job is not None and origin_task is not None and origin_task.sender == local:
            return await self._mirror_for_origin(origin_task, origin_job, event, now)

        own_job = await self._repo.get_job(event.task_id, local)
        own_task = await self._repo.get_task(event.task_id, local)
        if own_job is not None and own_task is not None:
            if event.sender != own_task.sender or event.kind not in (
                HandoffEventKind.QUESTION,
                HandoffEventKind.ANSWER,
            ):
                logger.warning(
                    "refusing %s event %s from %s about a job this agent owns",
                    event.kind.value,
                    event.event_id,
                    event.sender,
                )
                return None
            last = await self._repo.last_sequence(event.task_id) or 0
            if event.sequence > last + MAX_PEER_SEQUENCE_GAP:
                logger.warning(
                    "refusing %s event %s from %s: sequence %d is out of band (last %d)",
                    event.kind.value,
                    event.event_id,
                    event.sender,
                    event.sequence,
                    last,
                )
                return None
            duplicate = not await self._repo.record_event(event)
            return HandoffReceipt(event=event, job=own_job, created=False, duplicate=duplicate)

        logger.info("handoff event %s for unknown task %s", event.event_id, event.task_id)
        return None

    async def _mirror_for_origin(
        self, task: HandoffTask, job: HandoffJob, event: HandoffEvent, now: datetime
    ) -> HandoffReceipt:
        if not await self._repo.record_event(event):
            return HandoffReceipt(event=event, job=job, created=False, duplicate=True)
        try:
            transition = apply_event(job, event, now=now)
        except HandoffStateError as exc:
            logger.info("handoff %s: %s not applied (%s)", task.task_id, event.kind.value, exc)
            return HandoffReceipt(event=event, job=job, created=False)
        current = job
        if transition.changed and await self._repo.save_transition(transition):
            current = transition.job
        await self._tell_origin(task, event)
        return HandoffReceipt(event=event, job=current, created=False)

    async def _tell_origin(self, task: HandoffTask, event: HandoffEvent) -> None:
        """Acks and questions go to the origin conversation as prose.

        Blockers and results are delivered there by the recipient itself (its
        outbox), so posting them again here would double every result.
        """
        sid = short_task_id(task.task_id)
        if event.kind is HandoffEventKind.ACK:
            goal = " ".join(task.goal.split())[:MAX_ORIGIN_LINE_CHARS]
            text = f"🤝 {event.sender} accepted handoff `{sid}`: {goal}"
        elif event.kind is HandoffEventKind.QUESTION:
            question = " ".join(str(event.payload.get("question", "")).split())
            text = (
                f"❓ {event.sender} asks about handoff `{sid}`: {question[:MAX_ORIGIN_LINE_CHARS]}"
            )
        else:
            return
        target_id = task.reply_to.thread_id or task.reply_to.channel_id
        target = await self.lookup_channel(target_id)
        if target is None or not hasattr(target, "send"):
            logger.warning("handoff %s origin is unreachable for %s", task.task_id, event.kind)
            return
        # The resolved channel must be in the reply guild *and* the configured
        # one; an id the bot can see elsewhere is not the origin conversation.
        if not (
            channel_in_guild(target, task.reply_to.guild_id)
            and channel_in_guild(target, self._config.guild_id)
        ):
            logger.warning(
                "handoff %s: refusing to post %s to %s, not in the handoff guild",
                task.task_id,
                event.kind.value,
                target_id,
            )
            return
        try:
            await target.send(text)
        except Exception:
            logger.warning("could not post handoff %s to the origin", event.kind, exc_info=True)

    # -- sending -------------------------------------------------------------

    async def send_task(
        self, event: HandoffEvent, *, now: datetime | None = None
    ) -> tuple[Any, Any]:
        """Hand a task to a peer: ledger first, then one starter and its thread."""
        task = event.task
        if event.kind is not HandoffEventKind.TASK or task is None:
            raise ValueError("only a task event can be sent as a handoff")
        if task.sender != self._config.local_agent_id:
            raise ValueError(f"task sender {task.sender!r} is not this agent")
        if self._config.bot_for_agent(task.recipient) is None:
            raise ValueError(f"recipient {task.recipient!r} is not a configured peer")
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        channel = await self.lookup_channel(self._config.channel_id)
        if channel is None or not hasattr(channel, "send"):
            raise RuntimeError(f"handoff channel {self._config.channel_id} is unreachable")
        await self._repo.record_task(task, now=stamp)
        await self._repo.record_event(event)
        starter, thread = await post_task_starter(channel, event)
        await self._repo.set_job_thread(task.task_id, task.recipient, int(thread.id))
        return starter, thread

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

    # -- reconnect -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Every (re)connect reconciles: the scan is idempotent, so repeats are free."""
        try:
            await self.reconcile_on_reconnect()
        except Exception:
            logger.exception("handoff reconciliation failed")

    async def reconcile_on_reconnect(self, *, now: datetime | None = None) -> ReconnectReport:
        """Recover work addressed to this agent while it was away.

        Two sources, both idempotent against the ledger: jobs stored as
        ``running`` with no process behind them are requeued (same attempt),
        and recent starters in the handoff channel that the ledger has never
        seen are stored and acknowledged. Then one executor pass runs.

        The restart part runs once per process. ``on_ready`` also fires on a
        gateway reconnect, and a job that is genuinely running in *this*
        process must not be requeued underneath itself.
        """
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        requeued: list[str] = []
        if not self._restart_reconciled:
            self._restart_reconciled = True
            requeued = [
                job.task_id for job in await self._executor.reconcile_after_restart(now=stamp)
            ]
        discovered = await self._scan_starters(stamp)
        await self.run_executor(now=stamp)
        await self.deliver_pending(now=stamp)
        return ReconnectReport(requeued=requeued, discovered=discovered)

    async def _scan_starters(self, now: datetime) -> list[str]:
        channel: Any = await self.lookup_channel(self._config.channel_id)
        if channel is None or not callable(getattr(channel, "history", None)):
            logger.warning("handoff channel %s is not readable", self._config.channel_id)
            return []
        discovered: list[str] = []
        local = self._config.local_agent_id
        try:
            async for message in channel.history(
                limit=SCAN_LIMIT, after=now - self._config.retention, oldest_first=True
            ):
                task_id = await self._discover_starter(message, now, local)
                if task_id is not None:
                    discovered.append(task_id)
        except Exception:
            logger.warning("handoff starter scan failed", exc_info=True)
        return discovered

    async def _discover_starter(self, message: Any, now: datetime, local: str) -> str | None:
        if not getattr(getattr(message, "author", None), "bot", False):
            return None
        try:
            event = parse_event_message(getattr(message, "content", "") or "")
        except HandoffEnvelopeError:
            return None
        if event is None or event.kind is not HandoffEventKind.TASK or event.task is None:
            return None
        if event.recipient != local:
            return None
        if await self._repo.get_job(event.task_id, local) is not None:
            await self._remember_job_thread(message, event.task)
            return None
        try:
            self._config.verify_inbound(message, event)
        except HandoffTrustError as exc:
            logger.warning("skipping untrusted starter %s: %s", message.id, exc)
            return None
        task = event.task
        created, _job = await self._repo.record_task(task, now=now)
        await self._repo.record_event(event)
        await self._remember_job_thread(message, task)
        if not created:
            return None
        await self._poster.announce_accepted(task, now=now)
        return task.task_id

    # -- result delivery -----------------------------------------------------

    @tasks.loop(seconds=DELIVERY_INTERVAL_SECONDS)
    async def delivery_loop(self) -> None:
        """Retry origin deliveries from the outbox. Never raises."""
        try:
            await self.deliver_pending()
        except Exception:
            logger.exception("handoff delivery pass failed")

    @delivery_loop.before_loop
    async def _before_delivery(self) -> None:
        bot: Any = self.bot
        if callable(getattr(bot, "wait_until_ready", None)):
            await bot.wait_until_ready()

    async def deliver_pending(self, *, now: datetime | None = None) -> int:
        """One outbox pass; returns how many results reached their origin."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        before = await self._repo.pending_deliveries(now=stamp)
        if not before:
            return 0
        await deliver_pending_handoff_results(repo=self._repo, bot=self.bot, now=stamp)
        still = {entry.id for entry in await self._repo.pending_deliveries(now=stamp)}
        delivered = 0
        for entry in before:
            current = await self._repo.get_delivery(entry.id)
            if current is not None and current.delivered_at is not None:
                delivered += 1
        logger.debug("handoff outbox pass: %d delivered, %d still due", delivered, len(still))
        return delivered

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


__all__ = ["AgentHandoffCog", "HandoffReceipt", "ReconnectReport"]
