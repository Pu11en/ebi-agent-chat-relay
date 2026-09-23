"""What slowed a Go Work build down, counted the same way every time (T25).

One line per event, appended to a JSONL file beside the step records: waits
for capacity or for a prerequisite, repairs, review verdicts, questions to
the person (and the same task asked about again), capacity decisions and
plan-change reworks — each with the build, task, attempt and plan version it
belongs to. Counts come only from these lines: there are no token or cost
totals here because nothing here measures them. The report is plain
sentences for the progress file; it never touches the person's planning
instructions.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

FrictionKind = Literal[
    "queue_wait",
    "dependency_wait",
    "repair",
    "review",
    "question",
    "capacity",
    "rework",
]
MAX_DETAIL_CHARS = 300


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class FrictionEvent:
    kind: FrictionKind
    build_id: str
    task_id: str
    attempt_id: str
    plan_id: str
    plan_version: int
    detail: str = ""
    at: str = field(default_factory=_now)

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "build": self.build_id,
            "task": self.task_id,
            "attempt": self.attempt_id,
            "plan": self.plan_id,
            "version": self.plan_version,
            "detail": self.detail[:MAX_DETAIL_CHARS],
            "at": self.at,
        }

    @classmethod
    def from_json(cls, value: dict) -> FrictionEvent:
        return cls(
            kind=str(value["kind"]),  # type: ignore[arg-type]
            build_id=str(value["build"]),
            task_id=str(value.get("task", "")),
            attempt_id=str(value.get("attempt", "")),
            plan_id=str(value.get("plan", "")),
            plan_version=int(value.get("version", 0)),
            detail=str(value.get("detail", "")),
            at=str(value.get("at", "")),
        )


def append_friction(path: Path, event: FrictionEvent) -> None:
    """Add one event. Never raises — bookkeeping must not break a build."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        logger.debug("gowork friction: could not append to %s", path, exc_info=True)


def read_friction(path: Path) -> list[FrictionEvent]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[FrictionEvent] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(FrictionEvent.from_json(json.loads(line)))
        except (ValueError, KeyError, TypeError):
            continue  # a half-written line: skip it, keep the rest
    return events


def friction_summary(events: list[FrictionEvent]) -> dict[str, int]:
    """Repeatable counts by kind, plus two derived ones: review_changes and question_repeat."""
    counts: Counter[str] = Counter(e.kind for e in events)
    counts["review_changes"] = sum(
        1 for e in events if e.kind == "review" and e.detail.lower().startswith("changes")
    )
    asked: Counter[tuple[str, str]] = Counter(
        (e.build_id, e.task_id) for e in events if e.kind == "question"
    )
    counts["question_repeat"] = sum(n - 1 for n in asked.values() if n > 1)
    return dict(counts)


def friction_report(events: list[FrictionEvent], *, build_id: str) -> list[str]:
    """Plain sentences about one build, for its progress file."""
    mine = [e for e in events if e.build_id == build_id]
    lines: list[str] = []
    repairs = [e for e in mine if e.kind == "repair"]
    if repairs:
        tasks = sorted({e.task_id for e in repairs})
        detail = "; ".join(f"{e.task_id}: {e.detail}" for e in repairs[:5] if e.detail)
        lines.append(
            f"{len(tasks)} task{'s' if len(tasks) != 1 else ''} needed a repair"
            + (f" ({detail})" if detail else "")
        )
    changes = [e for e in mine if e.kind == "review" and e.detail.lower().startswith("changes")]
    reviews = [e for e in mine if e.kind == "review"]
    if reviews:
        lines.append(f"the second AI sent back {len(changes)} of {len(reviews)} reviewed tasks")
    asked: Counter[str] = Counter(e.task_id for e in mine if e.kind == "question")
    repeated = {t: n for t, n in asked.items() if n > 1}
    if asked:
        total = sum(asked.values())
        lines.append(f"{total} question{'s' if total != 1 else ''} needed a person")
    for task, n in sorted(repeated.items()):
        lines.append(f"{task} was asked about {'twice' if n == 2 else f'{n} times'}")
    waits = [e for e in mine if e.kind == "queue_wait"]
    if waits:
        lines.append(f"{len(waits)} task start{'s' if len(waits) != 1 else ''} waited for capacity")
    deps = [e for e in mine if e.kind == "dependency_wait"]
    if deps:
        lines.append(f"{len(deps)} task{'s' if len(deps) != 1 else ''} waited on a prerequisite")
    capacity = [e for e in mine if e.kind == "capacity"]
    if capacity:
        lines.append(
            f"the computer's capacity limited starts {len(capacity)} time"
            f"{'s' if len(capacity) != 1 else ''} ({capacity[-1].detail})"
        )
    reworks = [e for e in mine if e.kind == "rework"]
    if reworks:
        lines.append(
            f"{len(reworks)} task{'s' if len(reworks) != 1 else ''} reworked after the plan changed"
        )
    return lines
