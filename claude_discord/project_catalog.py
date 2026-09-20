"""Immutable domain model for the shared local project catalog.

One computer, one catalog: Discord's New session and the local Claude, Codex and
DSH adapters must agree on what a project *is* before they can agree on which
folder to open.  This module holds only that vocabulary — approved roots,
identities, availability, bounded queries, and the five typed resolution
results.  Discovery (task 1.2) and owner aliases (task 1.3) build on it.

Two rules shape every type here:

* **An absolute path is not an identity.**  ``/home/drew/main-projects/alpha``
  and ``C:\\Users\\david\\projects\\alpha`` are different folders on different
  computers, and a moved root must not orphan a user's favorites.  Identity is
  owner + computer + approved-root key + direct-child folder name; the canonical
  path travels alongside it as local runtime data.
* **Only a locally available project yields a working directory.**  Missing,
  unreadable and remote results all return ``None`` from
  ``working_directory``, so a stale or remote answer cannot be bound to a
  session by accident.

:class:`ProjectDiscovery` is the one part that reads the disk, and it reads
exactly one directory level per approved root: every direct child directory is a
project, nothing deeper is, and nothing outside an approved root can become one.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePath

DEFAULT_QUERY_LIMIT = 25
MAX_QUERY_LIMIT = 200

#: How long one scan may be reused before the disk is read again.  Short on
#: purpose: a folder created seconds ago should show up in the next query, and
#: session creation revalidates its chosen folder regardless.
DEFAULT_DISCOVERY_CACHE_TTL = 2.0

#: Upper bound on the projects one snapshot returns, so a root holding thousands
#: of direct children cannot flood a caller or a Discord view.
MAX_DISCOVERED_PROJECTS = 500

#: Direct children that are build or dependency output rather than projects.
#: Names beginning with ``.`` are skipped separately.
IGNORED_PROJECT_NAMES = frozenset({"__pycache__", "build", "dist", "node_modules", "venv"})

_TOKEN_SEPARATOR = ":"
_INVALID_TOKEN_CHARS = re.compile(r"[^a-z0-9]+")
_RESERVED_NAMES = frozenset({".", ".."})


def normalize_token(value: str, *, kind: str) -> str:
    """Normalize an owner, computer or root key into a portable lowercase token.

    Tokens are restricted to ``[a-z0-9-]`` so an identity key can be split back
    apart unambiguously — only the folder name, which comes last, is free-form.
    """
    token = _INVALID_TOKEN_CHARS.sub("-", value.strip().lower()).strip("-")
    if not token:
        raise ValueError(f"A catalog {kind} must contain at least one letter or digit")
    return token


def validate_child_name(name: str) -> str:
    """Accept only a direct-child folder name, preserving its exact spelling.

    Case is preserved deliberately: on a case-sensitive filesystem ``Alpha`` and
    ``alpha`` are two different projects, and lowercasing would merge them.
    """
    if not name.strip():
        raise ValueError("A catalog project name must not be empty")
    if name in _RESERVED_NAMES:
        raise ValueError(f"{name!r} is not a project folder name")
    if "/" in name or "\\" in name or "\0" in name:
        raise ValueError(f"{name!r} is not a direct child folder name")
    return name


class Availability(StrEnum):
    """Whether a root or project can be used right now."""

    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    UNKNOWN = "unknown"

    @property
    def is_available(self) -> bool:
        return self is Availability.AVAILABLE


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """A stable, path-free identity for one catalog project."""

    owner: str
    computer: str
    root_key: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        object.__setattr__(self, "root_key", normalize_token(self.root_key, kind="root key"))
        object.__setattr__(self, "name", validate_child_name(self.name))

    @property
    def key(self) -> str:
        """The storage key for personal metadata; survives a moved root."""
        return _TOKEN_SEPARATOR.join((self.owner, self.computer, self.root_key, self.name))

    @property
    def qualifier(self) -> str:
        """Human-readable owner and computer context, e.g. ``drew/drewai``."""
        return f"{self.owner}/{self.computer}"

    @classmethod
    def from_key(cls, key: str) -> ProjectIdentity:
        """Rebuild an identity written by :attr:`key`."""
        parts = key.split(_TOKEN_SEPARATOR, 3)
        if len(parts) != 4 or not all(parts[:3]):
            raise ValueError(f"Malformed catalog identity key: {key!r}")
        owner, computer, root_key, name = parts
        return cls(owner=owner, computer=computer, root_key=root_key, name=name)

    def __str__(self) -> str:
        return self.key


@dataclass(frozen=True, slots=True)
class ApprovedRoot:
    """One explicitly approved project root — configuration, not discovery."""

    key: str
    path: PurePath
    owner: str
    computer: str
    display_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", normalize_token(self.key, kind="root key"))
        object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        if not self.path.is_absolute():
            raise ValueError(f"An approved root must be an absolute path: {self.path}")

    @property
    def label(self) -> str:
        return self.display_label or self.key

    def with_label(self, label: str) -> ApprovedRoot:
        return replace(self, display_label=label)

    def child_identity(self, name: str) -> ProjectIdentity:
        """The identity of a direct child of this root, without touching disk."""
        return ProjectIdentity(
            owner=self.owner,
            computer=self.computer,
            root_key=self.key,
            name=name,
        )

    def child_path(self, name: str) -> PurePath:
        return self.path / validate_child_name(name)


@dataclass(frozen=True, slots=True)
class RootStatus:
    """A root's availability at query time, reported as data rather than an error."""

    root: ApprovedRoot
    availability: Availability = Availability.AVAILABLE
    reason: str | None = None

    @property
    def is_available(self) -> bool:
        return self.availability.is_available


@dataclass(frozen=True, slots=True)
class CatalogProject:
    """A discovered direct child of an approved root."""

    identity: ProjectIdentity
    path: PurePath
    availability: Availability = Availability.AVAILABLE

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError(f"A catalog project path must be absolute: {self.path}")
        if self.path.name != self.identity.name:
            raise ValueError(
                f"{self.path} is not the direct child folder {self.identity.name!r} "
                "of its approved root"
            )

    @property
    def is_available(self) -> bool:
        return self.availability.is_available

    @property
    def display_name(self) -> str:
        return self.identity.name

    @property
    def working_directory(self) -> PurePath | None:
        """The path a session may bind — ``None`` unless the project is available."""
        return self.path if self.is_available else None

    def with_availability(self, availability: Availability) -> CatalogProject:
        return replace(self, availability=availability)


def disambiguated_labels(projects: Iterable[CatalogProject]) -> Mapping[ProjectIdentity, str]:
    """Label each project by folder name, qualified only where a name repeats."""
    by_name: dict[str, list[CatalogProject]] = defaultdict(list)
    for project in projects:
        by_name[project.identity.name].append(project)

    labels: dict[ProjectIdentity, str] = {}
    for name, group in by_name.items():
        if len(group) == 1:
            labels[group[0].identity] = name
            continue
        one_computer = len({project.identity.qualifier for project in group}) == 1
        for project in group:
            identity = project.identity
            if one_computer:
                labels[identity] = f"{name} ({identity.root_key})"
            elif len({p.identity.qualifier for p in group}) == len(group):
                labels[identity] = f"{name} ({identity.qualifier})"
            else:
                labels[identity] = f"{name} ({identity.qualifier}, {identity.root_key})"
    return labels


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """A bounded catalog request; the limit is clamped rather than trusted."""

    text: str = ""
    owner: str | None = None
    limit: int = DEFAULT_QUERY_LIMIT
    include_hidden: bool = False
    include_unavailable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self.text.strip())
        owner = self.owner.strip() if self.owner else ""
        object.__setattr__(self, "owner", normalize_token(owner, kind="owner") if owner else None)
        object.__setattr__(self, "limit", max(1, min(int(self.limit), MAX_QUERY_LIMIT)))

    def matches(self, project: CatalogProject) -> bool:
        """Pure predicate over already-discovered projects; never reads the disk."""
        if not project.is_available and not self.include_unavailable:
            return False
        if self.owner and project.identity.owner != self.owner:
            return False
        return self.text.lower() in project.identity.name.lower()


class ResolutionKind(StrEnum):
    """The five outcomes a catalog resolution can have."""

    LOCAL_AVAILABLE = "local_available"
    LOCAL_UNAVAILABLE = "local_unavailable"
    REMOTE_TARGET = "remote_target"
    AMBIGUOUS_OWNER = "ambiguous_owner"
    NO_MATCH = "no_match"


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Base of the typed resolution union; callers match on :attr:`kind`."""

    @property
    def kind(self) -> ResolutionKind:  # pragma: no cover - overridden by every member
        raise NotImplementedError

    @property
    def is_local_available(self) -> bool:
        return False

    @property
    def working_directory(self) -> PurePath | None:
        """Only a locally available project has one; everything else returns None."""
        return None


@dataclass(frozen=True, slots=True)
class LocalProjectResolution(ResolutionResult):
    """A project on this computer that can be opened right now."""

    project: CatalogProject

    def __post_init__(self) -> None:
        if not self.project.is_available:
            raise ValueError(
                "LocalProjectResolution requires an available project; "
                f"{self.project.identity} is {self.project.availability.value}"
            )

    @property
    def kind(self) -> ResolutionKind:
        return ResolutionKind.LOCAL_AVAILABLE

    @property
    def is_local_available(self) -> bool:
        return True

    @property
    def working_directory(self) -> PurePath | None:
        return self.project.working_directory


@dataclass(frozen=True, slots=True)
class LocalUnavailableResolution(ResolutionResult):
    """A known local identity whose folder cannot be used at the moment."""

    project: CatalogProject
    reason: str = ""

    def __post_init__(self) -> None:
        if self.project.is_available:
            raise ValueError("LocalUnavailableResolution requires a project that is not available")

    @property
    def kind(self) -> ResolutionKind:
        return ResolutionKind.LOCAL_UNAVAILABLE

    @property
    def availability(self) -> Availability:
        return self.project.availability


@dataclass(frozen=True, slots=True)
class RemoteTargetResolution(ResolutionResult):
    """An owner-qualified target on a trusted computer — deliberately path-free.

    The handoff subsystem turns this into a request for the destination
    computer.  It carries no working directory because the same folder name can
    mean something unrelated here, and a remote path would bypass that
    computer's own permissions.
    """

    owner: str
    computer: str
    requested_terms: tuple[str, ...] = ()
    availability: Availability = Availability.UNKNOWN
    verified_at: datetime | None = None
    queued: bool = False
    source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        terms = tuple(term.strip() for term in self.requested_terms if term.strip())
        object.__setattr__(self, "requested_terms", terms)

    @property
    def kind(self) -> ResolutionKind:
        return ResolutionKind.REMOTE_TARGET

    @property
    def is_locally_verified(self) -> bool:
        """Always False: remote state is reported, never verified from here."""
        return False


@dataclass(frozen=True, slots=True)
class AmbiguousOwnerResolution(ResolutionResult):
    """An owner phrase that maps to more than one computer; ask, never guess."""

    requested_owner: str
    candidate_owners: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "requested_owner", normalize_token(self.requested_owner, kind="owner")
        )
        seen: dict[str, None] = {}
        for candidate in self.candidate_owners:
            seen.setdefault(normalize_token(candidate, kind="owner"), None)
        object.__setattr__(self, "candidate_owners", tuple(seen))

    @property
    def kind(self) -> ResolutionKind:
        return ResolutionKind.AMBIGUOUS_OWNER


@dataclass(frozen=True, slots=True)
class NoMatchResolution(ResolutionResult):
    """Nothing matched; the query is returned so the caller can explain why."""

    query: CatalogQuery = field(default_factory=CatalogQuery)

    @property
    def kind(self) -> ResolutionKind:
        return ResolutionKind.NO_MATCH


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """One query's answer: root availability plus the projects that were listed."""

    roots: tuple[RootStatus, ...] = ()
    projects: tuple[CatalogProject, ...] = ()
    truncated: bool = False

    @property
    def available_projects(self) -> tuple[CatalogProject, ...]:
        return tuple(project for project in self.projects if project.is_available)

    @property
    def unavailable_roots(self) -> tuple[RootStatus, ...]:
        return tuple(status for status in self.roots if not status.is_available)


def _project_sort_key(project: CatalogProject) -> tuple[str, str, str, str, str]:
    """Total order: folder name first, then the identity that disambiguates it.

    Case-insensitive so ``Zeta`` does not sort before ``alpha``, with the exact
    name as a tiebreaker so two folders differing only in case keep a stable
    order.
    """
    identity = project.identity
    return (
        identity.name.casefold(),
        identity.name,
        identity.root_key,
        identity.computer,
        identity.owner,
    )


class ProjectDiscovery:
    """Bounded one-level discovery of projects under approved roots.

    One scan reads the direct children of each approved root, keeps the readable
    directories, and returns them in a deterministic order together with each
    root's availability.  It never recurses, never treats a file as a project and
    never emits a path outside an approved root: a project's path is always
    ``root.path / name``.

    Scans are cheap but not free, so a snapshot is reused for
    ``cache_ttl`` seconds.  Callers that must see the current state ask for
    ``refresh=True``; :meth:`revalidate` re-checks a single project immediately
    before a session binds its working directory.
    """

    def __init__(
        self,
        roots: Iterable[ApprovedRoot],
        *,
        cache_ttl: float = DEFAULT_DISCOVERY_CACHE_TTL,
        max_projects: int = MAX_DISCOVERED_PROJECTS,
        ignored_names: Iterable[str] = IGNORED_PROJECT_NAMES,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self._roots = tuple(roots)
        self._cache_ttl = max(0.0, float(cache_ttl))
        self._max_projects = max(1, int(max_projects))
        self._ignored_names = frozenset(ignored_names)
        self._now = time_source
        self._lock = threading.Lock()
        self._cached: CatalogSnapshot | None = None
        self._cached_at = 0.0

    @property
    def roots(self) -> tuple[ApprovedRoot, ...]:
        return self._roots

    def snapshot(self, *, refresh: bool = False) -> CatalogSnapshot:
        """Return the current catalog, scanning the disk unless the cache is fresh."""
        with self._lock:
            cached = self._cached
            age = self._now() - self._cached_at
            if cached is not None and not refresh and age < self._cache_ttl:
                return cached
            scanned = self._scan()
            self._cached = scanned
            self._cached_at = self._now()
            return scanned

    async def snapshot_async(self, *, refresh: bool = False) -> CatalogSnapshot:
        """Same answer as :meth:`snapshot`, off the event loop."""
        return await asyncio.to_thread(self.snapshot, refresh=refresh)

    def invalidate(self) -> None:
        """Drop the cached scan so the next query reads the disk."""
        with self._lock:
            self._cached = None
            self._cached_at = 0.0

    def revalidate(self, project: CatalogProject) -> CatalogProject:
        """Re-check one project's folder, keeping its identity and path.

        Used immediately before a session binds a working directory: a project
        that has since been deleted or replaced comes back unavailable, and
        :attr:`CatalogProject.working_directory` then returns ``None``.
        """
        return project.with_availability(self._availability_of(Path(project.path)))

    def _scan(self) -> CatalogSnapshot:
        statuses: list[RootStatus] = []
        projects: list[CatalogProject] = []
        for root in self._roots:
            status, found = self._scan_root(root)
            statuses.append(status)
            projects.extend(found)

        projects.sort(key=_project_sort_key)
        truncated = len(projects) > self._max_projects
        if truncated:
            projects = projects[: self._max_projects]
        return CatalogSnapshot(
            roots=tuple(statuses),
            projects=tuple(projects),
            truncated=truncated,
        )

    def _scan_root(self, root: ApprovedRoot) -> tuple[RootStatus, list[CatalogProject]]:
        """One directory level of one root; an unusable root is data, not an error."""
        try:
            with os.scandir(Path(root.path)) as entries:
                names = [entry.name for entry in entries if self._is_project_entry(entry)]
        except FileNotFoundError:
            return RootStatus(root, Availability.MISSING, "The approved root does not exist"), []
        except NotADirectoryError:
            return (
                RootStatus(root, Availability.UNREADABLE, "The approved root is not a directory"),
                [],
            )
        except PermissionError:
            unreadable = RootStatus(
                root, Availability.UNREADABLE, "The approved root is not readable"
            )
            return unreadable, []
        except OSError as error:
            reason = f"The approved root could not be read: {error.strerror or error}"
            return RootStatus(root, Availability.UNREADABLE, reason), []

        found = [
            CatalogProject(identity=root.child_identity(name), path=root.child_path(name))
            for name in names
        ]
        return RootStatus(root, Availability.AVAILABLE), found

    def _is_project_entry(self, entry: os.DirEntry[str]) -> bool:
        name = entry.name
        if name.startswith(".") or name in self._ignored_names:
            return False
        try:
            validate_child_name(name)
        except ValueError:
            return False
        try:
            # Follows symlinks: a symlinked project folder is still a direct
            # child, and its catalog path stays inside the approved root.  A
            # dangling symlink is not a directory and is skipped.
            return entry.is_dir()
        except OSError:
            return False

    @staticmethod
    def _availability_of(path: Path) -> Availability:
        try:
            if path.is_dir():
                return Availability.AVAILABLE
            return Availability.MISSING if not path.exists() else Availability.UNREADABLE
        except OSError:
            return Availability.UNREADABLE


__all__ = [
    "DEFAULT_DISCOVERY_CACHE_TTL",
    "DEFAULT_QUERY_LIMIT",
    "IGNORED_PROJECT_NAMES",
    "MAX_DISCOVERED_PROJECTS",
    "MAX_QUERY_LIMIT",
    "AmbiguousOwnerResolution",
    "ApprovedRoot",
    "Availability",
    "CatalogProject",
    "CatalogQuery",
    "CatalogSnapshot",
    "LocalProjectResolution",
    "LocalUnavailableResolution",
    "NoMatchResolution",
    "ProjectDiscovery",
    "ProjectIdentity",
    "RemoteTargetResolution",
    "ResolutionKind",
    "ResolutionResult",
    "RootStatus",
    "disambiguated_labels",
    "normalize_token",
    "validate_child_name",
]
