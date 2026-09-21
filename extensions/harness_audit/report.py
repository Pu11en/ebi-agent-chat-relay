"""The audit report: machine-readable and plain-language (task 4.1).

A report is assembled from one redacted bundle per machine — so it can only
ever contain what was allowed to cross a machine boundary — plus the parity
result computed over the combined inventories.  It renders the same evidence
two ways:

* :func:`render_json` — every record, in a fixed key order, for tooling and
  for diffing two runs.
* :func:`render_text` — sections a person reads top to bottom: coverage,
  what each target loads (loaded vs installed-only, never blurred), token
  impact with the estimate labeled as an estimate, findings with their local
  evidence and dated citations, one verdict per item, parity and the named
  machine exceptions.

Coverage is the first line because it qualifies everything after it.  Any of
the four approved targets without an inventory becomes a
:class:`CoverageGap`, the report is marked **PARTIAL**, and no verdict, parity
result or token total is stated for the missing target.  Rendering touches no
file and calls no model.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from extensions.harness_audit.models import (
    APPROVED_TARGETS,
    AuditTarget,
    CheckOutcome,
    Classification,
    ClassificationRecord,
    CoverageGap,
    EvidenceLevel,
    Finding,
    HarnessInventory,
    InventoryItem,
    MachineException,
    Severity,
    SizeMeasurement,
    SourceKind,
    TokenCountKind,
)
from extensions.harness_audit.parity import ParityReport, ParityStatus
from extensions.harness_audit.redaction import TOKEN_ESTIMATE_METHOD, RedactedBundle

_SEVERITY_ORDER = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
    Severity.INFO: 3,
}
_OUTCOME_ORDER = {CheckOutcome.FAIL: 0, CheckOutcome.UNKNOWN: 1, CheckOutcome.PASS: 2}
_START_KINDS = frozenset(
    {
        SourceKind.GLOBAL_INSTRUCTIONS,
        SourceKind.PROJECT_INSTRUCTIONS,
        SourceKind.MEMORY,
        SourceKind.BOT_ADDITION,
    }
)


@dataclass(frozen=True, slots=True)
class TokenImpact:
    """Bytes proven loaded at every session start on one target."""

    target: AuditTarget
    loaded_bytes: int
    loaded_characters: int
    item_count: int

    @property
    def size(self) -> SizeMeasurement:
        return SizeMeasurement(
            byte_size=self.loaded_bytes,
            characters=self.loaded_characters,
            token_count=self.loaded_characters // 4,
            token_count_kind=TokenCountKind.ESTIMATED,
            token_method=TOKEN_ESTIMATE_METHOD,
        )

    def to_dict(self) -> dict[str, object]:
        size = self.size
        return {
            "target": self.target.key,
            "loaded_bytes": self.loaded_bytes,
            "loaded_characters": self.loaded_characters,
            "item_count": self.item_count,
            "token_count": size.token_count,
            "token_count_kind": size.token_count_kind.value,
            "token_method": size.token_method,
            "label": size.label,
        }


@dataclass(frozen=True, slots=True)
class AuditReport:
    generated_at: datetime
    bundles: tuple[RedactedBundle, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    parity: ParityReport | None

    @property
    def inventories(self) -> tuple[HarnessInventory, ...]:
        return tuple(
            sorted(
                (inv for bundle in self.bundles for inv in bundle.inventories),
                key=lambda inv: inv.target.key,
            )
        )

    @property
    def covered_targets(self) -> tuple[AuditTarget, ...]:
        return tuple(inv.target for inv in self.inventories)

    @property
    def is_partial(self) -> bool:
        return bool(self.coverage_gaps)

    @property
    def findings(self) -> tuple[Finding, ...]:
        found = [f for bundle in self.bundles for f in bundle.findings]
        if self.parity is not None:
            found.extend(self.parity.findings)
        return tuple(sorted(found, key=_finding_order))

    @property
    def classifications(self) -> tuple[ClassificationRecord, ...]:
        return tuple(
            sorted(
                (c for bundle in self.bundles for c in bundle.classifications),
                key=lambda c: (c.item_id, [t.key for t in c.affected_targets]),
            )
        )

    @property
    def exceptions(self) -> tuple[MachineException, ...]:
        found = {e.exception_id: e for bundle in self.bundles for e in bundle.exceptions}
        if self.parity is not None:
            for exception in self.parity.exceptions:
                found.setdefault(exception.exception_id, exception)
        return tuple(sorted(found.values(), key=lambda e: e.exception_id))

    @property
    def token_impact(self) -> tuple[TokenImpact, ...]:
        return tuple(_token_impact(inv) for inv in self.inventories)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "coverage": {
                "complete": not self.is_partial,
                "covered": [t.key for t in self.covered_targets],
                "gaps": [
                    {
                        "target": gap.target.key,
                        "reason": gap.reason,
                        "missing_evidence": list(gap.missing_evidence),
                    }
                    for gap in self.coverage_gaps
                ],
            },
            "inventories": [
                {
                    "target": inv.target.key,
                    "collected_at": inv.collected_at.isoformat(),
                    "counts": _counts(inv),
                    "items": [item.to_dict() for item in inv.items],
                }
                for inv in self.inventories
            ],
            "token_impact": [impact.to_dict() for impact in self.token_impact],
            "findings": [f.to_dict() for f in self.findings],
            "verdicts": [c.to_dict() for c in self.classifications],
            "exceptions": [e.to_dict() for e in self.exceptions],
            "parity": None if self.parity is None else self.parity.to_dict(),
            "redaction_notes": [
                {"machine": bundle.machine.value, **note.to_dict()}
                for bundle in self.bundles
                for note in bundle.notes
            ],
        }


def build_report(
    bundles: Iterable[RedactedBundle],
    *,
    parity: ParityReport | None,
    generated_at: datetime,
) -> AuditReport:
    """Combine one bundle per machine; every approved target not present is a gap."""
    ordered = tuple(sorted(bundles, key=lambda b: b.machine.value))
    present = {inv.target for bundle in ordered for inv in bundle.inventories}
    gaps: dict[AuditTarget, CoverageGap] = {}
    for bundle in ordered:
        for gap in bundle.coverage_gaps:
            gaps.setdefault(gap.target, gap)
    machines = {bundle.machine for bundle in ordered}
    for target in APPROVED_TARGETS:
        if target in present or target in gaps:
            continue
        if target.machine in machines:
            reason = (
                f"the {target.machine.value} bundle carries no {target.harness.value} inventory"
            )
            missing = (f"{target.harness.value} configuration on {target.machine.value}",)
        else:
            reason = f"no bundle was imported for {target.machine.value}"
            missing = (f"{target.machine.value} redacted bundle",)
        gaps[target] = CoverageGap(
            target=target, reason=reason, missing_evidence=missing, observed_at=generated_at
        )
    return AuditReport(
        generated_at=generated_at,
        bundles=ordered,
        coverage_gaps=tuple(gaps[t] for t in sorted(gaps, key=lambda t: t.key)),
        parity=parity,
    )


def render_json(report: AuditReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)


# --------------------------------------------------------------------------- #
# Plain language
# --------------------------------------------------------------------------- #


def render_text(report: AuditReport) -> str:
    lines: list[str] = []
    out = lines.append
    covered = report.covered_targets
    total = len(APPROVED_TARGETS)
    out(f"Harness configuration audit — generated {report.generated_at.isoformat()}")
    status = "COMPLETE" if not report.is_partial else "PARTIAL"
    out(f"Coverage: {status} ({len(covered)}/{total} targets)")
    if report.is_partial:
        out("")
        out("== Coverage gaps ==")
        for gap in report.coverage_gaps:
            out(f"  {gap.target.key}: coverage gap — {gap.reason}")
            out(f"    could not collect: {', '.join(gap.missing_evidence)}")
        out("  No verdict, parity result or token total is stated for a missing target.")

    for inventory in report.inventories:
        out("")
        out(f"== Inventory: {inventory.target.key} ==")
        counts = _counts(inventory)
        out(
            f"  {len(inventory.items)} items: {counts['loaded']} loaded,"
            f" {counts['configured']} configured, {counts['installed-only']} installed-only,"
            f" {counts['unknown']} unknown"
        )
        for level in (
            EvidenceLevel.LOADED,
            EvidenceLevel.CONFIGURED,
            EvidenceLevel.INSTALLED_ONLY,
            EvidenceLevel.UNKNOWN,
        ):
            members = [i for i in inventory.items if i.evidence.level is level]
            if not members:
                continue
            if level is EvidenceLevel.INSTALLED_ONLY:
                out("  installed but not loaded (not counted toward effective context):")
            for item in members:
                out(f"  [{level.value}] {item.item_id}")
                out(f"      {item.kind.value}, scope {item.scope.value}: {_describe(item)}")

    out("")
    out("== Token impact ==")
    out("  Exact bytes are authoritative; token numbers are estimates, not provider billing data.")
    for impact in report.token_impact:
        out(f"  {impact.target.key}: {impact.item_count} loaded item(s); {impact.size.label}")

    out("")
    out("== Findings ==")
    findings = report.findings
    failing = [f for f in findings if f.outcome is CheckOutcome.FAIL]
    unknown = [f for f in findings if f.outcome is CheckOutcome.UNKNOWN]
    passing = [f for f in findings if f.outcome is CheckOutcome.PASS]
    out(f"  {len(failing)} failing, {len(unknown)} unknown, {len(passing)} passing")
    for finding in (*failing, *unknown):
        out(f"  [{finding.outcome.value.upper()} {finding.severity.value}] {finding.check.value}:")
        out(f"      {finding.summary}")
        out(f"      targets: {', '.join(t.key for t in finding.targets)}")
        for record in finding.evidence:
            out(f"      evidence: {record.method} — {record.detail} ({record.source_reference})")
        if finding.comparison_method:
            out(f"      comparison: {finding.comparison_method}")
        if finding.duplicated_bytes:
            out(f"      duplicated: {finding.duplicated_bytes} bytes")
        for citation in finding.vendor_sources:
            out(
                f"      source: {citation.url} (retrieved {citation.retrieved_on.isoformat()},"
                f" versions {citation.applies_to_versions or 'unpinned'})"
            )
        if finding.missing_evidence:
            out(f"      missing evidence: {', '.join(finding.missing_evidence)}")
    for finding in passing:
        out(f"  [PASS] {finding.check.value}: {finding.summary}")

    out("")
    out("== Verdicts ==")
    by_verdict: dict[Classification, list[ClassificationRecord]] = defaultdict(list)
    for record in report.classifications:
        by_verdict[record.classification].append(record)
    out(
        "  "
        + ", ".join(
            f"{verdict.value}: {len(by_verdict.get(verdict, []))}" for verdict in Classification
        )
    )
    for verdict in Classification:
        for record in by_verdict.get(verdict, []):
            out(f"  [{verdict.value}] {record.item_id}")
            out(f"      targets: {', '.join(t.key for t in record.affected_targets)}")
            out(f"      reason: {record.reason}")
            if verdict is not Classification.KEEP:
                out(f"      proposed scope: {record.proposed_scope.value}; risk: {record.risk}")
                out(f"      reversible action: {record.reversible_action}")
            if record.activation_boundary:
                out(f"      activation boundary: {record.activation_boundary}")
            if record.uncertainty:
                out(f"      uncertainty: {record.uncertainty}")

    out("")
    out("== Parity ==")
    parity = report.parity
    if parity is None:
        out("  not run (parity needs the combined inventories of both machines)")
    else:
        tally = {status: 0 for status in ParityStatus}
        for result in parity.results:
            tally[result.status] += 1
        out(
            "  "
            + ", ".join(f"{status.value}: {count}" for status, count in tally.items())
            + (
                ""
                if parity.complete
                else "; partial — missing " + ", ".join(t.key for t in parity.missing_targets)
            )
        )
        for result in parity.results:
            if result.status is ParityStatus.ALIGNED:
                continue
            suffix = f" [{result.exception_id}]" if result.exception_id else ""
            out(
                f"  [{result.status.value}] {result.identity}"
                f" ({' vs '.join(t.key for t in result.targets)}): {result.detail}{suffix}"
            )

    out("")
    out("== Machine exceptions ==")
    if not report.exceptions:
        out("  none")
    for exception in report.exceptions:
        scope = exception.harness.value if exception.harness else "both harnesses"
        header = f"{exception.kind.value}, {exception.machine.value}, {scope}"
        out(f"  {exception.exception_id} ({header})")
        out(f"      {exception.description}")
        out(f"      preserved outcome: {exception.preserved_outcome}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _counts(inventory: HarnessInventory) -> dict[str, int]:
    counts = {level.value: 0 for level in EvidenceLevel}
    for item in inventory.items:
        counts[item.evidence.level.value] += 1
    return counts


def _describe(item: InventoryItem) -> str:
    parts: list[str] = []
    if item.size is not None:
        parts.append(item.size.label)
    if item.permissions:
        parts.append(f"mode {item.permissions}")
    parts.append(f"{item.evidence.method} — {item.evidence.detail}")
    if len(item.sources) > 1:
        parts.append("sources: " + ", ".join(s.reference for s in item.sources))
    return "; ".join(parts)


def _token_impact(inventory: HarnessInventory) -> TokenImpact:
    total_bytes = total_chars = count = 0
    for item in inventory.effective_items:
        if item.kind not in _START_KINDS or item.size is None:
            continue
        total_bytes += item.size.byte_size
        total_chars += item.size.characters
        count += 1
    return TokenImpact(inventory.target, total_bytes, total_chars, count)


def _finding_order(finding: Finding) -> tuple[int, int, str]:
    return (_OUTCOME_ORDER[finding.outcome], _SEVERITY_ORDER[finding.severity], finding.finding_id)


def summarize_targets(targets: Sequence[AuditTarget]) -> str:
    return ", ".join(t.key for t in targets)
