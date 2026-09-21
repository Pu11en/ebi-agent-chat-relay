"""One verdict per audited item, by fixed precedence (task 3.2).

The rules in :mod:`rules` say what is wrong; this module says what to do
about it, and it says exactly one thing per item.  The verdict comes from a
fixed order over the *failing* checks (:data:`VERDICT_PRECEDENCE`), so two
runs on the same evidence agree and a reviewer can predict the answer from
the findings alone:

1. ``dead-configuration`` — configuration that can never take effect.  When
   the evidence is complete and the item is dead in every context (an
   undocumented hook event, a server switched off) the verdict is **Remove**;
   when it is only shadowed or excluded by one invocation the verdict is
   **Fix**, because another launch path may still read it.
2. ``project-content-is-global`` / ``scope`` — **Move to project**.
3. ``size`` on content injected at every session start — **Load only when
   needed**, with a concrete activation boundary (a skill that loads on
   invocation).
4. ``duplication``, ``permissions``, ``precedence``, ``load-behavior`` —
   **Fix**.
5. nothing failing — **Keep**.

Two guards sit above that order.  Model and reasoning selections are always
**Keep**: the audit does not tune them.  And incomplete evidence — an
``unknown`` level on the item or an ``unknown`` finding naming it — caps the
verdict at **Fix** and records the uncertainty; :class:`ClassificationRecord`
refuses ``Remove`` with uncertainty, and :func:`choose_verdict` never asks it
to.  Nothing here touches a file.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.models import (
    AuditCheck,
    AuditTarget,
    CheckOutcome,
    Classification,
    ClassificationRecord,
    EvidenceLevel,
    EvidenceRecord,
    Finding,
    InventoryItem,
    Scope,
    SourceKind,
)

VERDICT_PRECEDENCE: tuple[AuditCheck, ...] = (
    AuditCheck.DEAD_CONFIGURATION,
    AuditCheck.PROJECT_CONTENT_IS_GLOBAL,
    AuditCheck.SCOPE,
    AuditCheck.SIZE,
    AuditCheck.DUPLICATION,
    AuditCheck.PERMISSIONS,
    AuditCheck.PRECEDENCE,
    AuditCheck.LOAD_BEHAVIOR,
)
"""The order competing failing checks are settled in; first match wins."""

_EXCLUDED_MARKER = "excluded from cleanup"
_START_KINDS = frozenset(
    {SourceKind.GLOBAL_INSTRUCTIONS, SourceKind.PROJECT_INSTRUCTIONS, SourceKind.MEMORY}
)
_DEAD_EVERYWHERE_METHODS = frozenset({"settings-disabled-server", "config-disabled-server"})


def choose_verdict(
    *,
    failing: frozenset[AuditCheck],
    incomplete: bool,
    excluded: bool,
    removable: bool,
    kind: SourceKind,
    multi_source: bool,
) -> Classification:
    """The pure decision: the same inputs always give the same verdict.

    ``removable`` says the dead configuration is dead in every context (not
    merely shadowed for one launch path); ``incomplete`` forbids Remove.
    """
    if excluded:
        return Classification.KEEP
    for check in VERDICT_PRECEDENCE:
        if check not in failing:
            continue
        if check is AuditCheck.DEAD_CONFIGURATION:
            if removable and not incomplete and not multi_source:
                return Classification.REMOVE
            return Classification.FIX
        if check in (AuditCheck.PROJECT_CONTENT_IS_GLOBAL, AuditCheck.SCOPE):
            return Classification.MOVE_TO_PROJECT
        if check is AuditCheck.SIZE:
            return Classification.LOAD_ON_DEMAND if kind in _START_KINDS else Classification.FIX
        return Classification.FIX
    return Classification.KEEP


@dataclass(frozen=True, slots=True)
class _Seen:
    items: tuple[InventoryItem, ...]
    failing: tuple[Finding, ...]
    unknown: tuple[Finding, ...]


def classify(
    results: Sequence[CollectionResult], findings: Sequence[Finding]
) -> tuple[ClassificationRecord, ...]:
    """Exactly one record per item id, across every target that has it."""
    by_item: dict[str, list[InventoryItem]] = defaultdict(list)
    for result in results:
        for item in result.inventory.items:
            by_item[item.item_id].append(item)
    failing: dict[str, list[Finding]] = defaultdict(list)
    unknown: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        bucket = (
            failing
            if finding.outcome is CheckOutcome.FAIL
            else unknown
            if finding.outcome is CheckOutcome.UNKNOWN
            else None
        )
        if bucket is None:
            continue
        for item_id in finding.item_ids:
            if item_id in by_item:
                bucket[item_id].append(finding)
    records = [
        _classify_one(
            item_id,
            _Seen(
                items=tuple(sorted(items, key=lambda i: i.target.key)),
                failing=tuple(sorted(failing.get(item_id, ()), key=lambda f: f.finding_id)),
                unknown=tuple(sorted(unknown.get(item_id, ()), key=lambda f: f.finding_id)),
            ),
        )
        for item_id, items in by_item.items()
    ]
    return tuple(sorted(records, key=lambda record: record.item_id))


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _classify_one(item_id: str, seen: _Seen) -> ClassificationRecord:
    first = seen.items[0]
    targets: tuple[AuditTarget, ...] = tuple(item.target for item in seen.items)
    excluded = any(_EXCLUDED_MARKER in item.effective_behavior for item in seen.items)
    incomplete = any(item.evidence.level is EvidenceLevel.UNKNOWN for item in seen.items) or bool(
        seen.unknown
    )
    failing_checks = frozenset(finding.check for finding in seen.failing)
    dead = [f for f in seen.failing if f.check is AuditCheck.DEAD_CONFIGURATION]
    removable = bool(dead) and all(_dead_everywhere(item, dead) for item in seen.items)
    verdict = choose_verdict(
        failing=failing_checks,
        incomplete=incomplete,
        excluded=excluded,
        removable=removable,
        kind=first.kind,
        multi_source=any(len(item.sources) > 1 for item in seen.items),
    )
    decisive = _decisive(seen.failing)
    evidence: tuple[EvidenceRecord, ...] = tuple(item.evidence for item in seen.items)
    finding_ids = tuple(f.finding_id for f in (*seen.failing, *seen.unknown))
    uncertainty = _uncertainty(seen) if incomplete else ""
    reference = first.effective_source.reference
    scope = first.scope

    if excluded:
        reason = (
            "model/reasoning selection is excluded from the audit's scope; recorded only to"
            " identify the inspected configuration path"
        )
    elif decisive is None:
        reason = "no check fails on any target; the item earns its place"
    else:
        reason = f"{decisive.check.value} ({decisive.severity.value}): {decisive.summary}"

    proposed_scope = Scope.PROJECT if verdict is Classification.MOVE_TO_PROJECT else scope
    activation = ""
    if verdict is Classification.LOAD_ON_DEMAND:
        activation = (
            f"move the workflow-specific sections of {reference} into a skill under the"
            " harness skills directory so the body loads only when that skill is invoked;"
            " keep the portable rules in place"
        )
    return ClassificationRecord(
        item_id=item_id,
        classification=verdict,
        reason=reason,
        evidence=evidence,
        affected_targets=targets,
        proposed_scope=proposed_scope,
        risk=_risk(verdict, first),
        reversible_action=_reversible_action(verdict, first),
        finding_ids=finding_ids,
        activation_boundary=activation,
        uncertainty=uncertainty,
    )


def _dead_everywhere(item: InventoryItem, dead: Sequence[Finding]) -> bool:
    if "can never fire" in item.effective_behavior:
        return True
    if item.evidence.method in _DEAD_EVERYWHERE_METHODS:
        return True
    return any("can never fire" in finding.summary for finding in dead)


def _decisive(failing: Sequence[Finding]) -> Finding | None:
    for check in VERDICT_PRECEDENCE:
        for finding in failing:
            if finding.check is check:
                return finding
    return None


def _uncertainty(seen: _Seen) -> str:
    missing: list[str] = []
    for item in seen.items:
        if item.evidence.level is EvidenceLevel.UNKNOWN:
            missing.extend(f"{item.target.key}: {m}" for m in item.evidence.missing_evidence)
    for finding in seen.unknown:
        missing.extend(f"{finding.check.value}: {m}" for m in finding.missing_evidence)
    unique = sorted(set(missing))
    return "evidence incomplete — missing " + "; ".join(unique)


_RISK: Mapping[Classification, str] = {
    Classification.KEEP: "none; nothing changes",
    Classification.FIX: "low; the item stays in place while its content or settings are corrected",
    Classification.MOVE_TO_PROJECT: (
        "medium; other projects lose the content, which is the intent — verify the target"
        " project still receives it"
    ),
    Classification.LOAD_ON_DEMAND: (
        "medium; sessions that relied on the content being present at start must invoke the"
        " skill instead"
    ),
    Classification.REMOVE: (
        "medium; global configuration affects every project and both harnesses, so the item"
        " is quarantined first and removed only after the target checks pass"
    ),
}


def _risk(verdict: Classification, item: InventoryItem) -> str:
    text = _RISK[verdict]
    if item.kind is SourceKind.HOOK and verdict is not Classification.KEEP:
        text += "; hooks run shell commands, so rerun the harness checks after the change"
    return text


def _reversible_action(verdict: Classification, item: InventoryItem) -> str:
    reference = item.effective_source.reference
    is_file = reference.startswith("~") or "/" in reference.split("#", 1)[0]
    if verdict is Classification.KEEP:
        return "none required"
    if verdict is Classification.REMOVE:
        if "#" in reference or not is_file:
            return (
                f"quarantine: comment the entry out of {reference.split('#', 1)[0]} under version"
                " control; rollback restores the previous file by hash"
            )
        return (
            f"quarantine: move {reference} into the quarantine directory with a manifest;"
            " rollback moves it back and verifies the hash"
        )
    if verdict is Classification.MOVE_TO_PROJECT:
        return (
            f"copy the project-specific sections of {reference} into the project's own"
            " instructions file, then quarantine the global copy; rollback restores it by hash"
        )
    if verdict is Classification.LOAD_ON_DEMAND:
        return (
            f"create the skill first, then quarantine the moved sections of {reference};"
            " rollback restores the file by hash"
        )
    return (
        f"edit {reference.split('#', 1)[0]} under version control (or quarantine the file with"
        " a manifest); rollback restores it by hash"
    )
