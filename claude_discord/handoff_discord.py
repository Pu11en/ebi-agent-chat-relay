"""How a handoff looks on Discord: one starter per task, typed events after it.

A handoff message has two readers. A person skimming ``agent-handoffs`` wants
to know who asked whom for what; the other bot wants the exact packet. Both
get the same message: a short human-readable header followed by the strict
envelope from :mod:`claude_discord.handoff_messages`. The header is trimmed
to whatever room the packet leaves under Discord's limit — the packet is
never trimmed, and a packet that would not fit is refused rather than split,
because a starter that spans two messages cannot be found again by a
reconnecting recipient.

The starter message in the channel owns the job thread: the thread is opened
*on* that message, so its id equals the starter's id and either side can find
it later without a lookup table.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from claude_code_core.handoffs.protocol import (
    HandoffEvent,
    HandoffEventKind,
    HandoffProtocolError,
    decode_event,
    encode_event,
)

from .handoff_messages import HANDOFF_MARKER, HandoffEnvelopeError
from .thread_policy import THREAD_AUTO_ARCHIVE_MINUTES

logger = logging.getLogger(__name__)

DISCORD_MESSAGE_LIMIT = 2000
SHORT_ID_CHARS = 8
MAX_HEADER_CHARS = 600
MIN_HEADER_CHARS = 32
MAX_LINE_CHARS = 300
THREAD_NAME_PREFIX = "handoff-"

_EVENT_RE = re.compile(
    rf"(?:^|\n){re.escape(HANDOFF_MARKER)}[ \t]*\n```json[ \t]*\n(?P<packet>.*?)\n```\s*$",
    re.DOTALL,
)

_STATE_ICONS = {
    "accepted": "🤝",
    "queued": "⏳",
    "running": "▶️",
    "blocked": "⛔",
    "completed": "✅",
    "failed": "❌",
}


def short_task_id(task_id: str) -> str:
    """The first eight hex characters — enough to name a thread, not to trust."""
    return task_id[:SHORT_ID_CHARS]


def job_thread_name(task_id: str) -> str:
    return f"{THREAD_NAME_PREFIX}{short_task_id(task_id)}"


def channel_in_guild(channel: Any, guild_id: int) -> bool:
    """True when ``channel`` (or thread) demonstrably belongs to ``guild_id``.

    A ``reply_to`` coordinate is peer-supplied. ``bot.get_channel`` resolves an
    id across every guild the bot is in, so the id alone would let a peer name
    any channel this bot can see as the place its result lands. The guild on
    the resolved object is what Discord says; a DM or an unknown object has no
    guild and is refused.
    """
    resolved = getattr(getattr(channel, "guild", None), "id", None)
    return isinstance(resolved, int) and resolved == guild_id


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"


def describe_authority(event: HandoffEvent) -> str:
    task = event.task
    if task is None:
        return ""
    scope = task.authority
    if scope.is_read_only:
        return "read-only"
    parts = ["read"]
    if scope.edit:
        parts.append(
            "edit " + (", ".join(scope.edit_paths) if scope.edit_paths else "within the project")
        )
    parts.extend(cap.value for cap in sorted(scope.capabilities, key=lambda c: c.value))
    return " · ".join(parts)


def render_human_line(event: HandoffEvent) -> str:
    """One bounded line a person can read without opening the packet."""
    sid = short_task_id(event.task_id)
    kind = event.kind
    payload = event.payload
    if kind is HandoffEventKind.TASK and event.task is not None:
        task = event.task
        lines = [
            f"🤝 Handoff `{sid}` · {task.sender} → {task.recipient}",
            f"📁 {task.project.owner}/{task.project.folder} · 🔐 {describe_authority(event)}",
            f"🎯 {_clip(task.goal, MAX_LINE_CHARS)}",
        ]
        return "\n".join(lines)
    if kind is HandoffEventKind.ACK:
        return f"🤝 {event.sender} accepted handoff `{sid}`"
    if kind is HandoffEventKind.STATE:
        state = str(payload.get("state", ""))
        icon = _STATE_ICONS.get(state, "ℹ️")
        note = payload.get("note")
        tail = f" · {_clip(str(note), MAX_LINE_CHARS)}" if note else ""
        return f"{icon} {event.sender} · `{sid}` · {state}{tail}"
    if kind is HandoffEventKind.QUESTION:
        question = _clip(str(payload.get("question", "")), MAX_LINE_CHARS)
        return f"❓ {event.sender} asks on `{sid}`: {question}"
    if kind is HandoffEventKind.ANSWER:
        answer = _clip(str(payload.get("answer", "")), MAX_LINE_CHARS)
        return f"💬 {event.sender} answers on `{sid}`: {answer}"
    outcome = str(payload.get("outcome", ""))
    icon = "✅" if outcome == "completed" else "❌"
    summary = _clip(str(payload.get("summary", "")), MAX_LINE_CHARS)
    return f"{icon} {event.sender} · `{sid}` · {outcome} — {summary}"


def _compose(header: str, event: HandoffEvent) -> str:
    packet = encode_event(event)
    envelope = f"{HANDOFF_MARKER}\n```json\n{packet}\n```"
    room = DISCORD_MESSAGE_LIMIT - len(envelope) - 1
    if room < MIN_HEADER_CHARS:
        raise HandoffEnvelopeError(
            f"handoff packet leaves no room for its header: {len(envelope)} characters "
            f"of the {DISCORD_MESSAGE_LIMIT} Discord allows"
        )
    text = header if len(header) <= room else header[: max(0, room - 1)] + "…"
    return f"{text}\n{envelope}"


def render_task_starter(event: HandoffEvent) -> str:
    """The channel message that opens a job: human header plus the packet."""
    if event.kind is not HandoffEventKind.TASK or event.task is None:
        raise HandoffEnvelopeError("only a task event can start a job thread")
    return _compose(render_human_line(event)[:MAX_HEADER_CHARS], event)


def render_event_message(event: HandoffEvent) -> str:
    """A thread message for any typed event, bounded to one Discord message."""
    return _compose(render_human_line(event)[:MAX_HEADER_CHARS], event)


def parse_event_message(text: str) -> HandoffEvent | None:
    """Parse a starter or event message; ``None`` for ordinary chatter.

    A human header before the marker is allowed; two markers or a broken
    fence are refused, exactly as the strict envelope refuses them.
    """
    if not text or HANDOFF_MARKER not in text:
        return None
    if text.count(HANDOFF_MARKER) != 1:
        raise HandoffEnvelopeError("handoff message must contain exactly one marker")
    match = _EVENT_RE.search(text)
    if match is None:
        raise HandoffEnvelopeError("handoff message must contain one fenced JSON packet")
    try:
        return decode_event(match.group("packet").strip())
    except HandoffProtocolError as exc:
        raise HandoffEnvelopeError(f"handoff packet is invalid: {exc}") from exc


# -- the job thread ----------------------------------------------------------


async def ensure_job_thread(
    starter: Any,
    task_id: str,
    *,
    fetch_channel: Callable[[int], Awaitable[Any]] | None = None,
) -> Any:
    """Open the job thread on ``starter``, or return the one already there.

    Either bot may open it: the name is a pure function of the task id, and
    when the other side wins the race Discord refuses a second thread on the
    same message, in which case the existing one is fetched by the starter's
    id. An archived thread is unarchived so the next post is visible.
    """
    thread = getattr(starter, "thread", None)
    if thread is None:
        try:
            thread = await starter.create_thread(
                name=job_thread_name(task_id),
                auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as exc:
            if fetch_channel is None:
                raise
            logger.debug("job thread for %s already exists (%s); fetching", task_id, exc)
            thread = await fetch_channel(int(starter.id))
    if getattr(thread, "archived", False) and hasattr(thread, "edit"):
        with contextlib.suppress(Exception):
            await thread.edit(archived=False)
    return thread


async def post_task_starter(channel: Any, event: HandoffEvent) -> tuple[Any, Any]:
    """Post one starter for ``event`` and open its job thread. Returns both."""
    starter = await channel.send(render_task_starter(event))
    thread = await ensure_job_thread(starter, event.task_id)
    return starter, thread


__all__ = [
    "DISCORD_MESSAGE_LIMIT",
    "THREAD_NAME_PREFIX",
    "channel_in_guild",
    "describe_authority",
    "ensure_job_thread",
    "job_thread_name",
    "parse_event_message",
    "post_task_starter",
    "render_event_message",
    "render_human_line",
    "render_task_starter",
    "short_task_id",
]
