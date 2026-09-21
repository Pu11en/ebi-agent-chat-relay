"""Personal Favorite / Hide / recency metadata for shared-catalog projects.

The catalog itself is filesystem truth (see ``project_catalog.py``); this
repository only stores how one user wants to *see* it.  Three rules:

* Rows are keyed by :attr:`ProjectIdentity.key`, never by path, so a moved
  root or a different drive letter does not orphan anyone's favorites.
* Nothing here creates or deletes a project.  Metadata for a folder that is
  temporarily missing stays put and applies again when the folder returns.
* A row that carries nothing (not favorite, not hidden, never opened) is
  removed, so the table only ever holds real preferences.

Every write is one ``BEGIN IMMEDIATE`` upsert, so concurrent flag updates on
the same project from the same user compose instead of overwriting each other.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from ..project_catalog import ProjectIdentity

logger = logging.getLogger(__name__)

DEFAULT_RECENT_LIMIT = 25
MAX_RECENT_LIMIT = 200


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    """One user's view of one catalog project."""

    identity: ProjectIdentity
    favorite: bool = False
    hidden: bool = False
    last_opened_at: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.favorite or self.hidden or self.last_opened_at is not None)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("catalog timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _from_row(row: aiosqlite.Row) -> ProjectMetadata | None:
    """Rebuild a row; a key this code cannot parse is logged and skipped, never raised."""
    try:
        identity = ProjectIdentity.from_key(str(row["project_key"]))
    except ValueError:
        logger.warning("Skipping catalog metadata with malformed key %r", row["project_key"])
        return None
    return ProjectMetadata(
        identity=identity,
        favorite=bool(row["favorite"]),
        hidden=bool(row["hidden"]),
        last_opened_at=_parse(row["last_opened_at"]),
    )


def _rows_to_metadata(rows: Iterable[aiosqlite.Row]) -> list[ProjectMetadata]:
    result: list[ProjectMetadata] = []
    for row in rows:
        item = _from_row(row)
        if item is not None:
            result.append(item)
    return result


class ProjectCatalogRepository:
    """SQLite access for ``project_catalog_metadata``."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        return self._db_path

    async def init_db(self) -> None:
        """Create the table when this repository is used outside ``init_db``."""
        from .models import _PROJECT_CATALOG_SCHEMA

        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_PROJECT_CATALOG_SCHEMA)
            await db.commit()

    # -- writes -----------------------------------------------------------

    async def set_favorite(
        self, guild_id: int, user_id: int, identity: ProjectIdentity, favorite: bool
    ) -> ProjectMetadata | None:
        return await self._upsert(guild_id, user_id, identity, favorite=favorite)

    async def set_hidden(
        self, guild_id: int, user_id: int, identity: ProjectIdentity, hidden: bool
    ) -> ProjectMetadata | None:
        return await self._upsert(guild_id, user_id, identity, hidden=hidden)

    async def record_recent(
        self,
        guild_id: int,
        user_id: int,
        identity: ProjectIdentity,
        *,
        now: datetime | None = None,
    ) -> ProjectMetadata | None:
        stamp = now if now is not None else datetime.now(UTC)
        return await self._upsert(guild_id, user_id, identity, last_opened_at=stamp)

    async def _upsert(
        self,
        guild_id: int,
        user_id: int,
        identity: ProjectIdentity,
        *,
        favorite: bool | None = None,
        hidden: bool | None = None,
        last_opened_at: datetime | None = None,
    ) -> ProjectMetadata | None:
        """Merge one change into the row, then drop the row if nothing is left."""
        updated = _iso(datetime.now(UTC))
        key = identity.key
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                INSERT INTO project_catalog_metadata
                    (guild_id, user_id, project_key, favorite, hidden, last_opened_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, project_key) DO UPDATE SET
                    favorite = COALESCE(?, favorite),
                    hidden = COALESCE(?, hidden),
                    last_opened_at = COALESCE(?, last_opened_at),
                    updated_at = excluded.updated_at
                """,
                (
                    int(guild_id),
                    int(user_id),
                    key,
                    int(bool(favorite)),
                    int(bool(hidden)),
                    _iso(last_opened_at) if last_opened_at is not None else None,
                    updated,
                    None if favorite is None else int(favorite),
                    None if hidden is None else int(hidden),
                    _iso(last_opened_at) if last_opened_at is not None else None,
                ),
            )
            await db.execute(
                """
                DELETE FROM project_catalog_metadata
                 WHERE guild_id = ? AND user_id = ? AND project_key = ?
                   AND favorite = 0 AND hidden = 0 AND last_opened_at IS NULL
                """,
                (int(guild_id), int(user_id), key),
            )
            cursor = await db.execute(
                "SELECT * FROM project_catalog_metadata "
                "WHERE guild_id = ? AND user_id = ? AND project_key = ?",
                (int(guild_id), int(user_id), key),
            )
            row = await cursor.fetchone()
            await db.commit()
        return None if row is None else _from_row(row)

    # -- reads ------------------------------------------------------------

    async def get(
        self, guild_id: int, user_id: int, identity: ProjectIdentity
    ) -> ProjectMetadata | None:
        rows = await self._query(
            "SELECT * FROM project_catalog_metadata "
            "WHERE guild_id = ? AND user_id = ? AND project_key = ?",
            (int(guild_id), int(user_id), identity.key),
        )
        found = _rows_to_metadata(rows)
        return found[0] if found else None

    async def list_for_user(
        self, guild_id: int, user_id: int
    ) -> dict[ProjectIdentity, ProjectMetadata]:
        rows = await self._query(
            "SELECT * FROM project_catalog_metadata WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        return {item.identity: item for item in _rows_to_metadata(rows)}

    async def favorites(self, guild_id: int, user_id: int) -> list[ProjectMetadata]:
        rows = await self._query(
            "SELECT * FROM project_catalog_metadata "
            "WHERE guild_id = ? AND user_id = ? AND favorite = 1 ORDER BY project_key",
            (int(guild_id), int(user_id)),
        )
        return _rows_to_metadata(rows)

    async def hidden(self, guild_id: int, user_id: int) -> list[ProjectMetadata]:
        rows = await self._query(
            "SELECT * FROM project_catalog_metadata "
            "WHERE guild_id = ? AND user_id = ? AND hidden = 1 ORDER BY project_key",
            (int(guild_id), int(user_id)),
        )
        return _rows_to_metadata(rows)

    async def recents(
        self, guild_id: int, user_id: int, *, limit: int = DEFAULT_RECENT_LIMIT
    ) -> list[ProjectMetadata]:
        bounded = max(1, min(int(limit), MAX_RECENT_LIMIT))
        rows = await self._query(
            "SELECT * FROM project_catalog_metadata "
            "WHERE guild_id = ? AND user_id = ? AND last_opened_at IS NOT NULL "
            "ORDER BY last_opened_at DESC, project_key LIMIT ?",
            (int(guild_id), int(user_id), bounded),
        )
        return _rows_to_metadata(rows)

    async def count(self, guild_id: int, user_id: int) -> int:
        rows = await self._query(
            "SELECT COUNT(*) AS n FROM project_catalog_metadata WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        )
        return int(rows[0]["n"]) if rows else 0

    async def _query(self, sql: str, params: tuple[object, ...]) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            return list(await cursor.fetchall())


__all__ = [
    "DEFAULT_RECENT_LIMIT",
    "MAX_RECENT_LIMIT",
    "ProjectCatalogRepository",
    "ProjectMetadata",
]
