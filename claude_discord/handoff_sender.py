"""Build and post Discord handoff messages for parsed natural-language requests."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from claude_code_core.handoffs import protocol as p

from .handoff_messages import format_handoff_message
from .handoff_triggers import DrewAILookupTrigger

DEFAULT_HANDOFF_TTL = timedelta(hours=6)
DEFAULT_PROJECT_LOOKUP_EXPECTED_RESULT = "Exact paths, relevant files, and a short summary."


class _DiscordDestination(Protocol):
    def send(self, content: str) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class SentHandoff:
    """A handoff event plus the Discord message created for it."""

    event: p.HandoffEvent
    sent_message: Any


def build_project_lookup_handoff_event(
    trigger: DrewAILookupTrigger,
    *,
    origin_message: Any,
    sender_agent_id: str = "david",
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_HANDOFF_TTL,
    id_factory: Callable[[], tuple[str, str]] | None = None,
) -> p.HandoffEvent:
    """Build the trusted task event for a DrewAI read-only project lookup."""
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    task_id, event_id = _new_ids(id_factory)
    origin = _coordinate_for_message(origin_message)
    task = p.HandoffTask(
        task_id=task_id,
        sender=sender_agent_id,
        recipient=trigger.agent_id,
        origin=origin,
        origin_human_id=str(origin_message.author.id),
        project=p.ProjectLocator(owner="drew", folder="main-projects"),
        goal=f"Search Drew's main projects for: {trigger.query}",
        authority=p.AuthorityScope(read=True),
        expected_result=DEFAULT_PROJECT_LOOKUP_EXPECTED_RESULT,
        reply_to=origin,
        created_at=created_at,
        expires_at=created_at + ttl,
    )
    return p.HandoffEvent(
        event_id=event_id,
        kind=p.HandoffEventKind.TASK,
        task_id=task_id,
        sender=sender_agent_id,
        recipient=trigger.agent_id,
        sequence=0,
        created_at=created_at,
        task=task,
    )


async def send_project_lookup_handoff(
    trigger: DrewAILookupTrigger,
    *,
    origin_message: Any,
    destination: _DiscordDestination,
    sender_agent_id: str = "david",
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_HANDOFF_TTL,
    id_factory: Callable[[], tuple[str, str]] | None = None,
) -> SentHandoff:
    """Post a parsed DrewAI lookup trigger to a Discord handoff destination."""
    event = build_project_lookup_handoff_event(
        trigger,
        origin_message=origin_message,
        sender_agent_id=sender_agent_id,
        now=now,
        ttl=ttl,
        id_factory=id_factory,
    )
    sent_message = await destination.send(format_handoff_message(event))
    return SentHandoff(event=event, sent_message=sent_message)


def _coordinate_for_message(message: Any) -> p.ConversationCoordinate:
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("handoff origin message must have a guild")

    channel = message.channel
    thread_id = None
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is not None:
        thread_id = int(channel.id)
        channel_id = int(parent_id)
    else:
        channel_id = int(channel.id)

    return p.ConversationCoordinate(
        guild_id=int(guild.id),
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=int(message.id),
    )


def _new_ids(id_factory: Callable[[], tuple[str, str]] | None) -> tuple[str, str]:
    if id_factory is not None:
        return id_factory()
    return str(uuid.uuid4()), str(uuid.uuid4())


__all__ = [
    "DEFAULT_HANDOFF_TTL",
    "DEFAULT_PROJECT_LOOKUP_EXPECTED_RESULT",
    "SentHandoff",
    "build_project_lookup_handoff_event",
    "send_project_lookup_handoff",
]
