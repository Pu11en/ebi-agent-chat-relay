"""Receive trusted Discord handoff packets into the durable handoff ledger.

The caller has already decided the *author* is trusted. This module checks
what the packet says about *where* it came from: its origin and reply
coordinates must name the guild the message was actually posted in, so a
trusted bot cannot be used to route a job's result into a guild it was never
seen in. The acknowledgement names the job, never the sender's text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from claude_code_core.handoffs.protocol import HandoffEvent, HandoffEventKind

from .database.handoff_repo import HandoffJob, HandoffRepository
from .handoff_messages import parse_handoff_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HandoffInboxResult:
    """What the inbox did with one Discord handoff packet."""

    event: HandoffEvent
    job: HandoffJob
    accepted: bool
    created: bool
    event_created: bool


async def handle_handoff_message(
    message: Any,
    *,
    repo: HandoffRepository,
    local_agent_id: str,
    now: datetime | None = None,
) -> HandoffInboxResult | None:
    """Store a task handoff addressed to this agent, or return None if unrelated."""
    event = parse_handoff_message(getattr(message, "content", ""))
    if event is None:
        return None
    if event.recipient != local_agent_id.strip().lower():
        return None
    if event.kind is not HandoffEventKind.TASK or event.task is None:
        return None
    task = event.task
    guild_id = getattr(getattr(message, "guild", None), "id", None)
    if (
        not isinstance(guild_id, int)
        or task.origin.guild_id != guild_id
        or task.reply_to.guild_id != guild_id
    ):
        logger.warning(
            "refusing handoff %s from %s: the packet's guild is not the guild it was posted in",
            task.task_id,
            task.sender,
        )
        return None

    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    created, job = await repo.record_task(task, now=stamp)
    event_created = await repo.record_event(event)
    await _acknowledge(message, event=event, created=created)
    return HandoffInboxResult(
        event=event,
        job=job,
        accepted=True,
        created=created,
        event_created=event_created,
    )


async def _acknowledge(message: Any, *, event: HandoffEvent, created: bool) -> None:
    task = event.task
    if task is None:  # pragma: no cover - caller already checks this
        return
    verb = "accepted" if created else "already accepted"
    # The job id, not the goal: the goal is peer-supplied prose and this bot
    # must not be the one that repeats it into the channel.
    await message.channel.send(f"✅ Handoff {verb}: `{task.task_id[:8]}` from {task.sender}")


__all__ = ["HandoffInboxResult", "handle_handoff_message"]
