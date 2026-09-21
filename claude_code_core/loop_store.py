"""Remember running /gowork builds so a bot restart doesn't end them.

Everything a build needs to carry on lives on disk already — the plan's ticked
boxes and the build's own copy of the project. This file only remembers which
builds were running and where they report, so on startup the bot can pick each
one up at its next unticked task, in the same worker thread.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(
    os.environ.get(
        "CCDB_GOWORK_STATE", Path.home() / ".local" / "state" / "ccdb" / "gowork-loops.json"
    )
)


@dataclass(frozen=True)
class LoopRecord:
    repo_dir: str
    plan_path: str
    copy_path: str
    copy_plan: str
    branch: str
    worker_thread_id: int
    report_channel_id: int
    notify_user_id: int | None = None
    harness: str | None = None
    model: str | None = None
    #: Used automatically when the build's AI hits its usage limit (no question asked).
    fallback_harness: str | None = None
    fallback_model: str | None = None
    #: A plan with no Goal line starts with the goal interview (people starting a
    #: build ask for it; an API caller that already knows the goal need not).
    ask_goal: bool = False
    #: Before every step a quick AI picks which AI does it (idea 3).
    per_step_ai: bool = False
    #: Started from the build queue (idea 6): the line moves on when it waits.
    queued: bool = False
    #: "cheap", "balanced" or "careful" — where the build sits between cost and quality.
    mode: str = "balanced"


class LoopStore:
    """A small JSON file: one record per project with a build running."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH

    def all(self) -> list[LoopRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning("gowork state file unreadable; nothing to resume", exc_info=True)
            return []
        records = []
        for item in raw if isinstance(raw, list) else []:
            try:
                records.append(LoopRecord(**item))
            except TypeError:
                logger.warning("skipping malformed gowork record: %r", item)
        return records

    def _write(self, records: list[LoopRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def save(self, record: LoopRecord) -> None:
        others = [r for r in self.all() if r.repo_dir != record.repo_dir]
        self._write([*others, record])

    def remove(self, repo_dir: str) -> None:
        self._write([r for r in self.all() if r.repo_dir != repo_dir])
