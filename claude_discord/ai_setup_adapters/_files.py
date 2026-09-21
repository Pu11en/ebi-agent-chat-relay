"""Declared source roots and the one way an adapter may look at a file.

Every adapter in this package reads from a :class:`SetupRoot` it was handed —
a fixture home in tests, the real ``~/.claude`` in production — and never from
anywhere else.  :func:`read_file_facts` is the single door through which file
content passes, and what comes out is a fingerprint, a size and a modification
time: the content itself is dropped before the function returns.

Nothing here writes.  A root that is missing, a file that cannot be read, or a
symlink that points outside the root becomes a :class:`SourceError` for the
adapter to report as a diagnostic, not an exception that aborts the run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from claude_discord.ai_setup_collector import CollectionContext
from claude_discord.ai_setup_inventory import (
    ContentFingerprint,
    EffectiveScope,
    InventorySource,
    Measurement,
    OwnershipClass,
    normalize_token,
    safe_text,
)
from claude_discord.ai_setup_redaction import safe_fingerprint, safe_locator

#: Bytes above which a file is fingerprinted and measured but never decoded.
MAX_DECODED_BYTES = 4 * 1024 * 1024
#: The rough "four characters per token" rule; always labelled as an estimate.
ESTIMATED_CHARS_PER_TOKEN = 4


class HarnessLayout(StrEnum):
    """Which directory convention a root follows."""

    CLAUDE_HOME = "claude_home"
    CODEX_HOME = "codex_home"
    PROJECT = "project"
    DSH_CONFIG = "dsh_config"
    COGS_DIR = "cogs_dir"


class SourceError(Exception):
    """A file or root the adapter could not summarize; carries safe text only."""


@dataclass(frozen=True, slots=True)
class SetupRoot:
    """One declared boundary an adapter may read.

    ``ownership`` is the internal class the collector reasons with; the user
    only ever sees the :class:`EffectiveScope` :meth:`scope_for` derives from
    it.  ``home`` is the directory shown as ``~`` in locators.  ``companions``
    are the few files that belong to this boundary but sit outside its
    directory — ``~/.claude.json`` next to ``~/.claude`` — named one by one so
    the boundary stays explicit.
    """

    key: str
    path: Path
    layout: HarnessLayout
    ownership: OwnershipClass
    label: str
    harness: str | None = None
    owner: str | None = None
    project: str | None = None
    home: Path | None = None
    companions: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", normalize_token(self.key, kind="source key"))
        object.__setattr__(self, "label", safe_text(self.label, kind="source label"))
        object.__setattr__(self, "path", Path(self.path))
        if self.harness is not None:
            object.__setattr__(self, "harness", normalize_token(self.harness, kind="harness"))
        if self.owner is not None:
            object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        if self.project is not None:
            object.__setattr__(self, "project", safe_text(self.project, kind="project name"))
        if self.home is not None:
            object.__setattr__(self, "home", Path(self.home))
        object.__setattr__(self, "companions", tuple(Path(entry) for entry in self.companions))

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    def contains(self, path: Path) -> bool:
        """True when ``path`` resolves beneath this root or to a declared companion."""
        try:
            resolved = path.resolve()
            if any(resolved == companion.resolve() for companion in self.companions):
                return True
            resolved.relative_to(self.path.resolve())
        except (ValueError, OSError):
            return False
        return True

    def locator_for(self, path: Path) -> str:
        """A safe, ``~``-collapsed locator; never a credential or a query."""
        home = self.home.as_posix() if self.home is not None else None
        return safe_locator(path.as_posix(), home=home)

    def source_for(
        self,
        path: Path,
        context: CollectionContext,
        *,
        modified_at: datetime | None = None,
    ) -> InventorySource:
        return InventorySource(
            key=self.key,
            computer=context.computer,
            label=self.label,
            locator=self.locator_for(path),
            harness=self.harness,
            modified_at=modified_at,
        )

    def scope_for(
        self, context: CollectionContext, *, ownership: OwnershipClass | None = None
    ) -> EffectiveScope:
        """The user-facing scope; ``ownership`` overrides the root's for one file."""
        return EffectiveScope.for_ownership(
            ownership or self.ownership,
            owner=self.owner or context.owner,
            computer=context.computer,
            project=self.project,
            primary_owner=context.primary_owner or context.owner,
        )

    def missing_message(self) -> str:
        locator = self.locator_for(self.path)
        return f"The {self.label} root is not present on this computer ({locator})"


def claude_home_root(
    path: Path,
    *,
    owner: str,
    key: str = "claude-home",
    home: Path | None = None,
    label: str = "Claude home",
) -> SetupRoot:
    """``~/.claude`` — shared across every computer that signs in as ``owner``.

    Claude Code keeps its user-level config (MCP servers, OAuth account) in
    ``~/.claude.json`` *beside* the home directory, so that one file is the
    root's declared companion.
    """
    path = Path(path)
    user_config = (Path(home) if home is not None else path.parent) / ".claude.json"
    return SetupRoot(
        key=key,
        path=path,
        layout=HarnessLayout.CLAUDE_HOME,
        ownership=OwnershipClass.MEGA_GLOBAL,
        label=label,
        harness="claude",
        owner=owner,
        home=home,
        companions=(user_config,),
    )


def codex_home_root(
    path: Path,
    *,
    owner: str,
    key: str = "codex-home",
    home: Path | None = None,
    label: str = "Codex home",
) -> SetupRoot:
    """``~/.codex`` (or the ``CODEX_HOME`` ccdb owns)."""
    return SetupRoot(
        key=key,
        path=path,
        layout=HarnessLayout.CODEX_HOME,
        ownership=OwnershipClass.MEGA_GLOBAL,
        label=label,
        harness="codex",
        owner=owner,
        home=home,
    )


def project_root(
    path: Path,
    *,
    name: str,
    key: str | None = None,
    home: Path | None = None,
) -> SetupRoot:
    """One project's configuration boundary."""
    return SetupRoot(
        key=key or f"project-{normalize_token(name, kind='project name')}",
        path=path,
        layout=HarnessLayout.PROJECT,
        ownership=OwnershipClass.PROJECT,
        label=f"Project {name}",
        project=name,
        home=home,
    )


def dsh_config_root(
    path: Path,
    *,
    owner: str,
    key: str = "dsh-config",
    home: Path | None = None,
    label: str = "DSH configuration",
) -> SetupRoot:
    """``~/.config/ccdb/dsh`` — the DeepSeek Harness composition overrides."""
    return SetupRoot(
        key=key,
        path=path,
        layout=HarnessLayout.DSH_CONFIG,
        ownership=OwnershipClass.MACHINE,
        label=label,
        harness="dsh",
        owner=owner,
        home=home,
    )


def cogs_dir_root(
    path: Path,
    *,
    key: str = "custom-cogs",
    home: Path | None = None,
    label: str = "Custom Cogs",
) -> SetupRoot:
    """The ``CUSTOM_COGS_DIR`` this bot loads — one computer's Discord extensions."""
    return SetupRoot(
        key=key,
        path=path,
        layout=HarnessLayout.COGS_DIR,
        ownership=OwnershipClass.MACHINE,
        label=label,
        harness="ccdb",
        home=home,
    )


@dataclass(frozen=True, slots=True)
class FileFacts:
    """What an adapter is allowed to keep about a file's content."""

    fingerprint: ContentFingerprint
    measurement: Measurement
    modified_at: datetime


def modified_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def read_file_facts(root: SetupRoot, path: Path) -> FileFacts:
    """Fingerprint, measure and date one file inside ``root``; keep no content.

    Raises :class:`SourceError` (with a safe message) when the file is not a
    regular file, escapes the root through a link, or cannot be read.
    """
    locator = root.locator_for(path)
    if not root.contains(path):
        raise SourceError(f"{locator} points outside the {root.label} root and was not read")
    if not path.is_file():
        raise SourceError(f"{locator} is not a readable file")
    try:
        data = path.read_bytes()
        modified_at = modified_time(path)
    except OSError as error:
        raise SourceError(f"{locator} could not be read ({type(error).__name__})") from error
    fingerprint = safe_fingerprint(data)
    if len(data) > MAX_DECODED_BYTES:
        measurement = Measurement(byte_size=len(data))
    else:
        characters = len(data.decode("utf-8", errors="replace"))
        measurement = Measurement.estimated(
            byte_size=len(data),
            character_count=characters,
            token_count=math.ceil(characters / ESTIMATED_CHARS_PER_TOKEN),
        )
    return FileFacts(fingerprint=fingerprint, measurement=measurement, modified_at=modified_at)


def read_text_bounded(root: SetupRoot, path: Path, *, limit: int = MAX_DECODED_BYTES) -> str:
    """Read a config file's text for *parsing*; the caller must not retain it."""
    if not root.contains(path):
        raise SourceError(
            f"{root.locator_for(path)} points outside the {root.label} root and was not read"
        )
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SourceError(
            f"{root.locator_for(path)} could not be read ({type(error).__name__})"
        ) from error
    if len(data) > limit:
        raise SourceError(f"{root.locator_for(path)} is too large to parse safely")
    return data.decode("utf-8", errors="replace")


def sorted_children(directory: Path) -> list[Path]:
    """Deterministic listing; a directory that vanished is simply empty."""
    try:
        return sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []


__all__ = [
    "ESTIMATED_CHARS_PER_TOKEN",
    "MAX_DECODED_BYTES",
    "FileFacts",
    "HarnessLayout",
    "SetupRoot",
    "SourceError",
    "claude_home_root",
    "codex_home_root",
    "cogs_dir_root",
    "dsh_config_root",
    "modified_time",
    "project_root",
    "read_file_facts",
    "read_text_bounded",
    "sorted_children",
]
