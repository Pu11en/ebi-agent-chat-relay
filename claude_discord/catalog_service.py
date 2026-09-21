"""The one project catalog a deployment shares.

``setup_bridge`` builds a single :class:`ProjectCatalogService` from the
machine's :class:`CatalogConfig` and hands the same object to the launcher,
the REST control plane (and through it every local harness) and custom Cogs
via ``BridgeComponents.project_catalog``.  One instance means one answer:
New session and a Claude, Codex or DSH task that resolve the same identity
get the same owner, availability and canonical path.

The service is frontend-neutral.  It composes the pieces that already exist —
:class:`ProjectDiscovery` (filesystem truth), :class:`OwnerRegistry` and
:class:`CatalogResolver` (owner-aware resolution),
:class:`ProjectCatalogRepository` (personal metadata) and the legacy
migration — and adds the two things a consumer actually asks for: a
per-user *listing* (favorites first, hidden omitted from browse but found by
search) and a *find-by-key* that revalidates the folder and refuses anything
that resolves outside its approved root, immediately before a session binds
it.

Nothing here reads a project's contents.  Discovery is one directory level;
``find`` checks one folder.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .catalog_config import CATALOG_ACTIONS, CatalogConfig
from .catalog_migration import (
    LEGACY_FAVORITES_KEY,
    LEGACY_RECENTS_KEY,
    LegacyMapping,
    map_legacy_path,
    map_legacy_paths,
    migrate_launcher_metadata,
)
from .database.project_catalog_repo import ProjectCatalogRepository, ProjectMetadata
from .project_catalog import (
    DEFAULT_DISCOVERY_CACHE_TTL,
    DEFAULT_QUERY_LIMIT,
    AmbiguousOwnerResolution,
    ApprovedRoot,
    Availability,
    CatalogProject,
    CatalogResolver,
    CatalogSnapshot,
    LocalProjectResolution,
    LocalUnavailableResolution,
    NoMatchResolution,
    OwnerRegistry,
    ProjectDiscovery,
    ProjectIdentity,
    RemoteTargetResolution,
    ResolutionResult,
    RootStatus,
    disambiguated_labels,
)

logger = logging.getLogger(__name__)


class _Settings(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One project as one user sees it: the project plus their metadata."""

    project: CatalogProject
    label: str
    favorite: bool = False
    hidden: bool = False
    last_opened_at: datetime | None = None
    actions: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.project.identity.key

    @property
    def path(self) -> str:
        return str(self.project.path)


@dataclass(frozen=True, slots=True)
class CatalogListing:
    """A bounded, user-specific view of the catalog."""

    entries: tuple[CatalogEntry, ...] = ()
    roots: tuple[RootStatus, ...] = ()
    truncated: bool = False

    @property
    def unavailable_roots(self) -> tuple[RootStatus, ...]:
        return tuple(status for status in self.roots if not status.is_available)


@dataclass(frozen=True, slots=True)
class LegacyLeftovers:
    """Legacy launcher paths the catalog could not adopt; the browser still offers them."""

    favorites: tuple[str, ...] = ()
    recents: tuple[str, ...] = ()


class ProjectCatalogService:
    """Discovery + resolution + personal metadata behind one object."""

    def __init__(
        self,
        config: CatalogConfig,
        metadata: ProjectCatalogRepository | None,
        *,
        settings: _Settings | None = None,
        discovery: ProjectDiscovery | None = None,
        cache_ttl: float = DEFAULT_DISCOVERY_CACHE_TTL,
    ) -> None:
        self._config = config
        self._metadata = metadata
        self._settings = settings
        self._discovery = discovery or ProjectDiscovery(config.roots, cache_ttl=cache_ttl)
        self._registry = config.registry()
        self._resolver = CatalogResolver(self._discovery, self._registry)
        self._migration_lock = asyncio.Lock()
        self._migrated: set[tuple[int, int]] = set()

    # -- composition --------------------------------------------------------

    @property
    def config(self) -> CatalogConfig:
        return self._config

    @property
    def roots(self) -> tuple[ApprovedRoot, ...]:
        return self._config.roots

    @property
    def registry(self) -> OwnerRegistry:
        return self._registry

    @property
    def discovery(self) -> ProjectDiscovery:
        return self._discovery

    @property
    def resolver(self) -> CatalogResolver:
        return self._resolver

    @property
    def metadata(self) -> ProjectCatalogRepository | None:
        return self._metadata

    @property
    def settings(self) -> _Settings | None:
        return self._settings

    @property
    def local_qualifier(self) -> str:
        return f"{self._config.machine.owner}/{self._config.machine.computer}"

    def actions(self) -> tuple[str, ...]:
        """The actions this computer offers for a local project."""
        return self._config.available_actions(a for a in CATALOG_ACTIONS if a != "handoff")

    # -- resolution (no user metadata) --------------------------------------

    async def search(
        self,
        text: str = "",
        *,
        owner: str | None = None,
        limit: int | None = None,
        refresh: bool = False,
        include_unavailable: bool = False,
    ) -> CatalogSnapshot:
        return await self._resolver.search_async(
            text,
            owner=owner,
            limit=limit,
            refresh=refresh,
            include_unavailable=include_unavailable,
        )

    async def resolve(
        self, text: str = "", *, owner: str | None = None, refresh: bool = True
    ) -> ResolutionResult:
        return await self._resolver.resolve_async(text, owner=owner, refresh=refresh)

    def identity_for_path(self, path: str) -> ProjectIdentity | None:
        """The identity of a local path if it is a direct child of an approved root."""
        return map_legacy_path(path, self.roots)

    def root_for(self, identity: ProjectIdentity) -> ApprovedRoot | None:
        for root in self.roots:
            if (
                root.owner == identity.owner
                and root.computer == identity.computer
                and root.key == identity.root_key
            ):
                return root
        return None

    async def find(self, key: str, *, refresh: bool = True) -> CatalogProject | None:
        """The project behind an identity key, revalidated right now.

        ``None`` for a key this computer never issued (malformed, another
        computer, an unknown root).  A known identity whose folder is gone or
        which resolves outside its root comes back *unavailable* — its
        ``working_directory`` is then ``None``, so a caller cannot bind it.
        """
        try:
            identity = ProjectIdentity.from_key(key)
        except ValueError:
            return None
        root = self.root_for(identity)
        if root is None:
            return None
        candidate = CatalogProject(identity=identity, path=root.child_path(identity.name))
        return await asyncio.to_thread(self._revalidate_inside_root, candidate, root)

    def _revalidate_inside_root(
        self, project: CatalogProject, root: ApprovedRoot
    ) -> CatalogProject:
        checked = self._discovery.revalidate(project)
        if not checked.is_available:
            return checked
        try:
            real = Path(project.path).resolve(strict=True)
            real_root = Path(root.path).resolve(strict=True)
            real.relative_to(real_root)
        except (OSError, RuntimeError, ValueError):
            logger.warning(
                "Catalog project %s resolves outside its approved root; refusing", project.path
            )
            return checked.with_availability(Availability.UNREADABLE)
        return checked

    # -- personal metadata ---------------------------------------------------

    async def _user_metadata(
        self, guild_id: int, user_id: int
    ) -> Mapping[ProjectIdentity, ProjectMetadata]:
        if self._metadata is None:
            return {}
        await self.ensure_migrated(guild_id, user_id)
        return await self._metadata.list_for_user(guild_id, user_id)

    async def ensure_migrated(self, guild_id: int, user_id: int) -> None:
        """Adopt the user's legacy launcher lists once; safe to call on every open."""
        if self._metadata is None or self._settings is None:
            return
        marker = (guild_id, user_id)
        if marker in self._migrated:
            return
        async with self._migration_lock:
            if marker in self._migrated:
                return
            try:
                await migrate_launcher_metadata(
                    self._settings, self._metadata, self.roots, guild_id, user_id
                )
            except Exception:
                logger.exception("Legacy launcher metadata migration failed; continuing")
            self._migrated.add(marker)

    async def legacy_unmapped(self, guild_id: int, user_id: int) -> LegacyLeftovers:
        """Legacy favorites/recents outside every approved root, still usable by path."""
        if self._settings is None:
            return LegacyLeftovers()
        favorites = await self._legacy_mapping(
            LEGACY_FAVORITES_KEY.format(guild_id=guild_id, user_id=user_id)
        )
        recents = await self._legacy_mapping(
            LEGACY_RECENTS_KEY.format(guild_id=guild_id, user_id=user_id)
        )
        return LegacyLeftovers(favorites=favorites.unmapped, recents=recents.unmapped)

    async def _legacy_mapping(self, key: str) -> LegacyMapping:
        import json

        assert self._settings is not None
        raw = await self._settings.get(key)
        if not raw:
            return LegacyMapping()
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            return LegacyMapping()
        if not isinstance(values, list):
            return LegacyMapping()
        return map_legacy_paths([v for v in values if isinstance(v, str)], self.roots)

    async def set_favorite(self, guild_id: int, user_id: int, key: str, favorite: bool) -> bool:
        identity = self._identity(key)
        if identity is None or self._metadata is None:
            return False
        await self.ensure_migrated(guild_id, user_id)
        await self._metadata.set_favorite(guild_id, user_id, identity, favorite)
        return True

    async def set_hidden(self, guild_id: int, user_id: int, key: str, hidden: bool) -> bool:
        identity = self._identity(key)
        if identity is None or self._metadata is None:
            return False
        await self.ensure_migrated(guild_id, user_id)
        await self._metadata.set_hidden(guild_id, user_id, identity, hidden)
        return True

    async def remember_path(
        self, guild_id: int, user_id: int, path: str, *, now: datetime | None = None
    ) -> ProjectIdentity | None:
        """Record an open by path; returns the identity, or None outside the roots."""
        identity = self.identity_for_path(path)
        if identity is None or self._metadata is None:
            return identity
        await self.ensure_migrated(guild_id, user_id)
        await self._metadata.record_recent(guild_id, user_id, identity, now=now)
        return identity

    def _identity(self, key: str) -> ProjectIdentity | None:
        try:
            identity = ProjectIdentity.from_key(key)
        except ValueError:
            return None
        return identity if self.root_for(identity) is not None else None

    # -- user-facing listings ----------------------------------------------

    async def list_projects(
        self,
        guild_id: int,
        user_id: int,
        *,
        query: str = "",
        include_hidden: bool = False,
        limit: int = DEFAULT_QUERY_LIMIT,
        refresh: bool = False,
    ) -> CatalogListing:
        """Every available project for this user: favorites first, then by name.

        Hidden projects are omitted from a browse (empty ``query``) and shown,
        flagged, when the user searched for them or asked for them.
        """
        snapshot = await self.search(query, limit=max(limit, 1), refresh=refresh)
        metadata = await self._user_metadata(guild_id, user_id)
        entries = self._entries(snapshot.projects, metadata)
        if not include_hidden and not query.strip():
            entries = [entry for entry in entries if not entry.hidden]
        entries.sort(key=lambda e: (not e.favorite, e.label.casefold(), e.label))
        truncated = snapshot.truncated or len(entries) > limit
        return CatalogListing(
            entries=tuple(entries[:limit]), roots=snapshot.roots, truncated=truncated
        )

    async def favorites(self, guild_id: int, user_id: int) -> list[CatalogEntry]:
        listing = await self.list_projects(guild_id, user_id, include_hidden=True)
        return [entry for entry in listing.entries if entry.favorite]

    async def recents(
        self, guild_id: int, user_id: int, *, limit: int = DEFAULT_QUERY_LIMIT
    ) -> list[CatalogEntry]:
        """Recently opened projects, newest first, only those available right now."""
        if self._metadata is None:
            return []
        await self.ensure_migrated(guild_id, user_id)
        recent = await self._metadata.recents(guild_id, user_id, limit=limit)
        if not recent:
            return []
        snapshot = await self._discovery.snapshot_async(refresh=True)
        available = snapshot.available_projects
        by_identity = {project.identity: project for project in available}
        labels = disambiguated_labels(available)
        entries: list[CatalogEntry] = []
        for item in recent:
            project = by_identity.get(item.identity)
            if project is None:
                continue
            entries.append(self._entry(project, labels.get(project.identity), item))
        return entries

    def _entries(
        self,
        projects: Iterable[CatalogProject],
        metadata: Mapping[ProjectIdentity, ProjectMetadata],
    ) -> list[CatalogEntry]:
        found = tuple(projects)
        labels = disambiguated_labels(found)
        return [
            self._entry(project, labels.get(project.identity), metadata.get(project.identity))
            for project in found
        ]

    def _entry(
        self, project: CatalogProject, label: str | None, meta: ProjectMetadata | None
    ) -> CatalogEntry:
        return CatalogEntry(
            project=project,
            label=label or project.display_name,
            favorite=bool(meta and meta.favorite),
            hidden=bool(meta and meta.hidden),
            last_opened_at=meta.last_opened_at if meta else None,
            actions=self.actions() if project.is_available else (),
        )


# ---------------------------------------------------------------------------
# Serialization shared by the REST interface and the harness CLI helper.
# ---------------------------------------------------------------------------


def project_to_dict(project: CatalogProject) -> dict[str, Any]:
    identity = project.identity
    payload: dict[str, Any] = {
        "key": identity.key,
        "name": identity.name,
        "owner": identity.owner,
        "computer": identity.computer,
        "root": identity.root_key,
        "availability": project.availability.value,
    }
    payload["path"] = str(project.path)
    return payload


def entry_to_dict(entry: CatalogEntry) -> dict[str, Any]:
    payload = project_to_dict(entry.project)
    payload.update(
        {
            "label": entry.label,
            "favorite": entry.favorite,
            "hidden": entry.hidden,
            "last_opened_at": (
                entry.last_opened_at.isoformat() if entry.last_opened_at is not None else None
            ),
            "actions": list(entry.actions),
        }
    )
    return payload


def resolution_to_dict(result: ResolutionResult) -> dict[str, Any]:
    """A typed result as JSON; a remote target never carries a path."""
    payload: dict[str, Any] = {"kind": result.kind.value}
    if isinstance(result, LocalProjectResolution):
        payload.update(project_to_dict(result.project))
        payload["locally_verified"] = True
    elif isinstance(result, LocalUnavailableResolution):
        payload.update(project_to_dict(result.project))
        payload["reason"] = result.reason
        payload["locally_verified"] = True
        payload.pop("path", None)
    elif isinstance(result, RemoteTargetResolution):
        payload.update(
            {
                "owner": result.owner,
                "computer": result.computer,
                "requested_terms": list(result.requested_terms),
                "availability": result.availability.value,
                "verified_at": (
                    result.verified_at.isoformat() if result.verified_at is not None else None
                ),
                "queued": result.queued,
                "source": result.source,
                "locally_verified": False,
            }
        )
    elif isinstance(result, AmbiguousOwnerResolution):
        payload.update(
            {
                "requested_owner": result.requested_owner,
                "candidate_owners": list(result.candidate_owners),
                "candidate_computers": list(result.candidate_computers),
                "candidates": [
                    {
                        "key": identity.key,
                        "name": identity.name,
                        "owner": identity.owner,
                        "computer": identity.computer,
                        "root": identity.root_key,
                    }
                    for identity in result.candidate_identities
                ],
            }
        )
    elif isinstance(result, NoMatchResolution):
        payload.update({"text": result.query.text, "owner": result.query.owner})
    return payload


__all__ = [
    "CatalogEntry",
    "CatalogListing",
    "LegacyLeftovers",
    "ProjectCatalogService",
    "entry_to_dict",
    "project_to_dict",
    "resolution_to_dict",
]
