"""Discord text envelopes for trusted cross-computer handoff events.

The trusted handoff protocol already owns validation and JSON serialization.
This module only gives that packet a strict Discord message shape so ordinary
bot chatter stays ignored and structured handoffs can be recognized safely.
"""

from __future__ import annotations

import re

from claude_code_core.handoffs.protocol import (
    HandoffEvent,
    HandoffProtocolError,
    decode_event,
    encode_event,
)

HANDOFF_MARKER = "CCDB_HANDOFF_V1"
DEFAULT_MAX_DISCORD_MESSAGE_CHARS = 1900

_ENVELOPE_RE = re.compile(
    rf"^\s*{re.escape(HANDOFF_MARKER)}\s*\n```json\s*\n(?P<packet>.*?)\n```\s*$",
    re.DOTALL,
)


class HandoffEnvelopeError(ValueError):
    """A Discord message claims to be a handoff but is not a valid envelope."""


def format_handoff_message(
    event: HandoffEvent,
    *,
    max_chars: int = DEFAULT_MAX_DISCORD_MESSAGE_CHARS,
) -> str:
    """Return a strict Discord message carrying ``event``.

    ``max_chars`` defaults below Discord's normal 2,000-character message cap so
    callers can add small prefixes later without accidentally fragmenting the
    packet.
    """
    packet = encode_event(event)
    message = f"{HANDOFF_MARKER}\n```json\n{packet}\n```"
    if len(message) > max_chars:
        raise HandoffEnvelopeError(
            f"handoff Discord envelope is too large: {len(message)} characters "
            f"(limit {max_chars})"
        )
    return message


def parse_handoff_message(text: str) -> HandoffEvent | None:
    """Parse a Discord handoff envelope, or return ``None`` for ordinary text."""
    if HANDOFF_MARKER not in text:
        return None
    if text.count(HANDOFF_MARKER) != 1:
        raise HandoffEnvelopeError("handoff message must contain exactly one marker")

    match = _ENVELOPE_RE.match(text)
    if match is None:
        raise HandoffEnvelopeError("handoff message must contain one fenced JSON packet")

    packet = match.group("packet").strip()
    try:
        return decode_event(packet)
    except HandoffProtocolError as exc:
        message = str(exc)
        if "valid JSON" not in message:
            message = f"handoff packet is invalid: {message}"
        raise HandoffEnvelopeError(message) from exc


__all__ = [
    "DEFAULT_MAX_DISCORD_MESSAGE_CHARS",
    "HANDOFF_MARKER",
    "HandoffEnvelopeError",
    "format_handoff_message",
    "parse_handoff_message",
]
