"""Make every durable handoff transition visible in its one job thread.

The ledger decides; Discord shows. This module is the executor's transition
hook: after a transition is persisted it records the matching protocol event
(ack, state, or the stored result) in the ledger — so the sequence numbers
are the ledger's, not Discord's — and posts one bounded message into the job
thread. A blocker is also sent, as prose, to the origin conversation, because
that is the one state where the origin's human has something to do.

Posting is best-effort and never raises into the executor: a missing thread
costs a log line, not a stuck job. The ledger write comes first, so a
reconnecting bot can rebuild the story from the events even when a post was
dropped.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from claude_code_core.handoffs.protocol import (
    HandoffEvent,
    HandoffEventKind,
    HandoffTask,
    next_sequence,
)
from claude_code_core.handoffs.state import HandoffState, HandoffTrigger, Transition

from .database.handoff_repo import HandoffRepository
from .handoff_discord import render_event_message, short_task_id

logger = logging.getLogger(__name__)

ThreadLookup = Callable[[int], Awaitable[Any | None]]

MAX_ORIGIN_NOTE_CHARS = 600


class HandoffProgressPoster:
    """The ``on_transition`` hook: ledger event first, then one thread post."""

    def __init__(
        self,
        *,
        repo: HandoffRepository,
        local_agent_id: str,
        thread_lookup: ThreadLookup,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repo = repo
        self._agent = local_agent_id.strip().lower()
        self._lookup = thread_lookup
        self._new_id = event_id_factory or (lambda: str(uuid.uuid4()))

    async def __call__(self, task: HandoffTask, transition: Transition) -> None:
        if transition.trigger is HandoffTrigger.OBSERVE:
            return
        event = await self._event_for(task, transition)
        if event is None:
            return
        await self._post_to_job_thread(task, event)
        if transition.state is HandoffState.BLOCKED:
            await self._post_blocker_to_origin(task, transition)

    async def announce_accepted(self, task: HandoffTask, *, now: datetime | None = None) -> None:
        """Post the acknowledgement for a task this agent just stored."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        event = HandoffEvent(
            event_id=self._new_id(),
            kind=HandoffEventKind.ACK,
            task_id=task.task_id,
            sender=self._agent,
            recipient=task.sender,
            sequence=await self._next_sequence(task.task_id),
            created_at=stamp,
            payload={"note": "accepted"},
        )
        await self._repo.record_event(event)
        await self._post_to_job_thread(task, event)

    # -- building the event --------------------------------------------------

    async def _event_for(self, task: HandoffTask, transition: Transition) -> HandoffEvent | None:
        if transition.state.is_terminal:
            # The result event was written with the outbox row; show that one.
            for stored in await self._repo.list_events(task.task_id):
                if stored.kind is HandoffEventKind.RESULT and stored.sender == self._agent:
                    return stored.to_event()
            # A terminal transition without a stored result (expiry, exhausted
            # retries) is still shown, as a state event.
        payload = transition.to_payload()
        event = HandoffEvent(
            event_id=self._new_id(),
            kind=HandoffEventKind.STATE,
            task_id=task.task_id,
            sender=self._agent,
            recipient=task.sender,
            sequence=await self._next_sequence(task.task_id),
            created_at=transition.at,
            payload=payload,
        )
        await self._repo.record_event(event)
        return event

    async def _next_sequence(self, task_id: str) -> int:
        # The task event is sequence 0 whether or not this ledger stored it.
        last = await self._repo.last_sequence(task_id)
        return next_sequence(last if last is not None else 0)

    # -- posting -------------------------------------------------------------

    async def _post_to_job_thread(self, task: HandoffTask, event: HandoffEvent) -> None:
        thread_id = await self._repo.get_job_thread(task.task_id, self._agent)
        if thread_id is None:
            logger.info("handoff %s has no job thread yet; %s not posted", task.task_id, event.kind)
            return
        thread = await self._lookup(thread_id)
        if thread is None:
            logger.warning("handoff %s job thread %s is unreachable", task.task_id, thread_id)
            return
        try:
            await thread.send(render_event_message(event))
        except Exception:
            logger.warning("could not post handoff %s to thread %s", event.kind, thread_id)

    async def _post_blocker_to_origin(self, task: HandoffTask, transition: Transition) -> None:
        target_id = task.reply_to.thread_id or task.reply_to.channel_id
        origin = await self._lookup(target_id)
        if origin is None:
            logger.warning("handoff %s origin %s unreachable for blocker", task.task_id, target_id)
            return
        note = " ".join((transition.note or "needs your authority").split())[:MAX_ORIGIN_NOTE_CHARS]
        text = f"⛔ Handoff `{short_task_id(task.task_id)}` to {self._agent} is blocked: {note}"
        with contextlib.suppress(Exception):
            await origin.send(text)


__all__ = ["HandoffProgressPoster"]
