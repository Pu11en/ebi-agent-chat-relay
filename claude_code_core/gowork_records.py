"""The per-step records gowork learns from (roadmap idea 7).

Every step of every build appends one fixed-shape line: which AI did it, how
long it took and how it ended. Two things read them back: the step-AI picker
(a short track record per AI) and the end of a build (a few "next time"
bullets from that build's own records). A JSONL file, because it only ever
grows by appending and a half-written line must not lose the rest.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The track record looks at this many recent steps.
TRACK_WINDOW = 300


def append_record(path: Path, record: dict[str, Any]) -> None:
    """Add one step's record. Never raises — learning must not break a build."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("gowork: couldn't save a step record", exc_info=True)


def read_records(path: Path, limit: int = TRACK_WINDOW) -> list[dict[str, Any]]:
    """The last *limit* records; unreadable lines are skipped."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def track_record(records: list[dict[str, Any]]) -> list[str]:
    """One plain line per AI: steps, how they ended, typical time."""
    by_ai: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_ai[str(r.get("ai") or "unknown")].append(r)
    lines: list[str] = []
    for ai, rows in sorted(by_ai.items(), key=lambda kv: -len(kv[1])):
        results = Counter(str(r.get("result")) for r in rows)
        seconds = sorted(float(r.get("seconds") or 0) for r in rows)
        typical = seconds[len(seconds) // 2] / 60 if seconds else 0
        parts = [f"{count} {result}" for result, count in results.most_common()]
        lines.append(f"{ai}: {len(rows)} steps ({', '.join(parts)}), about {typical:.0f} min each")
    return lines


def lessons_prompt(records: list[dict[str, Any]], recaps: list[str]) -> str:
    """Ask a quick AI for "next time" bullets from one build's records."""
    rows = [
        f"- {r.get('step')}: {r.get('ai')}, {r.get('result')}, "
        f"{float(r.get('seconds') or 0) / 60:.0f} min"
        + (f" ({r['detail']})" if r.get("detail") else "")
        for r in records
    ]
    return "\n".join(
        [
            "A software build just finished. Here is how each step went (step: AI, how it "
            "ended, minutes):",
            *rows,
            "",
            "What each step did:",
            *[f"- {r}" for r in recaps],
            "",
            "Write 2 or 3 short bullets, in plain words for a non-technical person, on what "
            "to do differently next time: steps that should have been split, an AI that "
            "struggled or did well at a kind of step, things that slowed it down. Start each "
            "line with '- '. If it all went smoothly, write one bullet saying so. Nothing else.",
        ]
    )
