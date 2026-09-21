"""Behavioural parity across harnesses and machines (task 3.3).

Parity is *not* "are the files identical".  Claude reads ``CLAUDE.md`` and
Codex reads ``AGENTS.md``; a Claude settings file and a Codex ``config.toml``
will never match byte for byte, and they should not.  What must match is the
canonical identity of portable behaviour: the same global rules, the same
project guidance, the same skills and the same bot addition reaching every
approved target from one shared source.  Everything else is an adapter or a
machine exception, and the audit names it instead of failing it:

* **Portable identities** (``global-instructions``, ``project-instructions``,
  ``skill:<name>``, ``bot-addition``) are compared by content hash across the
  two harnesses on one machine and across the two machines for one harness.
  Equal hashes are ``aligned``; unequal or missing is ``drift`` unless a
  declared exception covers it.
* **Adapters** (``setting:<key>``, ``hook:…``, ``connector:<server>``,
  ``tool:<name>``, ``permission:…``) are compared only across machines for
  the same harness, because they are *expected* to differ between harnesses.
* **Model availability is never a parity failure.**  A different model or
  reasoning effort between machines becomes a ``model-availability`` exception
  automatically, declared or not: the audit must not recommend fabricating an
  option one subscription cannot use, and it does not tune those settings.
* **A missing target is a gap.**  Every comparison that would have involved
  it is ``unknown`` with the missing evidence named; nothing is inferred.

The hashes have to be comparable, so the machines must audit with the same
salt (``--salt`` on the CLI).  Nothing here reads a file or runs a process.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Self

from extensions.harness_audit.models import (
    APPROVED_TARGETS,
    AuditCheck,
    AuditTarget,
    CheckOutcome,
    EvidenceLevel,
    EvidenceRecord,
    ExceptionKind,
    Finding,
    Harness,
    HarnessInventory,
    InventoryItem,
    Machine,
    MachineException,
    SchemaError,
    Severity,
    SourceKind,
)

_SKILL_PATH = re.compile(r"/skills/(?P<name>[^/]+)/SKILL\.md$")
_COMMAND_PATH = re.compile(r"/(?:commands|prompts)/(?P<name>.+?)\.md$")
_MODEL_KEYS = frozenset({"setting:model", "setting:model_reasoning_effort"})
_PORTABLE_PREFIXES = ("global-instructions", "project-instructions", "skill:", "bot-addition")


class ParityError(SchemaError):
    """An overlay record is malformed."""


class ParityStatus(StrEnum):
    ALIGNED = "aligned"
    DRIFT = "drift"
    EXCEPTION = "exception"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def portable_identity(item: InventoryItem) -> str | None:
    """The harness-independent name of what an item contributes, or None."""
    kind = item.kind
    reference = item.effective_source.reference
    if kind is SourceKind.GLOBAL_INSTRUCTIONS:
        return "global-instructions"
    if kind is SourceKind.PROJECT_INSTRUCTIONS:
        return "project-instructions"
    if kind is SourceKind.MEMORY:
        return f"memory:{reference.rsplit('/', 1)[-1]}"
    if kind is SourceKind.BOT_ADDITION:
        return "bot-addition"
    if kind is SourceKind.SKILL:
        match = _SKILL_PATH.search(reference)
        return f"skill:{match.group('name')}" if match else f"skill:{reference}"
    if kind is SourceKind.COMMAND:
        match = _COMMAND_PATH.search(reference)
        return f"command:{match.group('name')}" if match else f"command:{reference}"
    if kind is SourceKind.CONNECTOR:
        return f"connector:{item.label.removeprefix('MCP server ').strip()}"
    if kind is SourceKind.HOOK:
        return f"hook:{item.label.removeprefix('hook ').strip()}"
    if kind in (SourceKind.TOOL, SourceKind.PLUGIN):
        return f"{kind.value}:{reference.rsplit('/', 1)[-1]}"
    if kind is SourceKind.HARNESS_SETTING:
        if "#" in item.item_id:
            key = item.item_id.rsplit("#", 1)[-1]
        elif item.item_id.startswith("codex/harness-setting/") and "/" not in (
            tail := item.item_id.removeprefix("codex/harness-setting/")
        ):
            key = tail
        else:
            return None  # a settings *file* is a container, not a behaviour
        key = key.replace("-", "_")
        if key == "permission_mode":
            key = "approval_policy"
        return f"setting:{key}"
    return None


def is_portable(identity: str) -> bool:
    return identity.startswith(_PORTABLE_PREFIXES)


# --------------------------------------------------------------------------- #
# Overlays: deliberate machine differences, declared up front
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DeclaredException:
    exception_id: str
    kind: ExceptionKind
    description: str
    preserved_outcome: str
    covers: tuple[str, ...]
    harness: Harness | None = None

    def matches(self, identity: str, harness: Harness) -> bool:
        if self.harness is not None and self.harness is not harness:
            return False
        return any(identity == c or identity.startswith(c.rstrip("*")) for c in self.covers)

    def to_dict(self) -> dict[str, object]:
        return {
            "exception_id": self.exception_id,
            "kind": self.kind.value,
            "description": self.description,
            "preserved_outcome": self.preserved_outcome,
            "covers": list(self.covers),
            "harness": None if self.harness is None else self.harness.value,
        }


@dataclass(frozen=True, slots=True)
class MachineOverlay:
    """What one machine deliberately does differently, and why."""

    machine: Machine
    exceptions: tuple[DeclaredException, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "machine": self.machine.value,
            "exceptions": [entry.to_dict() for entry in self.exceptions],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "overlay"
        try:
            machine = Machine(str(payload.get("machine")))
        except ValueError as error:
            raise ParityError(f"{where}: unknown machine {payload.get('machine')!r}") from error
        raw = payload.get("exceptions", ())
        if isinstance(raw, str) or not isinstance(raw, Sequence):
            raise ParityError(f"{where}: exceptions must be a list")
        exceptions: list[DeclaredException] = []
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise ParityError(f"{where}: each exception must be an object")
            covers = entry.get("covers", ())
            if isinstance(covers, str) or not isinstance(covers, Sequence) or not covers:
                raise ParityError(f"{where}: an exception must say what it covers")
            harness = entry.get("harness")
            try:
                exceptions.append(
                    DeclaredException(
                        exception_id=str(entry["exception_id"]),
                        kind=ExceptionKind(str(entry["kind"])),
                        description=str(entry["description"]),
                        preserved_outcome=str(entry["preserved_outcome"]),
                        covers=tuple(str(c) for c in covers),
                        harness=None if harness is None else Harness(str(harness)),
                    )
                )
            except (KeyError, ValueError) as error:
                raise ParityError(f"{where}: malformed exception: {error}") from error
        return cls(machine=machine, exceptions=tuple(exceptions))

    @classmethod
    def load(cls, path: Path) -> Self:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ParityError(f"overlay: {path} is unreadable: {error}") from error
        if not isinstance(payload, Mapping):
            raise ParityError("overlay: record must be an object")
        return cls.from_dict(payload)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ParityResult:
    identity: str
    status: ParityStatus
    targets: tuple[AuditTarget, ...]
    detail: str
    item_ids: tuple[str, ...] = ()
    exception_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "status": self.status.value,
            "targets": [t.to_dict() for t in self.targets],
            "detail": self.detail,
            "item_ids": list(self.item_ids),
            "exception_id": self.exception_id,
        }


@dataclass(frozen=True, slots=True)
class ParityReport:
    results: tuple[ParityResult, ...]
    findings: tuple[Finding, ...]
    exceptions: tuple[MachineException, ...]
    missing_targets: tuple[AuditTarget, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_targets

    def to_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "missing_targets": [t.to_dict() for t in self.missing_targets],
            "results": [r.to_dict() for r in self.results],
            "findings": [f.to_dict() for f in self.findings],
            "exceptions": [e.to_dict() for e in self.exceptions],
        }


# --------------------------------------------------------------------------- #
# The comparison
# --------------------------------------------------------------------------- #


@dataclass
class _State:
    overlays: Mapping[Machine, MachineOverlay]
    results: list[ParityResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    exceptions: dict[str, MachineException] = field(default_factory=dict)


def check_parity(
    inventories: Iterable[HarnessInventory],
    *,
    overlays: Sequence[MachineOverlay] = (),
) -> ParityReport:
    """Compare every approved target pair; name gaps and exceptions, fail drift."""
    by_target: dict[AuditTarget, HarnessInventory] = {}
    for inventory in inventories:
        by_target[inventory.target] = inventory
    missing = tuple(t for t in APPROVED_TARGETS if t not in by_target)
    state = _State(overlays={o.machine: o for o in overlays})

    index: dict[AuditTarget, dict[str, InventoryItem]] = {}
    for target, inventory in by_target.items():
        table: dict[str, InventoryItem] = {}
        for item in inventory.items:
            if item.evidence.level not in (EvidenceLevel.LOADED, EvidenceLevel.CONFIGURED):
                continue
            identity = portable_identity(item)
            if identity is not None and identity not in table:
                table[identity] = item
        index[target] = table

    # Cross-harness on one machine: portable identities only.
    for machine in Machine:
        left, right = AuditTarget(machine, Harness.CLAUDE), AuditTarget(machine, Harness.CODEX)
        _compare(state, index, left, right, missing, portable_only=True)
    # Cross-machine for one harness: portable identities and adapters.
    for harness in Harness:
        left, right = AuditTarget(Machine.DREWAI, harness), AuditTarget(Machine.IMAC, harness)
        _compare(state, index, left, right, missing, portable_only=False)

    return ParityReport(
        results=tuple(
            sorted(state.results, key=lambda r: (r.identity, [t.key for t in r.targets]))
        ),
        findings=tuple(sorted(state.findings, key=lambda f: f.finding_id)),
        exceptions=tuple(sorted(state.exceptions.values(), key=lambda e: e.exception_id)),
        missing_targets=missing,
    )


def _compare(
    state: _State,
    index: Mapping[AuditTarget, Mapping[str, InventoryItem]],
    left: AuditTarget,
    right: AuditTarget,
    missing: Sequence[AuditTarget],
    *,
    portable_only: bool,
) -> None:
    pair = (left, right)
    if left in missing or right in missing:
        gone = left if left in missing else right
        present = right if gone is left else left
        identities = sorted(index.get(present, {}))
        if portable_only:
            identities = [i for i in identities if is_portable(i)]
        for identity in identities:
            state.results.append(
                ParityResult(
                    identity=identity,
                    status=ParityStatus.UNKNOWN,
                    targets=pair,
                    detail=f"{gone.key} was not inspected; no parity conclusion is drawn",
                    item_ids=(index[present][identity].item_id,),
                )
            )
        if identities:
            state.findings.append(
                Finding(
                    finding_id=f"parity:{left.key}:{right.key}:gap",
                    check=AuditCheck.PARITY,
                    outcome=CheckOutcome.UNKNOWN,
                    severity=Severity.MEDIUM,
                    summary=(
                        f"{len(identities)} identities on {present.key} could not be compared:"
                        f" {gone.key} is a coverage gap"
                    ),
                    item_ids=tuple(index[present][i].item_id for i in identities),
                    targets=pair,
                    evidence=(
                        EvidenceRecord(
                            level=EvidenceLevel.UNKNOWN,
                            method="coverage-gap",
                            detail=f"no inventory for {gone.key}",
                            source_reference=f"{gone.key} inventory",
                            missing_evidence=(f"{gone.key} inventory",),
                        ),
                    ),
                    missing_evidence=(f"{gone.key} inventory",),
                )
            )
        return

    left_table, right_table = index.get(left, {}), index.get(right, {})
    identities = sorted(set(left_table) | set(right_table))
    if portable_only:
        identities = [i for i in identities if is_portable(i)]
    for identity in identities:
        a, b = left_table.get(identity), right_table.get(identity)
        item_ids = tuple(i.item_id for i in (a, b) if i is not None)
        if a is not None and b is not None and a.content_hash and a.content_hash == b.content_hash:
            state.results.append(
                ParityResult(identity, ParityStatus.ALIGNED, pair, "same content hash", item_ids)
            )
            continue
        if (
            a is not None
            and b is not None
            and identity.startswith("setting:")
            and (a.label == b.label or _same_setting_value(a, b))
        ):
            state.results.append(
                ParityResult(identity, ParityStatus.ALIGNED, pair, "same setting value", item_ids)
            )
            continue
        if (
            a is not None
            and b is not None
            and not a.content_hash
            and not b.content_hash
            and not identity.startswith("setting:")
        ):
            state.results.append(
                ParityResult(identity, ParityStatus.ALIGNED, pair, "present on both", item_ids)
            )
            continue
        exception = _exception_for(state, identity, pair, a, b)
        if exception is not None:
            state.results.append(
                ParityResult(
                    identity,
                    ParityStatus.EXCEPTION,
                    pair,
                    exception.description,
                    item_ids,
                    exception.exception_id,
                )
            )
            continue
        _drift(state, identity, pair, a, b)


def _same_setting_value(a: InventoryItem, b: InventoryItem) -> bool:
    return _setting_value(a.label) == _setting_value(b.label)


def _setting_value(label: str) -> str:
    text = label.split(" (", 1)[0]
    if " = " in text:
        return text.split(" = ", 1)[1].strip()
    parts = text.split()
    return parts[-1] if parts else text


def _exception_for(
    state: _State,
    identity: str,
    pair: tuple[AuditTarget, AuditTarget],
    a: InventoryItem | None,
    b: InventoryItem | None,
) -> MachineException | None:
    left, right = pair
    same_harness = left.harness is right.harness
    # Declared overlay exceptions, checked for whichever machine differs.
    for target in pair:
        overlay = state.overlays.get(target.machine)
        if overlay is None:
            continue
        for declared in overlay.exceptions:
            if declared.matches(identity, target.harness):
                exception = MachineException(
                    exception_id=declared.exception_id,
                    machine=target.machine,
                    kind=declared.kind,
                    description=declared.description,
                    preserved_outcome=declared.preserved_outcome,
                    evidence=EvidenceRecord(
                        level=EvidenceLevel.CONFIGURED,
                        method="declared-overlay",
                        detail=f"declared in the {target.machine.value} overlay; covers {identity}",
                        source_reference=f"{target.machine.value} overlay",
                    ),
                    harness=declared.harness,
                )
                state.exceptions.setdefault(exception.exception_id, exception)
                return exception
    # Model availability is an exception whether or not anyone declared it.
    if same_harness and identity in _MODEL_KEYS:
        machine = right.machine
        values = ", ".join(f"{i.target.key}: {_setting_value(i.label)}" for i in (a, b) if i)
        exception = MachineException(
            exception_id=f"auto-model-availability:{left.harness.value}:{identity}",
            machine=machine,
            kind=ExceptionKind.MODEL_AVAILABILITY,
            description=(
                f"{identity} differs between machines ({values}); the audit records the"
                " difference and does not recommend fabricating an unavailable option"
            ),
            preserved_outcome="the same portable rules, skills and project guidance load",
            evidence=EvidenceRecord(
                level=EvidenceLevel.CONFIGURED,
                method="setting-comparison",
                detail=values or identity,
                source_reference=f"{left.key} and {right.key} inventories",
            ),
            harness=left.harness,
        )
        state.exceptions.setdefault(exception.exception_id, exception)
        return exception
    # A harness that has no such feature at all is an adapter, not drift.
    if not same_harness and not is_portable(identity) and (a is None or b is None):
        present = a if a is not None else b
        assert present is not None
        exception = MachineException(
            exception_id=f"auto-harness-format:{left.machine.value}:{identity}",
            machine=left.machine,
            kind=ExceptionKind.HARNESS_FORMAT,
            description=f"{identity} exists only on {present.target.harness.value}; native adapter",
            preserved_outcome="portable behaviour is compared separately",
            evidence=EvidenceRecord(
                level=EvidenceLevel.CONFIGURED,
                method="adapter-presence",
                detail=present.effective_source.reference,
                source_reference=present.effective_source.reference,
            ),
            harness=present.target.harness,
        )
        state.exceptions.setdefault(exception.exception_id, exception)
        return exception
    return None


def _drift(
    state: _State,
    identity: str,
    pair: tuple[AuditTarget, AuditTarget],
    a: InventoryItem | None,
    b: InventoryItem | None,
) -> None:
    left, right = pair
    item_ids = tuple(i.item_id for i in (a, b) if i is not None)
    portable = is_portable(identity)
    if a is not None and b is not None:
        detail = (
            f"{identity}: {left.key} and {right.key} load separately maintained copies that differ"
        )
        summary = (
            f"{detail}; keep one canonical source ({a.effective_source.reference}) and the"
            " smallest necessary adapters; migration risk: the other copy"
            f" ({b.effective_source.reference}) may hold rules only one harness needs"
            if portable
            else f"{detail}; declare the difference as a named exception or align the setting"
        )
    else:
        present = a if a is not None else b
        assert present is not None
        absent = right if a is not None else left
        detail = f"{identity} reaches {present.target.key} but not {absent.key}"
        summary = (
            f"{detail}; link the canonical source there or declare a named exception"
            if portable
            else f"{detail}; install it there or declare a named installed-tool exception"
        )
    state.results.append(ParityResult(identity, ParityStatus.DRIFT, pair, detail, item_ids))
    state.findings.append(
        Finding(
            finding_id=f"parity:{left.key}:{right.key}:{identity}",
            check=AuditCheck.PARITY,
            outcome=CheckOutcome.FAIL,
            severity=Severity.MEDIUM if portable else Severity.LOW,
            summary=summary,
            item_ids=item_ids,
            targets=pair,
            evidence=tuple(
                EvidenceRecord(
                    level=i.evidence.level,
                    method="content-hash-comparison" if i.content_hash else "presence-comparison",
                    detail=f"{i.target.key}: {i.effective_source.reference}"
                    + (f" hashes to {i.content_hash[:19]}…" if i.content_hash else ""),
                    source_reference=i.effective_source.reference,
                )
                for i in (a, b)
                if i is not None
            ),
        )
    )


def group_by_identity(report: ParityReport) -> Mapping[str, tuple[ParityResult, ...]]:
    grouped: dict[str, list[ParityResult]] = defaultdict(list)
    for result in report.results:
        grouped[result.identity].append(result)
    return {key: tuple(value) for key, value in sorted(grouped.items())}
