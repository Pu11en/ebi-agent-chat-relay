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

Nothing in this module touches the filesystem.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath

DEFAULT_QUERY_LIMIT = 25
MAX_QUERY_LIMIT = 200

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


__all__ = [
    "DEFAULT_QUERY_LIMIT",
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
    "ProjectIdentity",
    "RemoteTargetResolution",
    "ResolutionKind",
    "ResolutionResult",
    "RootStatus",
    "disambiguated_labels",
    "normalize_token",
    "validate_child_name",
]
