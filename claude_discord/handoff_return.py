"""Return handoff worker results to the originating Discord conversation."""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from claude_code_core.handoffs.protocol import (
    MAX_PAYLOAD_VALUE_CHARS,
    ConversationCoordinate,
    HandoffEvent,
    HandoffEventKind,
    HandoffTask,
    next_sequence,
)
from claude_code_core.handoffs.state import HandoffTrigger, Transition, apply

from .database.handoff_repo import HandoffRepository

logger = logging.getLogger(__name__)

RESULT_SUMMARY_CHARS = MAX_PAYLOAD_VALUE_CHARS


async def record_and_deliver_handoff_result(
    *,
    repo: HandoffRepository,
    bot: Any,
    task_id: str,
    local_agent_id: str,
    text: str | None,
    error: str | None,
    now: datetime | None = None,
    event_id_factory: Any | None = None,
    on_transition: Callable[[HandoffTask, Transition], Awaitable[None]] | None = None,
) -> bool:
    """Record a worker result and try to send it back to the task origin.

    ``on_transition`` is told about the terminal transition (and handed the
    result event through ``Transition.note``) so the job thread can show it.
    """
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    recipient = local_agent_id.strip().lower()
    task = await repo.get_task(task_id, recipient)
    if task is None:
        raise ValueError(f"no stored handoff {task_id} for recipient {recipient}")

    outcome = "failed" if error else "completed"
    summary = _summary(text=text, error=error)
    last = await repo.last_sequence(task.task_id)
    event = HandoffEvent(
        event_id=_new_event_id(event_id_factory),
        kind=HandoffEventKind.RESULT,
        task_id=task.task_id,
        sender=task.recipient,
        recipient=task.sender,
        sequence=next_sequence(last if last is not None else 0),
        created_at=stamp,
        payload={"outcome": outcome, "summary": summary},
    )

    result_recorded = await repo.record_result(task.task_id, recipient, event=event, now=stamp)
    if result_recorded:
        transition = await _finish_job(
            repo,
            task_id=task.task_id,
            recipient=recipient,
            outcome=outcome,
            now=stamp,
        )
        if transition is not None and on_transition is not None:
            try:
                await on_transition(task, transition)
            except Exception:
                logger.warning("handoff status hook failed for %s", task_id, exc_info=True)

    return await deliver_pending_handoff_results(repo=repo, bot=bot, now=stamp)


async def deliver_pending_handoff_results(
    *,
    repo: HandoffRepository,
    bot: Any,
    now: datetime | None = None,
    limit: int = 20,
) -> bool:
    """Try due outbox deliveries. Returns True when at least one delivery succeeds."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    delivered_any = False
    for entry in await repo.pending_deliveries(now=stamp, limit=limit):
        try:
            target = await _resolve_destination(bot, entry.destination)
            await target.send(_render_result_message(entry.to_event()))
        except Exception as exc:
            logger.warning("Could not deliver handoff result %s", entry.event_id, exc_info=True)
            await repo.record_delivery_failure(entry.id, now=stamp, error=str(exc))
            continue
        await repo.mark_delivered(entry.id, now=stamp)
        await _archive_worker_thread(
            repo,
            bot=bot,
            task_id=entry.task_id,
            recipient=entry.recipient,
        )
        delivered_any = True
    return delivered_any


async def _finish_job(
    repo: HandoffRepository,
    *,
    task_id: str,
    recipient: str,
    outcome: str,
    now: datetime,
) -> Transition | None:
    job = await repo.get_job(task_id, recipient)
    if job is None:
        return None
    trigger = HandoffTrigger.FAIL if outcome == "failed" else HandoffTrigger.COMPLETE
    await repo.finish_attempt(task_id, recipient, attempt=job.attempt, outcome=outcome, now=now)
    transition = apply(job, trigger, now=now, note=outcome)
    if not await repo.save_transition(transition):
        return None
    return transition


async def _resolve_destination(bot: Any, destination: ConversationCoordinate) -> Any:
    target_id = destination.thread_id or destination.channel_id
    target = bot.get_channel(target_id)
    if target is None:
        target = await bot.fetch_channel(target_id)
    if not hasattr(target, "send"):
        raise ValueError(f"handoff destination {target_id} cannot receive messages")
    return target


async def _archive_worker_thread(
    repo: HandoffRepository,
    *,
    bot: Any,
    task_id: str,
    recipient: str,
) -> None:
    thread_id = await repo.get_job_thread(task_id, recipient)
    if thread_id is None:
        return

    thread = bot.get_channel(thread_id)
    if thread is None:
        with contextlib.suppress(Exception):
            thread = await bot.fetch_channel(thread_id)
    if thread is None or not hasattr(thread, "edit"):
        return

    try:
        await thread.edit(archived=True, reason="handoff completed")
    except Exception:
        logger.warning("Could not archive handoff worker thread %s", thread_id, exc_info=True)


def _render_result_message(event: HandoffEvent) -> str:
    summary = str(event.payload.get("summary") or "").strip()
    outcome = str(event.payload.get("outcome") or "completed")
    prefix = "✅ DrewAI result" if outcome == "completed" else "⚠️ DrewAI handoff failed"
    return f"{prefix}\n\n{summary}"


def _summary(*, text: str | None, error: str | None) -> str:
    raw = error if error else text
    cleaned = " ".join((raw or "").split())
    if not cleaned:
        cleaned = "The worker finished without a text result."
    return cleaned[:RESULT_SUMMARY_CHARS]


def _new_event_id(event_id_factory: Any | None) -> str:
    if event_id_factory is not None:
        return str(event_id_factory())
    return str(uuid.uuid4())


__all__ = [
    "RESULT_SUMMARY_CHARS",
    "deliver_pending_handoff_results",
    "record_and_deliver_handoff_result",
]
