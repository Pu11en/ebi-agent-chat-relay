"""Reversible quarantine with a manifest, never deletion (task 4.2).

This is the one place the audit is allowed to write inside a harness
directory, and every write is paired with the record that undoes it:

* :func:`plan_quarantine` turns selected verdicts into a
  :class:`QuarantineManifest` — original path, sha256 of the bytes, the
  disabled location under the quarantine directory, the verdict, the affected
  targets and the rollback action.  Planning writes nothing.  Items that are
  not a whole file (a hook, an MCP server, a setting key) are listed as
  *skipped* with the reason: they are edited under version control instead.
* :func:`apply_quarantine` verifies every hash first and aborts on the first
  mismatch **before** touching a file; then it moves each file into the
  quarantine directory.  If a move fails midway the moved files are put back.
  Nothing is deleted, ever.
* :func:`rollback_quarantine` moves the bytes back, verifying the quarantined
  copy's hash and refusing to overwrite an original that reappeared.
* :func:`removal_eligibility` is the gate before any permanent removal: only a
  ``Remove`` verdict, applied and not rolled back, with **every** affected
  target verified passing.  A target that was not verified blocks it.

The manifest stays on the machine it describes: it carries absolute paths,
which are local facts, and it is not part of the redacted bundle.

The manifest is data, not authority.  It is a JSON file anyone can edit, so
the paths it names are checked against what the operator actually approved
before a single byte moves: every ``original_path`` must resolve inside the
discovery roots given on the command line, every ``disabled_location`` must
resolve inside ``<quarantine_dir>/<manifest_id>``, and the manifest id must be
one plain path segment.  Planning applies the same rule to the disabled
location it derives from a bundle's display path — a ``..`` there would plan
a move to anywhere on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Self

from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.discovery import DiscoveryRoots
from extensions.harness_audit.models import (
    AuditTarget,
    CheckOutcome,
    Classification,
    ClassificationRecord,
    EvidenceLevel,
    EvidenceRecord,
    Finding,
    Machine,
    SchemaError,
    Severity,
)

MANIFEST_SCHEMA_VERSION = 1
DEFAULT_VERDICTS: frozenset[Classification] = frozenset(
    {Classification.REMOVE, Classification.MOVE_TO_PROJECT, Classification.LOAD_ON_DEMAND}
)


class QuarantineError(SchemaError):
    """The manifest cannot be applied or rolled back safely; nothing was changed."""


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Path boundaries
# --------------------------------------------------------------------------- #

_MANIFEST_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _is_within(path: Path, root: Path) -> bool:
    """True when *path* resolves to *root* or somewhere beneath it."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _is_audited(path: Path, roots: DiscoveryRoots) -> bool:
    resolved = Path(path).resolve()
    if any(resolved == approved.resolve() for approved in roots.approved_files):
        return True
    return any(_is_within(resolved, root) for root in roots.approved_roots)


def manifest_base(manifest: QuarantineManifest) -> Path:
    """``<quarantine_dir>/<manifest_id>`` — the only place a quarantined copy may live."""
    if manifest.manifest_id in {".", ".."} or not _MANIFEST_ID.match(manifest.manifest_id):
        raise QuarantineError(
            f"manifest_id {manifest.manifest_id!r} is not a plain path segment; refusing"
        )
    return Path(manifest.quarantine_dir) / manifest.manifest_id


def entry_boundary_problems(
    manifest: QuarantineManifest, entry: QuarantineEntry, roots: DiscoveryRoots
) -> list[str]:
    """Why this entry's paths may not be moved, or an empty list."""
    problems: list[str] = []
    base = manifest_base(manifest)
    if not _is_audited(Path(entry.original_path), roots):
        problems.append(
            f"{entry.display_path}: original path {entry.original_path} is outside the"
            " audited roots"
        )
    if not _is_within(Path(entry.disabled_location), base):
        problems.append(
            f"{entry.display_path}: disabled location {entry.disabled_location} is outside"
            f" the quarantine directory {base}"
        )
    return problems


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    item_id: str
    original_path: str
    display_path: str
    content_hash: str
    byte_size: int
    disabled_location: str
    classification: Classification
    affected_targets: tuple[AuditTarget, ...]
    rollback_action: str
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None
    also_item_ids: tuple[str, ...] = ()
    """Other items backed by the same file (a skill both harnesses read)."""

    @property
    def is_active(self) -> bool:
        return self.applied_at is not None and self.rolled_back_at is None

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "original_path": self.original_path,
            "display_path": self.display_path,
            "content_hash": self.content_hash,
            "byte_size": self.byte_size,
            "disabled_location": self.disabled_location,
            "classification": self.classification.value,
            "affected_targets": [t.to_dict() for t in self.affected_targets],
            "rollback_action": self.rollback_action,
            "applied_at": None if self.applied_at is None else self.applied_at.isoformat(),
            "rolled_back_at": None
            if self.rolled_back_at is None
            else self.rolled_back_at.isoformat(),
            "also_item_ids": list(self.also_item_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        return cls(
            also_item_ids=tuple(str(i) for i in payload.get("also_item_ids", ())),  # type: ignore[union-attr]
            item_id=str(payload["item_id"]),
            original_path=str(payload["original_path"]),
            display_path=str(payload["display_path"]),
            content_hash=str(payload["content_hash"]),
            byte_size=int(payload["byte_size"]),  # type: ignore[call-overload]
            disabled_location=str(payload["disabled_location"]),
            classification=Classification(str(payload["classification"])),
            affected_targets=tuple(
                AuditTarget.from_dict(t)  # type: ignore[arg-type]
                for t in payload.get("affected_targets", ())  # type: ignore[union-attr]
            ),
            rollback_action=str(payload["rollback_action"]),
            applied_at=_optional_datetime(payload.get("applied_at")),
            rolled_back_at=_optional_datetime(payload.get("rolled_back_at")),
        )


@dataclass(frozen=True, slots=True)
class SkippedItem:
    item_id: str
    classification: Classification
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "classification": self.classification.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class QuarantineManifest:
    manifest_id: str
    machine: Machine
    created_at: datetime
    quarantine_dir: str
    entries: tuple[QuarantineEntry, ...]
    skipped: tuple[SkippedItem, ...] = ()
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "machine": self.machine.value,
            "created_at": self.created_at.isoformat(),
            "quarantine_dir": self.quarantine_dir,
            "entries": [e.to_dict() for e in self.entries],
            "skipped": [s.to_dict() for s in self.skipped],
            "applied_at": None if self.applied_at is None else self.applied_at.isoformat(),
            "rolled_back_at": None
            if self.rolled_back_at is None
            else self.rolled_back_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise QuarantineError(
                f"manifest: schema_version {payload.get('schema_version')!r} is not supported"
            )
        try:
            return cls(
                manifest_id=str(payload["manifest_id"]),
                machine=Machine(str(payload["machine"])),
                created_at=datetime.fromisoformat(str(payload["created_at"])),
                quarantine_dir=str(payload["quarantine_dir"]),
                entries=tuple(
                    QuarantineEntry.from_dict(e)  # type: ignore[arg-type]
                    for e in payload.get("entries", ())  # type: ignore[union-attr]
                ),
                skipped=tuple(
                    SkippedItem(
                        str(s["item_id"]),  # type: ignore[index]
                        Classification(str(s["classification"])),  # type: ignore[index]
                        str(s["reason"]),  # type: ignore[index]
                    )
                    for s in payload.get("skipped", ())  # type: ignore[union-attr]
                ),
                applied_at=_optional_datetime(payload.get("applied_at")),
                rolled_back_at=_optional_datetime(payload.get("rolled_back_at")),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise QuarantineError(f"manifest: malformed record: {error}") from error

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> Self:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise QuarantineError(f"manifest: {path} is unreadable: {error}") from error
        if not isinstance(payload, Mapping):
            raise QuarantineError("manifest: record must be an object")
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class RemovalVerdict:
    item_id: str
    eligible: bool
    reason: str
    rollback_reference: str

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "eligible": self.eligible,
            "reason": self.reason,
            "rollback_reference": self.rollback_reference,
        }


@dataclass(frozen=True, slots=True)
class TargetVerdict:
    target: AuditTarget
    passed: bool
    blocking: tuple[Finding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target.key,
            "passed": self.passed,
            "blocking": [f.finding_id for f in self.blocking],
        }


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def absolute_paths(results: Iterable[CollectionResult], roots: DiscoveryRoots) -> dict[str, Path]:
    """Expand each file-backed item's ``~``-relative display path on this machine."""
    paths: dict[str, Path] = {}
    for result in results:
        for item_id, entry in result.files.items():
            if "#" in item_id.rsplit("/", 1)[-1] and item_id.rsplit("#", 1)[-1]:
                continue
            display = entry.path
            if display.startswith("~/"):
                paths[item_id] = roots.home / display[2:]
            else:
                paths[item_id] = Path(display)
    return paths


def plan_quarantine(
    classifications: Iterable[ClassificationRecord],
    paths: Mapping[str, Path],
    *,
    quarantine_dir: Path,
    machine: Machine,
    created_at: datetime,
    verdicts: frozenset[Classification] = DEFAULT_VERDICTS,
    manifest_id: str | None = None,
) -> QuarantineManifest:
    """Describe what would be quarantined.  Reads hashes; writes nothing."""
    manifest_id = manifest_id or f"q-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    base = Path(quarantine_dir) / manifest_id
    entries: list[QuarantineEntry] = []
    skipped: list[SkippedItem] = []
    by_path: dict[str, int] = {}
    for record in sorted(classifications, key=lambda r: r.item_id):
        if record.classification not in verdicts:
            continue
        path = paths.get(record.item_id)
        if path is None or "#" in record.item_id.rsplit("/", 1)[-1]:
            skipped.append(
                SkippedItem(
                    record.item_id,
                    record.classification,
                    "not a whole file; edit the containing file under version control",
                )
            )
            continue
        path = Path(path)
        if not path.is_file():
            skipped.append(
                SkippedItem(record.item_id, record.classification, f"{path} is not a regular file")
            )
            continue
        key = str(path.resolve())
        if key in by_path:
            # One file, two harnesses: a single move covers both items.
            index = by_path[key]
            existing = entries[index]
            entries[index] = replace(
                existing,
                also_item_ids=(*existing.also_item_ids, record.item_id),
                affected_targets=tuple(
                    sorted(
                        set(existing.affected_targets) | set(record.affected_targets),
                        key=lambda t: t.key,
                    )
                ),
            )
            continue
        by_path[key] = len(entries)
        display = record.evidence[0].source_reference or path.as_posix()
        relative = display[2:] if display.startswith("~/") else path.name
        disabled = base / relative
        if not _is_within(disabled, base):
            raise QuarantineError(
                f"{record.item_id}: display path {display!r} would place the quarantined copy"
                f" outside the quarantine directory {base}; refusing to plan"
            )
        entries.append(
            QuarantineEntry(
                item_id=record.item_id,
                original_path=str(path),
                display_path=display,
                content_hash=file_hash(path),
                byte_size=path.stat().st_size,
                disabled_location=str(disabled),
                classification=record.classification,
                affected_targets=record.affected_targets,
                rollback_action=(
                    f"move {disabled.as_posix()} back to {path.as_posix()} and verify"
                    f" {file_hash(path)}"
                ),
            )
        )
    return QuarantineManifest(
        manifest_id=manifest_id,
        machine=machine,
        created_at=created_at,
        quarantine_dir=str(quarantine_dir),
        entries=tuple(entries),
        skipped=tuple(skipped),
    )


# --------------------------------------------------------------------------- #
# Apply and rollback
# --------------------------------------------------------------------------- #


def apply_quarantine(
    manifest: QuarantineManifest, *, now: datetime, roots: DiscoveryRoots
) -> QuarantineManifest:
    """Verify every path boundary and hash, then move every file.

    Aborts before the first move.  *roots* is what the operator approved on
    the command line; the manifest's own paths are checked against it, never
    trusted.
    """
    if manifest.applied_at is not None and manifest.rolled_back_at is None:
        raise QuarantineError(f"manifest {manifest.manifest_id} is already applied")
    problems: list[str] = []
    for entry in manifest.entries:
        problems.extend(entry_boundary_problems(manifest, entry, roots))
    if problems:
        raise QuarantineError("refusing to apply; nothing was moved: " + "; ".join(problems))
    for entry in manifest.entries:
        original = Path(entry.original_path)
        if not original.is_file():
            problems.append(f"{entry.display_path}: original file is missing")
            continue
        actual = file_hash(original)
        if actual != entry.content_hash:
            problems.append(
                f"{entry.display_path}: hash mismatch (manifest {entry.content_hash[:19]}…,"
                f" on disk {actual[:19]}…)"
            )
        if Path(entry.disabled_location).exists():
            problems.append(f"{entry.display_path}: disabled location already exists")
    if problems:
        raise QuarantineError("refusing to apply; nothing was moved: " + "; ".join(problems))

    moved: list[QuarantineEntry] = []
    try:
        for entry in manifest.entries:
            destination = Path(entry.disabled_location)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(entry.original_path, destination)
            moved.append(entry)
    except OSError as error:
        for entry in reversed(moved):
            shutil.move(entry.disabled_location, entry.original_path)
        raise QuarantineError(f"move failed and was undone: {error}") from error

    return replace(
        manifest,
        entries=tuple(
            replace(entry, applied_at=now, rolled_back_at=None) for entry in manifest.entries
        ),
        applied_at=now,
        rolled_back_at=None,
    )


def rollback_quarantine(
    manifest: QuarantineManifest, *, now: datetime, roots: DiscoveryRoots
) -> QuarantineManifest:
    """Put every quarantined file back where it was, verifying its hash.

    The same boundaries as :func:`apply_quarantine`: a copy may only come
    from ``<quarantine_dir>/<manifest_id>`` and may only go back inside the
    audited roots.
    """
    if manifest.applied_at is None or manifest.rolled_back_at is not None:
        raise QuarantineError(f"manifest {manifest.manifest_id} is not currently applied")
    problems: list[str] = []
    for entry in manifest.entries:
        problems.extend(entry_boundary_problems(manifest, entry, roots))
    if problems:
        raise QuarantineError("refusing to roll back; nothing was moved: " + "; ".join(problems))
    for entry in manifest.entries:
        disabled = Path(entry.disabled_location)
        if not disabled.is_file():
            problems.append(f"{entry.display_path}: quarantined copy is missing")
            continue
        if file_hash(disabled) != entry.content_hash:
            problems.append(f"{entry.display_path}: quarantined copy hash mismatch")
        if Path(entry.original_path).exists():
            problems.append(f"{entry.display_path}: a file exists at the original path")
    if problems:
        raise QuarantineError("refusing to roll back; nothing was moved: " + "; ".join(problems))

    restored: list[QuarantineEntry] = []
    try:
        for entry in manifest.entries:
            Path(entry.original_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(entry.disabled_location, entry.original_path)
            restored.append(entry)
    except OSError as error:
        for entry in reversed(restored):
            shutil.move(entry.original_path, entry.disabled_location)
        raise QuarantineError(f"rollback failed and was undone: {error}") from error
    _prune_empty(manifest_base(manifest))

    return replace(
        manifest,
        entries=tuple(replace(entry, rolled_back_at=now) for entry in manifest.entries),
        rolled_back_at=now,
    )


def _prune_empty(root: Path) -> None:
    """Remove now-empty quarantine directories (never a file)."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


# --------------------------------------------------------------------------- #
# Verification and eligibility
# --------------------------------------------------------------------------- #


def target_passes(findings: Sequence[Finding], target: AuditTarget) -> TargetVerdict:
    """A target passes when nothing HIGH fails and nothing is unknown on it."""
    blocking = tuple(
        f
        for f in findings
        if target in f.targets
        and (
            (f.outcome is CheckOutcome.FAIL and f.severity is Severity.HIGH)
            or f.outcome is CheckOutcome.UNKNOWN
        )
    )
    return TargetVerdict(target=target, passed=not blocking, blocking=blocking)


def removal_eligibility(
    manifest: QuarantineManifest, verified: Mapping[AuditTarget, bool]
) -> tuple[RemovalVerdict, ...]:
    """Remove is eligible only after quarantine and every affected target passing."""
    verdicts: list[RemovalVerdict] = []
    for entry in manifest.entries:
        reason = ""
        if entry.classification is not Classification.REMOVE:
            reason = f"not classified Remove ({entry.classification.value})"
        elif not entry.is_active:
            reason = (
                "quarantine not applied"
                if entry.applied_at is None
                else "quarantine was rolled back; the item proved necessary"
            )
        else:
            unverified = [t.key for t in entry.affected_targets if t not in verified]
            failed = [t.key for t in entry.affected_targets if verified.get(t) is False]
            if unverified:
                reason = (
                    f"affected target(s) not verified after quarantine: {', '.join(unverified)}"
                )
            elif failed:
                reason = f"affected target(s) failed their checks: {', '.join(failed)}"
        verdicts.append(
            RemovalVerdict(
                item_id=entry.item_id,
                eligible=not reason,
                reason=reason or "quarantined and every affected target passed its checks",
                rollback_reference=manifest.manifest_id,
            )
        )
    return tuple(verdicts)


def reclassify_after_rollback(record: ClassificationRecord, why: str) -> ClassificationRecord:
    """A quarantined item that proved necessary is kept, with the reason recorded."""
    return ClassificationRecord(
        item_id=record.item_id,
        classification=Classification.KEEP,
        reason=f"restored after quarantine: {why}",
        evidence=(
            *record.evidence,
            EvidenceRecord(
                level=EvidenceLevel.LOADED,
                method="post-quarantine-check",
                detail=why,
                source_reference=record.evidence[0].source_reference or record.item_id,
            ),
        ),
        affected_targets=record.affected_targets,
        proposed_scope=record.proposed_scope,
        risk="none; the item is back in place",
        reversible_action="none required; the rollback already restored it",
        finding_ids=record.finding_ids,
    )


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))
