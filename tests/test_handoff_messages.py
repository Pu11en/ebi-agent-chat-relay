"""Tests for Discord-safe trusted handoff envelopes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from claude_code_core.handoffs import protocol as p
from claude_discord.handoff_messages import (
    HANDOFF_MARKER,
    HandoffEnvelopeError,
    format_handoff_message,
    parse_handoff_message,
)

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


def _origin() -> p.ConversationCoordinate:
    return p.ConversationCoordinate(guild_id=111, channel_id=222, thread_id=333, message_id=444)


def _task(**overrides: object) -> p.HandoffTask:
    fields: dict[str, object] = {
        "task_id": TASK_ID,
        "sender": "david",
        "recipient": "drewai",
        "origin": _origin(),
        "origin_human_id": "drew",
        "project": p.ProjectLocator(owner="drew", folder="main-projects"),
        "goal": "Search Drew's projects for the Realpage folder.",
        "authority": p.AuthorityScope(read=True),
        "expected_result": "Exact paths, relevant files, and a short summary.",
        "reply_to": _origin(),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=6),
    }
    fields.update(overrides)
    return p.HandoffTask(**fields)  # type: ignore[arg-type]


def _event(**overrides: object) -> p.HandoffEvent:
    fields: dict[str, object] = {
        "event_id": EVENT_ID,
        "kind": p.HandoffEventKind.TASK,
        "task_id": TASK_ID,
        "sender": "david",
        "recipient": "drewai",
        "sequence": 0,
        "created_at": NOW,
        "task": _task(),
    }
    fields.update(overrides)
    return p.HandoffEvent(**fields)  # type: ignore[arg-type]


def test_format_and_parse_handoff_message_round_trips_event() -> None:
    event = _event()

    text = format_handoff_message(event)

    assert text.startswith(HANDOFF_MARKER)
    assert "```json" in text
    assert parse_handoff_message(text) == event


def test_parse_ordinary_message_returns_none() -> None:
    assert parse_handoff_message("ask DrewAI to look for Realpage") is None


def test_marker_with_bad_json_is_rejected() -> None:
    with pytest.raises(HandoffEnvelopeError, match="valid JSON"):
        parse_handoff_message(f"{HANDOFF_MARKER}\n```json\nnot-json\n```")


def test_marker_without_fenced_packet_is_rejected() -> None:
    with pytest.raises(HandoffEnvelopeError, match="fenced JSON"):
        parse_handoff_message(f"{HANDOFF_MARKER}\nhello")


def test_multiple_handoff_markers_are_rejected() -> None:
    message = f"{format_handoff_message(_event())}\n\n{format_handoff_message(_event())}"

    with pytest.raises(HandoffEnvelopeError, match="exactly one"):
        parse_handoff_message(message)


def test_oversized_discord_envelope_is_rejected() -> None:
    event = _event(task=_task(goal="x" * 1400))

    with pytest.raises(HandoffEnvelopeError, match="too large"):
        format_handoff_message(event, max_chars=500)
