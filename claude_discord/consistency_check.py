"""A read-only consistency check over the session store and spoken tags.

The live API is not a safe place to diagnose from: listing sessions there may
allocate tags, and it returns a limited page. This reads the database directly
through a read-only SQLite connection, enumerates every session row, and never
starts a process, calls a model or writes a byte. Whatever it cannot read is
reported as unavailable rather than passed.

Legitimate states are not failures: internal workers are untagged on purpose,
and when every word in the pool is held an untagged user session is expected.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .voice_labels import SPOKEN_LABELS, VOICE_LABEL_PREFIX, thread_id_from_key

__all__ = ["ConsistencyReport", "inspect_database", "read_only_uri"]

_ADDRESSABLE_PREFIX = "voice_addressable:"


@dataclass
class ConsistencyReport:
    available: bool
    coverage: str
    closed_holding_tags: list[int] = field(default_factory=list)
    duplicate_tags: dict[str, list[int]] = field(default_factory=dict)
    untagged_user_sessions: list[int] = field(default_factory=list)
    untagged_workers: list[int] = field(default_factory=list)
    workers_holding_tags: list[int] = field(default_factory=list)
    pending_closes: list[int] = field(default_factory=list)
    pending_archives: list[int] = field(default_factory=list)
    free_tags: int = 0

    @property
    def problems(self) -> list[str]:
        """Inconsistencies, in a stable order. Informational counts are not problems."""
        found: list[str] = []
        if not self.available:
            return [f"session store unavailable: {self.coverage}"]
        if self.closed_holding_tags:
            found.append(f"closed sessions holding tags: {self.closed_holding_tags}")
        if self.duplicate_tags:
            found.append(f"tags shared by several open threads: {self.duplicate_tags}")
        if self.workers_holding_tags:
            found.append(f"internal workers holding tags: {self.workers_holding_tags}")
        if self.untagged_user_sessions and self.free_tags:
            found.append(
                "open user sessions untagged while tags are free: "
                f"{self.untagged_user_sessions} ({self.free_tags} free)"
            )
        return found


def read_only_uri(db: Path) -> str:
    """A SQLite URI that reads ``db`` without creating or changing any file.

    A read-only connection to a WAL database still creates -wal/-shm files when
    none exist. With no live WAL, open it immutable (no side files, no locks).
    With a live WAL, share it read-only: that touches only SQLite's reader index.
    """
    live_wal = db.with_name(db.name + "-wal").exists()
    return f"{db.resolve().as_uri()}?{'mode=ro' if live_wal else 'immutable=1'}"


def inspect_database(path: str | Path) -> ConsistencyReport:
    """Read ``path`` without changing it and report what disagrees."""
    db = Path(path)
    if not db.is_file():
        return ConsistencyReport(available=False, coverage=f"unavailable: no database at {db}")
    try:
        with closing(sqlite3.connect(read_only_uri(db), uri=True)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            # Databases from before the archive outbox have no pending-archive state.
            archive = "archive_pending" if "archive_pending" in columns else "0"
            rows = conn.execute(
                f"SELECT thread_id, lifecycle_state, {archive} FROM sessions"  # noqa: S608
            ).fetchall()
            settings = conn.execute(
                "SELECT key, value FROM settings WHERE key LIKE ? OR key LIKE ?",
                (f"{VOICE_LABEL_PREFIX}%", f"{_ADDRESSABLE_PREFIX}%"),
            ).fetchall()
    except sqlite3.Error as exc:
        return ConsistencyReport(available=False, coverage=f"unavailable: {exc}")
    report = _report(rows, settings)
    if archive == "0":
        report.coverage += " (archive state not recorded by this schema)"
    return report


def _report(rows: list[tuple[int, str, int]], settings: list[tuple[str, str]]) -> ConsistencyReport:
    labels: dict[int, str] = {}
    workers: set[int] = set()
    for key, value in settings:
        thread_id = thread_id_from_key(key)
        if thread_id is not None:
            labels[thread_id] = value
        elif key.startswith(_ADDRESSABLE_PREFIX) and value == "false":
            suffix = key.removeprefix(_ADDRESSABLE_PREFIX)
            if suffix.isdigit():
                workers.add(int(suffix))

    report = ConsistencyReport(available=True, coverage=f"all {len(rows)} session rows")
    holders: dict[str, list[int]] = {}
    for thread_id, state, archive_pending in sorted(rows):
        label = labels.get(thread_id)
        if state == "closed":
            if label:
                report.closed_holding_tags.append(thread_id)
            if archive_pending:
                report.pending_archives.append(thread_id)
            continue
        if state == "closing":
            report.pending_closes.append(thread_id)
        if thread_id in workers:
            (report.workers_holding_tags if label else report.untagged_workers).append(thread_id)
        elif label:
            holders.setdefault(label, []).append(thread_id)
        else:
            report.untagged_user_sessions.append(thread_id)
    report.duplicate_tags = {word: ids for word, ids in holders.items() if len(ids) > 1}
    report.free_tags = len(set(SPOKEN_LABELS) - set(labels.values()))
    return report
