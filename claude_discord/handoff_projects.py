"""Resolve a handoff's ``ProjectLocator`` inside this computer's approved roots.

The handoff protocol carries ``owner`` plus a relative ``folder``; it never
carries a path, because another computer's paths mean nothing here. This
module is the boundary where a locator becomes a real directory, and it is
deliberately narrow: an owner has a small set of configured roots, a folder
must exist under one of them, and the resolved real path must still be under
that root after symlinks are followed. Anything else is refused.

The shared project catalog is a separate build. When it lands it implements
:class:`ProjectResolver` — the protocol here — and nothing in the handoff
code changes. Until then :func:`build_project_resolver` reads the approved
roots from ``CCDB_HANDOFF_PROJECT_ROOTS`` (``owner=path`` entries) and pins
the one locator the narrow project-lookup slice already uses,
``drew/main-projects``, to the lookup root that slice has always resolved.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from claude_code_core.handoffs.protocol import HandoffProtocolError, ProjectLocator

logger = logging.getLogger(__name__)

ENV_OWNER_ROOTS = "CCDB_HANDOFF_PROJECT_ROOTS"
ENV_LOOKUP_ROOT = "CCDB_PROJECT_LOOKUP_ROOT"
ENV_PROJECT_ROOTS = "CCDB_PROJECT_ROOTS"
DEFAULT_LOOKUP_ROOT = "/home/drewp/main-projects"

DEFAULT_LOOKUP_LOCATOR = ProjectLocator(owner="drew", folder="main-projects")


class ProjectResolutionError(ValueError):
    """The locator names nothing this computer may open.

    The message is written for the peer and for Discord: it names the
    locator's labels (owner, folder) and never a local path. The paths that
    were tried go to the log, where only this computer's operator reads them.
    """


@dataclass(frozen=True)
class ResolvedProject:
    """A locator that became a real directory under an approved root."""

    locator: ProjectLocator
    path: Path
    root: Path


class ProjectResolver(Protocol):
    """What the executor needs: a locator in, a verified directory out."""

    def resolve(self, locator: ProjectLocator) -> ResolvedProject: ...


def _split_entries(raw: str) -> list[str]:
    return [item.strip().strip("'\"") for item in raw.replace(";", ",").split(",") if item.strip()]


def parse_owner_roots(raw: str) -> dict[str, tuple[Path, ...]]:
    """Parse ``owner=path`` entries; an owner may appear more than once."""
    roots: dict[str, list[Path]] = {}
    for entry in _split_entries(raw):
        owner_raw, sep, path_raw = entry.partition("=")
        if not sep or not path_raw.strip():
            raise ValueError(f"{ENV_OWNER_ROOTS} entry {entry!r} needs the form owner=path")
        try:
            owner = ProjectLocator(owner=owner_raw, folder="x").owner
        except HandoffProtocolError as exc:
            raise ValueError(f"{ENV_OWNER_ROOTS}: {exc}") from exc
        roots.setdefault(owner, []).append(Path(path_raw.strip()).expanduser())
    return {owner: tuple(paths) for owner, paths in roots.items()}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ApprovedRootResolver:
    """The configured-roots adapter of :class:`ProjectResolver`."""

    roots: Mapping[str, tuple[Path, ...]]
    pinned: Mapping[ProjectLocator, Path] = field(default_factory=dict)

    def resolve(self, locator: ProjectLocator) -> ResolvedProject:
        if not isinstance(locator, ProjectLocator):
            raise ProjectResolutionError("only a validated ProjectLocator can be resolved")
        pinned = self.pinned.get(locator)
        if pinned is not None:
            return self._check(locator, pinned.expanduser(), pinned.expanduser())
        roots = self.roots.get(locator.owner, ())
        if not roots:
            raise ProjectResolutionError(
                f"no approved project root is configured for owner {locator.owner!r}"
            )
        missing: list[str] = []
        for root in roots:
            candidate = root.expanduser() / locator.folder
            if not candidate.exists():
                missing.append(str(candidate))
                continue
            return self._check(locator, candidate, root.expanduser())
        logger.info(
            "handoff locator %s/%s: no such folder under the approved roots (looked in %s)",
            locator.owner,
            locator.folder,
            ", ".join(missing),
        )
        raise ProjectResolutionError(
            f"folder {locator.folder!r} does not exist under any approved root of {locator.owner!r}"
        )

    @staticmethod
    def _check(locator: ProjectLocator, candidate: Path, root: Path) -> ResolvedProject:
        label = f"{locator.owner}/{locator.folder}"
        if not candidate.exists():
            logger.info("handoff locator %s: %s does not exist", label, candidate)
            raise ProjectResolutionError(
                f"folder {locator.folder!r} does not exist under the approved root of "
                f"{locator.owner!r}"
            )
        real = candidate.resolve()
        real_root = root.resolve()
        if not real.is_dir():
            logger.info("handoff locator %s: %s is not a directory", label, candidate)
            raise ProjectResolutionError(f"project locator {label!r} is not a directory")
        if not _is_within(real, real_root):
            logger.warning(
                "handoff locator %s: %s resolves to %s, outside approved root %s",
                label,
                candidate,
                real,
                real_root,
            )
            raise ProjectResolutionError(
                f"project folder {locator.folder!r} resolves outside its approved root"
            )
        return ResolvedProject(locator=locator, path=real, root=real_root)


def _first_project_root() -> str | None:
    entries = _split_entries(os.getenv(ENV_PROJECT_ROOTS, ""))
    return entries[0] if entries else None


def lookup_root(*, configured: str | None = None, fallback: str | None = None) -> str:
    """The directory the narrow project-lookup slice may inspect.

    Precedence: an explicit ``configured`` value, ``CCDB_PROJECT_LOOKUP_ROOT``,
    the first ``CCDB_PROJECT_ROOTS`` entry, the caller's ``fallback`` (the
    runner's working directory), then the historical default. Every caller —
    the Discord envelope path and the REST endpoint alike — resolves through
    here, so configuration wins over whichever process happened to receive
    the request.
    """
    root = (
        configured
        or os.getenv(ENV_LOOKUP_ROOT, "").strip()
        or _first_project_root()
        or fallback
        or DEFAULT_LOOKUP_ROOT
    )
    path = Path(root).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"project lookup root is not a directory: {path}")
    return str(path)


def build_project_resolver(
    *,
    env: Mapping[str, str] | None = None,
    fallback_lookup_root: str | None = None,
) -> ApprovedRootResolver:
    """The resolver an instance runs with, from its environment.

    Owner roots come from ``CCDB_HANDOFF_PROJECT_ROOTS``. The default lookup
    locator is pinned to :func:`lookup_root` when that resolves, so the
    project-lookup use case keeps working with no new configuration.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    roots = parse_owner_roots(source.get(ENV_OWNER_ROOTS, ""))
    pinned: dict[ProjectLocator, Path] = {}
    with contextlib.suppress(ValueError):  # no usable lookup root: owner roots only
        pinned[DEFAULT_LOOKUP_LOCATOR] = Path(lookup_root(fallback=fallback_lookup_root))
    return ApprovedRootResolver(roots=roots, pinned=pinned)


__all__ = [
    "DEFAULT_LOOKUP_LOCATOR",
    "DEFAULT_LOOKUP_ROOT",
    "ENV_LOOKUP_ROOT",
    "ENV_OWNER_ROOTS",
    "ENV_PROJECT_ROOTS",
    "ApprovedRootResolver",
    "ProjectResolutionError",
    "ProjectResolver",
    "ResolvedProject",
    "build_project_resolver",
    "lookup_root",
    "parse_owner_roots",
]
