"""Bounded, read-only metadata from an indexed Codex native session.

Exec stdout exposes cumulative usage, not the last model request's context.
The native store is an optional, version-dependent source: failures mean unknown,
never a failed agent turn. No conversation text leaves this adapter.
Provider failure messages may be recovered when exec stdout omits the reason.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

_HEAD_BYTES = 256 * 1024
_TAIL_BYTES = 1024 * 1024
_SESSION_ID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")


@dataclass(frozen=True)
class CodexContext:
    used: int
    window: int
    observed_at: float


def _integer(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _indexed_rollout(home: Path, session_id: str) -> Path | None:
    # A handful of native schema versions may coexist after a CLI upgrade.
    # Never crawl all session transcripts to find one ID.
    versions = sorted(
        (p for p in home.glob("state_*.sqlite") if p.stem[6:].isdigit()),
        key=lambda p: int(p.stem[6:]),
        reverse=True,
    )[:4]
    for index in versions:
        if not index.resolve().is_relative_to(home):
            continue
        try:
            with sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=0.1) as db:
                steps = 0

                def limit_query() -> int:
                    nonlocal steps
                    steps += 1
                    return int(steps > 100)

                db.set_progress_handler(limit_query, 1000)
                row = db.execute(
                    "SELECT rollout_path FROM threads WHERE id = ?", (session_id,)
                ).fetchone()
        except sqlite3.Error:
            continue
        if row and isinstance(row[0], str):
            path = Path(row[0]).resolve()
            if path.is_relative_to(home / "sessions"):
                return path
    return None


def _rollout_tail(home: Path, session_id: str, started_at: float) -> list[bytes] | None:
    """Read a bounded, complete tail only after verifying identity and freshness."""
    if not _SESSION_ID.fullmatch(session_id):
        return None
    try:
        path = _indexed_rollout(home.expanduser().resolve(), session_id)
        if path is None or path.stat().st_mtime < started_at:
            return None
        with path.open("rb") as stream:
            first = json.loads(stream.readline(_HEAD_BYTES))
            if (
                not isinstance(first, dict)
                or first.get("type") != "session_meta"
                or not isinstance(first.get("payload"), dict)
                or first["payload"].get("id") != session_id
            ):
                return None
            size = stream.seek(0, 2)
            offset = max(0, size - _TAIL_BYTES)
            stream.seek(offset)
            tail = stream.read(_TAIL_BYTES)
        if offset:
            tail = tail.partition(b"\n")[2]
        if not tail.endswith(b"\n"):
            # A writer is still appending; do not silently reuse a prior turn.
            return None
        return tail.splitlines()
    except (OSError, ValueError, RuntimeError):
        return None


def _current_completion(payload: dict, started_at: float) -> bool:
    start = payload.get("started_at")
    return (
        isinstance(start, (int, float))
        and not isinstance(start, bool)
        and started_at - 1 <= start <= time.time() + 5
    )


def read_codex_failure(home: Path, session_id: str, started_at: float) -> str | None:
    """Recover only the latest current-run provider failure, never agent/tool text."""
    tail = _rollout_tail(home, session_id, started_at)
    if tail is None:
        return None
    for line in reversed(tail):
        if len(line) > _HEAD_BYTES:
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") == "compacted":
            return None
        if record.get("type") != "event_msg":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind in {"task_started", "context_compacted", "turn_aborted", "token_count"}:
            return None
        if kind == "task_complete":
            if not _current_completion(payload, started_at):
                return None
            error = payload.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            return message[:2000] if isinstance(message, str) and message.strip() else None
    return None


def read_codex_context(
    home: Path, session_id: str, started_at: float, completed_usage: dict
) -> CodexContext | None:
    """Match a completed run to its own last input count, including cached input.

    Validate native identity, current-run completion, and cumulative stdout usage.
    Looking backward must reach a token count before another turn or compaction
    boundary. A bounded tail can therefore yield unknown, but never old context.
    """
    if not isinstance(completed_usage, dict):
        return None
    tail = _rollout_tail(home, session_id, started_at)
    if tail is None:
        return None
    try:
        complete = False
        for line in reversed(tail):
            if len(line) > _HEAD_BYTES:
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "compacted":
                return None
            if record.get("type") != "event_msg":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if kind in {"task_started", "context_compacted", "turn_aborted"}:
                return None
            if kind == "task_complete":
                if complete or not _current_completion(payload, started_at):
                    return None
                complete = True
            elif kind == "token_count":
                if not complete:
                    return None
                info = payload.get("info")
                if not isinstance(info, dict):
                    return None
                last, total = info.get("last_token_usage"), info.get("total_token_usage")
                if not isinstance(last, dict) or not isinstance(total, dict):
                    return None
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    if not _integer(total.get(key)) or total[key] != completed_usage.get(key):
                        return None
                used, window = last.get("input_tokens"), info.get("model_context_window")
                if not _integer(used) or not _integer(window) or window == 0:
                    return None
                return CodexContext(used=used, window=window, observed_at=time.time())
    except (OSError, ValueError, RuntimeError):
        # Missing/unreadable state, malformed first record, or a symlink loop.
        return None
    return None
