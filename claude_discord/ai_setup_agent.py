"""The Setup Agent packet: safe item facts plus the user's question, nothing else.

**Ask Setup Agent** opens a normal session through the existing session
creation contract; this module only decides what the first prompt says.  The
packet points the agent at the source (its locator) and gives it the safe
facts the inventory already holds — identity, kind, scope, availability,
prerequisites by name, measurements, last change, how other computers
compare — and the user's question.  It never carries file content or a
credential: the item type has no field for either, and the question is run
through the redactor in case the user pasted one.

The packet also states the boundary out loud: reading is fine, changing
anything waits for the user's request in that session and then follows the
project's normal rules, verification and approval.
"""

from __future__ import annotations

from collections.abc import Iterable

from claude_discord.ai_setup_inventory import InventoryItem
from claude_discord.ai_setup_queries import detail_facts
from claude_discord.ai_setup_redaction import RedactionError, safe_message
from claude_discord.ai_setup_remote import ComputerComparison

#: A bound on the whole first prompt; the question is clipped before this bites.
MAX_PACKET_CHARS = 4000
MAX_QUESTION_CHARS = 1500

_RULES = (
    "Rules for this session: this packet carries safe metadata only — no file content and "
    "no credential values. Read the source at the locator yourself when you need its "
    "content. Do not edit, move, disable, install or remove any configuration merely "
    "because this session was opened; make a change only when the user asks for it here, "
    "and then follow the project's normal rules, verification and approval before changing "
    "anything."
)


def _safe_question(question: str, *, limit: int) -> str:
    text = " ".join(question.split())
    if not text:
        return "(no question given — describe this item and where it is loaded)"
    try:
        return safe_message(text, kind="question", limit=max(40, limit))
    except (RedactionError, ValueError):
        return "(the question was withheld because it contained only secret-looking text)"


def build_setup_agent_packet(
    item: InventoryItem,
    *,
    question: str,
    computer: str,
    comparisons: Iterable[ComputerComparison] = (),
) -> str:
    """The first prompt of a Setup Agent session about ``item``."""
    lines = [
        f"You are the Setup Agent for {computer}. The user selected one item in My AI Setup, "
        "the read-only inventory of their custom AI configuration, and has a question about it.",
        "",
        f"Item: {item.display_name} ({item.kind.label})",
        f"Identity: {item.identity.key}",
    ]
    lines.extend(f"{label}: {value}" for label, value in detail_facts(item))
    other: list[str] = []
    for comparison in comparisons:
        match = next((e for e in comparison.results if e.identity == item.identity), None)
        if match is not None:
            detail = f" — {match.detail}" if match.detail else ""
            other.append(f"- {comparison.computer}: {match.label}{detail}")
    if other:
        lines.append("Other computers:")
        lines.extend(other)
    lines.extend(["", "User's question:", "", "", _RULES])
    fixed = "\n".join(lines)
    budget = min(MAX_QUESTION_CHARS, MAX_PACKET_CHARS - len(fixed))
    lines[-3] = _safe_question(question, limit=budget)
    packet = "\n".join(lines)
    if len(packet) > MAX_PACKET_CHARS:  # only when the facts alone are enormous
        packet = packet[: MAX_PACKET_CHARS - 1] + "…"
    return packet


__all__ = ["MAX_PACKET_CHARS", "MAX_QUESTION_CHARS", "build_setup_agent_packet"]
