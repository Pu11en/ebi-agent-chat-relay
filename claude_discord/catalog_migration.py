"""Map the launcher's legacy raw-path favorites and recents onto catalog identities.

Before the shared catalog, ``ProjectLauncherCog`` kept two JSON lists of
absolute paths per guild and user in the generic settings table
(``launcher.favorites:*`` and ``launcher.recents:*``).  This adapter reads
them once and writes the entries that are direct children of an approved root
into :class:`ProjectCatalogRepository` — by identity, so the metadata survives
a moved root.

Two things it deliberately does not do:

* It never deletes or rewrites the raw lists.  A favorite outside every
  approved root has nowhere to go in the catalog, so it stays where the manual
  folder browser still finds it, until the user removes it.
* It never consults the disk.  A folder that is absent today keeps its
  favorite and reappears with it; existence is discovery's question, not
  migration's.

The migration runs once per (guild, user): a settings marker records that the
legacy lists were read, so unfavoriting a migrated project later is not undone
on the next open.
"""

from __future__ import annotations

import json
import logging
import ntpath
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from types import ModuleType
from typing import Protocol

from .database.project_catalog_repo import ProjectCatalogRepository
from .project_catalog import ApprovedRoot, ProjectIdentity

logger = logging.getLogger(__name__)

LEGACY_FAVORITES_KEY = "launcher.favorites:{guild_id}:{user_id}"
LEGACY_RECENTS_KEY = "launcher.recents:{guild_id}:{user_id}"
MIGRATED_MARKER_KEY = "launcher.catalog_migrated:{guild_id}:{user_id}"
_LEGACY_LIMIT = 25


class _Settings(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class LegacyMapping:
    """Which raw paths became identities, and which could not."""

    mapped: tuple[tuple[str, ProjectIdentity], ...] = ()
    unmapped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MigrationReport:
    favorites_mapped: int = 0
    favorites_unmapped: tuple[str, ...] = ()
    recents_mapped: int = 0
    recents_unmapped: tuple[str, ...] = ()
    skipped: bool = False


def _flavour(path: str) -> ModuleType:
    """``ntpath`` for a Windows-looking path, else this host's ``os.path``.

    A legacy favorite written on Windows must still map when the migration runs
    on Linux (and in CI): ``posixpath`` would treat a drive-letter path as one opaque
    segment. The choice is per string so mixed lists work.
    """
    if "\\" in path or (len(path) > 1 and path[1] == ":" and path[0].isalpha()):
        return ntpath
    return os.path


def _normalize(path: str, *, case_insensitive: bool) -> str:
    flavour = _flavour(path)
    text = flavour.normpath(path.strip())
    return flavour.normcase(text) if case_insensitive else text


def map_legacy_path(
    path: str,
    roots: Iterable[ApprovedRoot],
    *,
    case_insensitive: bool | None = None,
) -> ProjectIdentity | None:
    """The identity of ``path`` if it is a direct child of an approved root, else None.

    Pure string comparison after ``normpath``; the folder need not exist.
    Case-insensitive on Windows (or when asked), so ``c:\\users`` and
    ``C:\\Users`` are the same root — the child's spelling is preserved
    because it is part of the identity.
    """
    if not path or not path.strip():
        return None
    folded = os.name == "nt" if case_insensitive is None else case_insensitive
    flavour = _flavour(path)
    raw = flavour.normpath(path.strip())
    if not PurePath(raw).is_absolute() and not (len(raw) > 1 and raw[1] == ":"):
        return None
    parent = flavour.dirname(raw)
    name = flavour.basename(raw)
    if not name or not parent:
        return None
    parent_key = _normalize(parent, case_insensitive=folded)
    for root in roots:
        if _normalize(str(root.path), case_insensitive=folded) == parent_key:
            try:
                return root.child_identity(name)
            except ValueError:
                return None
    return None


def map_legacy_paths(
    paths: Iterable[str],
    roots: Iterable[ApprovedRoot],
    *,
    case_insensitive: bool | None = None,
) -> LegacyMapping:
    known = tuple(roots)
    mapped: list[tuple[str, ProjectIdentity]] = []
    unmapped: list[str] = []
    for path in paths:
        identity = map_legacy_path(path, known, case_insensitive=case_insensitive)
        if identity is None:
            unmapped.append(path)
        else:
            mapped.append((path, identity))
    return LegacyMapping(mapped=tuple(mapped), unmapped=tuple(unmapped))


async def _legacy_list(settings: _Settings, key: str) -> list[str]:
    raw = await settings.get(key)
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Legacy launcher value under %s is not JSON; left untouched", key)
        return []
    if not isinstance(values, list):
        logger.warning("Legacy launcher value under %s is not a list; left untouched", key)
        return []
    return [item for item in values if isinstance(item, str)][:_LEGACY_LIMIT]


async def migrate_launcher_metadata(
    settings: _Settings,
    catalog: ProjectCatalogRepository,
    roots: Iterable[ApprovedRoot],
    guild_id: int,
    user_id: int,
    *,
    now: datetime | None = None,
    case_insensitive: bool | None = None,
) -> MigrationReport:
    """Copy one user's legacy favorites/recents into the catalog, once.

    Idempotent through a settings marker; the legacy keys are read, never
    written.  Recents keep their order: the first legacy entry is the newest
    and gets the latest ``last_opened_at``.
    """
    marker = MIGRATED_MARKER_KEY.format(guild_id=guild_id, user_id=user_id)
    if await settings.get(marker):
        return MigrationReport(skipped=True)

    stamp = (now if now is not None else datetime.now(UTC)).astimezone(UTC)
    known = tuple(roots)
    favorites = await _legacy_list(
        settings, LEGACY_FAVORITES_KEY.format(guild_id=guild_id, user_id=user_id)
    )
    recents = await _legacy_list(
        settings, LEGACY_RECENTS_KEY.format(guild_id=guild_id, user_id=user_id)
    )

    favorite_map = map_legacy_paths(favorites, known, case_insensitive=case_insensitive)
    for _, identity in favorite_map.mapped:
        await catalog.set_favorite(guild_id, user_id, identity, True)

    recent_map = map_legacy_paths(recents, known, case_insensitive=case_insensitive)
    for index, (_, identity) in enumerate(recent_map.mapped):
        existing = await catalog.get(guild_id, user_id, identity)
        if existing is not None and existing.last_opened_at is not None:
            continue  # a real open already recorded; do not push it back in time
        await catalog.record_recent(
            guild_id, user_id, identity, now=stamp - timedelta(seconds=index)
        )

    await settings.set(marker, stamp.isoformat())
    if favorite_map.mapped or recent_map.mapped:
        logger.info(
            "Migrated launcher metadata for user %s in guild %s: %d favorite(s), %d recent(s)",
            user_id,
            guild_id,
            len(favorite_map.mapped),
            len(recent_map.mapped),
        )
    return MigrationReport(
        favorites_mapped=len(favorite_map.mapped),
        favorites_unmapped=favorite_map.unmapped,
        recents_mapped=len(recent_map.mapped),
        recents_unmapped=recent_map.unmapped,
    )


__all__ = [
    "LEGACY_FAVORITES_KEY",
    "LEGACY_RECENTS_KEY",
    "MIGRATED_MARKER_KEY",
    "LegacyMapping",
    "MigrationReport",
    "map_legacy_path",
    "map_legacy_paths",
    "migrate_launcher_metadata",
]
