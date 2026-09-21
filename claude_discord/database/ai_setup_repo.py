"""Safe inventory snapshots for My AI Setup, one per computer.

What is stored is exactly what :mod:`claude_discord.ai_setup_inventory` can
express — fingerprints, measurements, availability evidence, prerequisites by
name, diagnostics, verification times — and nothing else, because the item
type has no field for content or a credential and every item is passed
through :func:`~claude_discord.ai_setup_redaction.ensure_safe_item` before
the first write.  Diagnostics are re-scrubbed on the way in.

One snapshot per computer.  The newest wins; an older snapshot offered later
(a delayed remote reply) is refused, so a stale copy cannot overwrite a fresh
one.  When a snapshot replaces its predecessor the repository records the
*observed* differences — an item added, removed, or whose fingerprint moved —
in ``ai_setup_changes``.  That is the only exact change history the view can
promise; file modification times remain "source modification time".

The repository owns its schema: the tables are created on :meth:`init_db`
with ``CREATE TABLE IF NOT EXISTS`` and never touch the session tables, so an
existing deployment gets them by upgrading the package.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_codec import (
    diagnostic_from_dict,
    diagnostic_to_dict,
    item_from_dict,
    item_to_dict,
)
from claude_discord.ai_setup_inventory import (
    ContentFingerprint,
    Freshness,
    InventoryDiagnostic,
    InventoryItem,
    InventorySnapshot,
    ItemIdentity,
    PrerequisiteKind,
    normalize_token,
)
from claude_discord.ai_setup_redaction import ensure_safe_item, safe_diagnostic

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_setup_snapshots (
    computer TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    freshness TEXT NOT NULL,
    source_label TEXT,
    stored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_setup_items (
    computer TEXT NOT NULL,
    identity TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    classification TEXT NOT NULL,
    fingerprint_algorithm TEXT,
    fingerprint_digest TEXT,
    last_changed_at TEXT,
    item_json TEXT NOT NULL,
    PRIMARY KEY (computer, identity)
);
CREATE INDEX IF NOT EXISTS idx_ai_setup_items_kind ON ai_setup_items(computer, kind);
CREATE TABLE IF NOT EXISTS ai_setup_diagnostics (
    computer TEXT NOT NULL,
    position INTEGER NOT NULL,
    diagnostic_json TEXT NOT NULL,
    PRIMARY KEY (computer, position)
);
CREATE TABLE IF NOT EXISTS ai_setup_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    computer TEXT NOT NULL,
    identity TEXT NOT NULL,
    kind TEXT NOT NULL,
    previous_digest TEXT,
    previous_algorithm TEXT,
    current_digest TEXT,
    current_algorithm TEXT,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_setup_changes_when ON ai_setup_changes(computer, observed_at);
CREATE TABLE IF NOT EXISTS ai_setup_exceptions (
    computer TEXT NOT NULL,
    item TEXT NOT NULL,
    reason TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (computer, item)
);
"""


@dataclass(frozen=True, slots=True)
class ObservedChange:
    """A difference the repository itself witnessed between two snapshots."""

    computer: str
    identity: ItemIdentity
    kind: str  # added | removed | changed
    observed_at: datetime
    previous: ContentFingerprint | None = None
    current: ContentFingerprint | None = None


def _fingerprint(digest: str | None, algorithm: str | None) -> ContentFingerprint | None:
    if not digest:
        return None
    return ContentFingerprint(digest=digest, algorithm=algorithm or "sha256")


class AISetupRepository:
    """SQLite persistence for inventory snapshots, observed changes and exceptions."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    # -- snapshots ----------------------------------------------------------------

    async def save_snapshot(self, snapshot: InventorySnapshot) -> bool:
        """Store a snapshot, replacing the computer's previous one if it is older.

        Returns ``False`` (and stores nothing) when a newer snapshot for the
        same computer is already held.  Raises
        :class:`~claude_discord.ai_setup_redaction.RedactionError` before any
        write when an item is unsafe.
        """
        items = tuple(ensure_safe_item(item) for item in snapshot.items)
        diagnostics = tuple(_rescrub(entry) for entry in snapshot.diagnostics)
        computer = snapshot.computer
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT collected_at FROM ai_setup_snapshots WHERE computer = ?", (computer,)
            )
            row = await cursor.fetchone()
            if row is not None and datetime.fromisoformat(row[0]) > snapshot.collected_at:
                await db.execute("ROLLBACK")
                return False
            previous = await self._fingerprints(db, computer)
            await db.execute("DELETE FROM ai_setup_items WHERE computer = ?", (computer,))
            await db.execute("DELETE FROM ai_setup_diagnostics WHERE computer = ?", (computer,))
            await db.execute(
                "INSERT INTO ai_setup_snapshots"
                " (computer, owner, collected_at, verified_at, freshness, source_label, stored_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(computer) DO UPDATE SET owner = excluded.owner,"
                " collected_at = excluded.collected_at, verified_at = excluded.verified_at,"
                " freshness = excluded.freshness, source_label = excluded.source_label,"
                " stored_at = excluded.stored_at",
                (
                    computer,
                    snapshot.owner,
                    snapshot.collected_at.isoformat(),
                    (snapshot.verified_at or snapshot.collected_at).isoformat(),
                    snapshot.freshness.value,
                    snapshot.source_label,
                    snapshot.collected_at.isoformat(),
                ),
            )
            for item in items:
                await db.execute(
                    "INSERT INTO ai_setup_items (computer, identity, kind, source_key,"
                    " classification, fingerprint_algorithm, fingerprint_digest,"
                    " last_changed_at, item_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        computer,
                        item.identity.key,
                        item.kind.value,
                        item.identity.source_key,
                        item.classification.value,
                        item.fingerprint.algorithm if item.fingerprint else None,
                        item.fingerprint.digest if item.fingerprint else None,
                        item.last_changed_at.isoformat() if item.last_changed_at else None,
                        json.dumps(item_to_dict(item), sort_keys=True),
                    ),
                )
            for position, entry in enumerate(diagnostics):
                await db.execute(
                    "INSERT INTO ai_setup_diagnostics (computer, position, diagnostic_json)"
                    " VALUES (?, ?, ?)",
                    (computer, position, json.dumps(diagnostic_to_dict(entry), sort_keys=True)),
                )
            if row is not None:
                await self._record_changes(db, computer, previous, items, snapshot.collected_at)
            await db.commit()
        return True

    async def load_snapshot(self, computer: str) -> InventorySnapshot | None:
        wanted = normalize_token(computer, kind="computer")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT owner, collected_at, verified_at, freshness, source_label"
                " FROM ai_setup_snapshots WHERE computer = ?",
                (wanted,),
            )
            header = await cursor.fetchone()
            if header is None:
                return None
            cursor = await db.execute(
                "SELECT item_json FROM ai_setup_items WHERE computer = ? ORDER BY rowid",
                (wanted,),
            )
            item_rows = await cursor.fetchall()
            cursor = await db.execute(
                "SELECT diagnostic_json FROM ai_setup_diagnostics WHERE computer = ?"
                " ORDER BY position",
                (wanted,),
            )
            diagnostic_rows = await cursor.fetchall()
        owner, collected_at, verified_at, freshness, source_label = header
        items: list[InventoryItem] = [item_from_dict(json.loads(row[0])) for row in item_rows]
        diagnostics: list[InventoryDiagnostic] = [
            diagnostic_from_dict(json.loads(row[0])) for row in diagnostic_rows
        ]
        return InventorySnapshot(
            computer=wanted,
            owner=owner,
            collected_at=datetime.fromisoformat(collected_at),
            items=tuple(items),
            diagnostics=tuple(diagnostics),
            freshness=Freshness(freshness),
            verified_at=datetime.fromisoformat(verified_at),
            source_label=source_label,
        )

    async def list_computers(self) -> tuple[str, ...]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT computer FROM ai_setup_snapshots ORDER BY computer")
            rows = await cursor.fetchall()
        return tuple(row[0] for row in rows)

    async def load_all(self) -> tuple[InventorySnapshot, ...]:
        snapshots: list[InventorySnapshot] = []
        for computer in await self.list_computers():
            snapshot = await self.load_snapshot(computer)
            if snapshot is not None:
                snapshots.append(snapshot)
        return tuple(snapshots)

    # -- observed changes ---------------------------------------------------------

    async def _fingerprints(
        self, db: aiosqlite.Connection, computer: str
    ) -> dict[str, ContentFingerprint | None]:
        cursor = await db.execute(
            "SELECT identity, fingerprint_digest, fingerprint_algorithm FROM ai_setup_items"
            " WHERE computer = ?",
            (computer,),
        )
        rows = await cursor.fetchall()
        return {row[0]: _fingerprint(row[1], row[2]) for row in rows}

    async def _record_changes(
        self,
        db: aiosqlite.Connection,
        computer: str,
        previous: dict[str, ContentFingerprint | None],
        items: Iterable[InventoryItem],
        observed_at: datetime,
    ) -> None:
        current = {item.identity.key: item.fingerprint for item in items}
        rows: list[tuple[object, ...]] = []
        for key in sorted(set(previous) | set(current)):
            before = previous.get(key)
            after = current.get(key)
            if key not in previous:
                kind = "added"
            elif key not in current:
                kind = "removed"
            elif before is not None and after is not None and not before.matches(after):
                kind = "changed"
            else:
                continue
            rows.append(
                (
                    computer,
                    key,
                    kind,
                    before.digest if before else None,
                    before.algorithm if before else None,
                    after.digest if after else None,
                    after.algorithm if after else None,
                    observed_at.isoformat(),
                )
            )
        if rows:
            await db.executemany(
                "INSERT INTO ai_setup_changes (computer, identity, kind, previous_digest,"
                " previous_algorithm, current_digest, current_algorithm, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    async def recent_changes(
        self, computer: str, *, limit: int | None = None
    ) -> tuple[ObservedChange, ...]:
        """Differences this repository witnessed, newest first."""
        wanted = normalize_token(computer, kind="computer")
        query = (
            "SELECT identity, kind, previous_digest, previous_algorithm, current_digest,"
            " current_algorithm, observed_at FROM ai_setup_changes WHERE computer = ?"
            " ORDER BY observed_at DESC, id DESC"
        )
        params: tuple[object, ...] = (wanted,)
        if limit is not None:
            query += " LIMIT ?"
            params = (wanted, limit)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(
            ObservedChange(
                computer=wanted,
                identity=ItemIdentity.from_key(row[0]),
                kind=row[1],
                observed_at=datetime.fromisoformat(row[6]),
                previous=_fingerprint(row[2], row[3]),
                current=_fingerprint(row[4], row[5]),
            )
            for row in rows
        )

    # -- declared exceptions ------------------------------------------------------

    async def save_exceptions(
        self, computer: str, exceptions: Iterable[DeliberateException]
    ) -> None:
        wanted = normalize_token(computer, kind="computer")
        declared = tuple(exceptions)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM ai_setup_exceptions WHERE computer = ?", (wanted,))
            await db.executemany(
                "INSERT INTO ai_setup_exceptions (computer, item, reason, kind)"
                " VALUES (?, ?, ?, ?)",
                [(wanted, entry.item, entry.reason, entry.kind.value) for entry in declared],
            )
            await db.commit()

    async def load_exceptions(self, computer: str) -> tuple[DeliberateException, ...]:
        wanted = normalize_token(computer, kind="computer")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT item, reason, kind FROM ai_setup_exceptions WHERE computer = ?"
                " ORDER BY rowid",
                (wanted,),
            )
            rows = await cursor.fetchall()
        return tuple(
            DeliberateException(item=row[0], reason=row[1], kind=PrerequisiteKind(row[2]))
            for row in rows
        )


def _rescrub(entry: InventoryDiagnostic) -> InventoryDiagnostic:
    return safe_diagnostic(
        entry.source_key,
        entry.severity,
        entry.message,
        computer=entry.computer,
        identity=entry.identity,
        occurred_at=entry.occurred_at,
    )


__all__ = ["SCHEMA", "AISetupRepository", "ObservedChange"]
