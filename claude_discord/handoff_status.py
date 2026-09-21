"""Read-only status view for cross-agent handoffs.

The handoff ledger is intentionally durable and job-shaped. This module turns
that ledger into a compact operator view: how many jobs are accepted, running,
completed, or failed, and what the most recent jobs were about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from .database.handoff_repo import HandoffRepository

DEFAULT_HANDOFF_STATUS_LIMIT = 20
MAX_HANDOFF_STATUS_LIMIT = 100


@dataclass(frozen=True)
class HandoffStatusItem:
    """One row in the human/operator handoff status view."""

    task_id: str
    sender: str
    recipient: str
    state: str
    goal: str
    job_thread_id: int | None
    updated_at: datetime
    result_outcome: str | None = None
    result_summary: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the item for the local REST control plane."""
        return {
            "task_id": self.task_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "state": self.state,
            "goal": self.goal,
            "job_thread_id": self.job_thread_id,
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
            "result_outcome": self.result_outcome,
            "result_summary": self.result_summary,
        }


@dataclass(frozen=True)
class HandoffStatusSummary:
    """Counts plus a bounded recent list."""

    counts: dict[str, int]
    items: tuple[HandoffStatusItem, ...]


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("stored handoff timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _clean_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_HANDOFF_STATUS_LIMIT
    return max(1, min(MAX_HANDOFF_STATUS_LIMIT, int(limit)))


def _task_goal(packet_json: object) -> str:
    if not isinstance(packet_json, str):
        return "(no goal stored)"
    try:
        packet = json.loads(packet_json)
    except json.JSONDecodeError:
        return "(unreadable goal)"
    goal = packet.get("goal") if isinstance(packet, dict) else None
    return str(goal).strip() if goal else "(no goal stored)"


async def load_handoff_status(
    repo: HandoffRepository,
    *,
    recipient: str | None = None,
    limit: int | None = DEFAULT_HANDOFF_STATUS_LIMIT,
) -> HandoffStatusSummary:
    """Load a bounded status snapshot from the handoff ledger."""
    bounded_limit = _clean_limit(limit)
    count_where = "WHERE recipient_agent_id = ?" if recipient else ""
    item_where = "WHERE t.recipient_agent_id = ?" if recipient else ""
    params: tuple[object, ...] = (recipient,) if recipient else ()

    async with aiosqlite.connect(repo._db_path) as db:  # noqa: SLF001 - read-only status view
        db.row_factory = aiosqlite.Row
        count_cursor = await db.execute(
            f"""
            SELECT state, COUNT(*) AS n
              FROM handoff_tasks
              {count_where}
             GROUP BY state
             ORDER BY state
            """,
            params,
        )
        count_rows = await count_cursor.fetchall()
        counts = {str(row["state"]): int(row["n"]) for row in count_rows}

        item_cursor = await db.execute(
            f"""
            SELECT
                t.task_id,
                t.recipient_agent_id,
                t.sender_agent_id,
                t.packet_json,
                t.state,
                t.job_thread_id,
                t.updated_at,
                r.outcome AS result_outcome,
                r.summary AS result_summary
              FROM handoff_tasks t
              LEFT JOIN handoff_results r
                ON r.task_id = t.task_id
               AND r.recipient_agent_id = t.recipient_agent_id
              {item_where}
             ORDER BY t.updated_at DESC, t.id DESC
             LIMIT ?
            """,
            (*params, bounded_limit),
        )
        item_rows = await item_cursor.fetchall()

    items = tuple(
        HandoffStatusItem(
            task_id=str(row["task_id"]),
            sender=str(row["sender_agent_id"]),
            recipient=str(row["recipient_agent_id"]),
            state=str(row["state"]),
            goal=_task_goal(row["packet_json"]),
            job_thread_id=(
                int(row["job_thread_id"]) if row["job_thread_id"] is not None else None
            ),
            updated_at=_parse_datetime(row["updated_at"]),
            result_outcome=(
                str(row["result_outcome"]) if row["result_outcome"] is not None else None
            ),
            result_summary=(
                str(row["result_summary"]) if row["result_summary"] is not None else None
            ),
        )
        for row in item_rows
    )
    return HandoffStatusSummary(counts=counts, items=items)


def render_handoff_status(summary: HandoffStatusSummary) -> str:
    """Render the snapshot as short plain text for humans and agents."""
    if not summary.items:
        return "No handoffs found."

    count_text = " · ".join(f"{state}: {count}" for state, count in sorted(summary.counts.items()))
    lines = [f"Handoffs — {count_text}"]
    for item in summary.items:
        thread = f" · thread {item.job_thread_id}" if item.job_thread_id is not None else ""
        result = f" · {item.result_summary}" if item.result_summary else ""
        lines.append(
            f"- {item.state} · {item.sender} → {item.recipient} · {item.goal}{thread}{result}"
        )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_HANDOFF_STATUS_LIMIT",
    "MAX_HANDOFF_STATUS_LIMIT",
    "HandoffStatusItem",
    "HandoffStatusSummary",
    "load_handoff_status",
    "render_handoff_status",
]
