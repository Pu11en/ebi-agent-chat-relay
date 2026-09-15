"""The gowork build queue (roadmap idea 6): builds wait in line and run back to back.

"Queue it" adds a plan; the bot starts queued builds one after another and moves
on as soon as the current one finishes or stops to wait for the person, so one
stuck build never holds up the rest. What happened to each is kept for the
morning summary. Stored as one JSON file so a restart keeps the line.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    plan_path: str
    report_id: int
    notify_user_id: int | None = None
    harness: str | None = None
    model: str | None = None
    mode: str | None = None
    fallback_harness: str | None = None
    fallback_model: str | None = None
    queued_at: str = field(
        default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds")
    )


@dataclass
class QueueState:
    waiting: list[QueueItem] = field(default_factory=list)
    #: One entry per queued build started: plan, repo, thread, report channel, state.
    history: list[dict[str, Any]] = field(default_factory=list)
    #: The day (YYYY-MM-DD) the last morning summary was posted.
    last_summary: str = ""


class BuildQueue:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = self._load()

    def _load(self) -> QueueState:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return QueueState(
                waiting=[QueueItem(**item) for item in data.get("waiting", [])],
                history=list(data.get("history", [])),
                last_summary=str(data.get("last_summary", "")),
            )
        except (OSError, ValueError, TypeError):
            return QueueState()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "waiting": [asdict(i) for i in self.state.waiting],
                        "history": self.state.history[-100:],
                        "last_summary": self.state.last_summary,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("gowork: couldn't save the build queue", exc_info=True)

    def add(self, item: QueueItem) -> int:
        """Put *item* at the end of the line. Returns its place (1 = next)."""
        self.state.waiting.append(item)
        self.save()
        return len(self.state.waiting)

    def take(self, item: QueueItem) -> None:
        self.state.waiting.remove(item)
        self.save()

    def started(self, item: QueueItem, repo: str, thread_id: int) -> None:
        self.state.history.append(
            {
                "plan": Path(item.plan_path).name,
                "repo": repo,
                "thread_id": thread_id,
                "report_id": item.report_id,
                "started": datetime.datetime.now().isoformat(timespec="seconds"),
                "state": "running",
            }
        )
        self.save()

    def note(self, thread_id: int, state: str) -> None:
        """Update what happened to the queued build working in *thread_id*."""
        for entry in reversed(self.state.history):
            if entry.get("thread_id") == thread_id:
                entry["state"] = state
                self.save()
                return

    def unreported(self) -> list[dict[str, Any]]:
        """Queued builds not yet in a morning summary."""
        return [e for e in self.state.history if not e.get("reported")]

    def mark_reported(self, day: str, entries: list[dict[str, Any]] | None = None) -> None:
        """Mark *entries* (default: all) as summarised, so they aren't listed again."""
        for entry in entries if entries is not None else self.state.history:
            entry["reported"] = True
        self.state.last_summary = day
        self.save()


def morning_summary(entries: list[dict[str, Any]], waiting: int) -> str:
    """One plain message: what each queued build did overnight, and what's still in line."""
    if not entries and not waiting:
        return ""
    lines = ["☀️ **Good morning. Here's what the build queue did:**"]
    for e in entries:
        lines.append(
            f"• `{e.get('plan')}` ({e.get('repo')}): {e.get('state')} · <#{e.get('thread_id')}>"
        )
    if waiting:
        lines.append(f"-# {waiting} more still waiting in line.")
    return "\n".join(lines)
