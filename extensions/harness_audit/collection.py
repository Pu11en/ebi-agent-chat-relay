"""Records shared by the Claude and Codex collectors (tasks 2.2 and 2.3).

A collector's job is to turn *what exists* (discovery) plus *how the relay
launches the CLI* (an invocation record) plus *what earlier sessions left
behind* (transcript or rollout metadata) into an inventory whose evidence
levels are earned.  The two harnesses differ in where they read and what
their session files prove, but they share the shape of the answer:

* :class:`Invocation` — the argv, cwd and environment *names* of the
  Discord-launched CLI.  It is a record handed in, never ``sys.argv`` or
  ``os.environ``, so the audit can run against a fixture and never has to
  spawn the CLI to learn how it was spawned.
* :class:`SessionObservation` — the metadata a transcript or rollout carries
  (cwd, version, instructions hash, skill names seen).  Never a message.
* :class:`CollectionResult` — the inventory plus the side channels the later
  stages need: private bodies, redacted environment, content signals and the
  settings facts per item, and the notes explaining what could not be proven.

Nothing here reads a file or runs a process.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Self

from extensions.harness_audit.discovery import ContentSignals, DiscoveredFile, SettingsFacts
from extensions.harness_audit.models import (
    CoverageGap,
    EvidenceLevel,
    EvidenceRecord,
    Harness,
    HarnessInventory,
    SchemaError,
)
from extensions.harness_audit.redaction import PrivateBody


class CollectionError(SchemaError):
    """An invocation or session record is malformed."""


@dataclass(frozen=True, slots=True)
class Invocation:
    """How the relay launched the CLI: argv after the executable, cwd, env names."""

    argv: tuple[str, ...]
    cwd: str
    environment_names: tuple[str, ...] = ()
    cli_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(str(part) for part in self.argv))
        object.__setattr__(self, "environment_names", tuple(sorted(set(self.environment_names))))
        if not self.cwd.strip():
            raise CollectionError("invocation: cwd must not be empty")

    def option(self, *names: str) -> str | None:
        """The value of ``--name value`` or ``--name=value``; the last one wins."""
        values = self.options(*names)
        return values[-1] if values else None

    def options(self, *names: str) -> tuple[str, ...]:
        """Every value of a repeatable option, in order."""
        values: list[str] = []
        for index, part in enumerate(self.argv):
            if part == "--":
                break
            for name in names:
                if part == name and index + 1 < len(self.argv):
                    values.append(self.argv[index + 1])
                elif part.startswith(f"{name}="):
                    values.append(part[len(name) + 1 :])
        return tuple(values)

    def has_flag(self, *names: str) -> bool:
        for part in self.argv:
            if part == "--":
                break
            if part in names:
                return True
        return False

    @classmethod
    def from_dict(cls, payload: Mapping[str, object], *, harness: Harness | None = None) -> Self:
        where = "invocation"
        declared = payload.get("harness")
        if harness is not None and declared is not None and str(declared) != harness.value:
            raise CollectionError(f"{where}: record is for {declared!r}, not {harness.value!r}")
        argv = payload.get("argv")
        if not isinstance(argv, Sequence) or isinstance(argv, str):
            raise CollectionError(f"{where}: argv must be a list of strings")
        cwd = payload.get("cwd")
        if not isinstance(cwd, str):
            raise CollectionError(f"{where}: cwd must be a string")
        names = payload.get("environment_names", ())
        if isinstance(names, str) or not isinstance(names, Sequence):
            raise CollectionError(f"{where}: environment_names must be a list")
        version = payload.get("cli_version", "")
        return cls(
            argv=tuple(str(part) for part in argv),
            cwd=cwd,
            environment_names=tuple(str(name) for name in names),
            cli_version=str(version or ""),
        )

    @classmethod
    def load(cls, path: Path, *, harness: Harness | None = None) -> Self:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CollectionError(f"invocation: {path} is unreadable: {error}") from error
        if not isinstance(payload, Mapping):
            raise CollectionError("invocation: record must be an object")
        return cls.from_dict(payload, harness=harness)


@dataclass(frozen=True, slots=True)
class SessionObservation:
    """Metadata from one transcript or rollout.  Never a message body."""

    harness: Harness
    path: str
    cwd: str
    version: str
    instructions_hash: str = ""
    skill_names: tuple[str, ...] = ()
    parse_error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "harness": self.harness.value,
            "path": self.path,
            "cwd": self.cwd,
            "version": self.version,
            "instructions_hash": self.instructions_hash,
            "skill_names": list(self.skill_names),
            "parse_error": self.parse_error,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        return cls(
            harness=Harness(str(payload.get("harness"))),
            path=str(payload.get("path", "")),
            cwd=str(payload.get("cwd", "")),
            version=str(payload.get("version", "")),
            instructions_hash=str(payload.get("instructions_hash", "")),
            skill_names=tuple(str(name) for name in payload.get("skill_names", ())),  # type: ignore[union-attr]
            parse_error=str(payload.get("parse_error", "")),
        )


def same_directory(left: str, right: str) -> bool:
    """Two cwd strings name the same directory (separators and case aside on Windows)."""
    return _norm(left) == _norm(right)


def _norm(text: str) -> str:
    normalized = text.replace("\\", "/").rstrip("/")
    return normalized.lower() if len(normalized) > 1 and normalized[1] == ":" else normalized


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """One target's inventory and the side channels later stages need."""

    inventory: HarnessInventory
    private_bodies: tuple[PrivateBody, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    signals: Mapping[str, ContentSignals] = field(default_factory=dict)
    settings: Mapping[str, SettingsFacts] = field(default_factory=dict)
    files: Mapping[str, DiscoveredFile] = field(default_factory=dict)
    sessions: tuple[SessionObservation, ...] = ()
    coverage_gap: CoverageGap | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "signals", MappingProxyType(dict(self.signals)))
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))

    def canonical_hash(self, resolved_path: str) -> str:
        """The hash of the file a display path resolves to, or ``""``."""
        for entry in self.files.values():
            if entry.resolved_path == resolved_path or entry.path == resolved_path:
                return entry.content_hash
        return ""


def unknown(method: str, detail: str, *missing: str, reference: str = "") -> EvidenceRecord:
    return EvidenceRecord(
        level=EvidenceLevel.UNKNOWN,
        method=method,
        detail=detail,
        source_reference=reference,
        missing_evidence=tuple(missing) or ("evidence not named",),
    )


def evidence(
    level: EvidenceLevel,
    method: str,
    detail: str,
    *,
    reference: str = "",
    vendor: bool = False,
) -> EvidenceRecord:
    return EvidenceRecord(
        level=level,
        method=method,
        detail=detail,
        source_reference=reference,
        depends_on_vendor_behavior=vendor,
    )
