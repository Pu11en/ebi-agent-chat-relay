"""Receive trusted Discord handoff packets into the durable handoff ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from claude_code_core.handoffs.protocol import HandoffEvent, HandoffEventKind

from .database.handoff_repo import HandoffJob, HandoffRepository
from .handoff_messages import parse_handoff_message


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

    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    created, job = await repo.record_task(event.task, now=stamp)
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
    await message.channel.send(f"✅ Handoff {verb}: {task.goal}")


__all__ = ["HandoffInboxResult", "handle_handoff_message"]
