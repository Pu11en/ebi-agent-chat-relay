"""Deterministic audit checks over collected inventories (task 3.1).

The rules are pure functions of :class:`CollectionResult` records and the
pinned source manifest.  They answer the eight professional checks for every
item — scope, duplication, size, permissions, load behaviour, precedence,
dead configuration and project-content-is-global — and they answer each one
in exactly one of three ways: ``pass``, ``fail`` or ``unknown`` with the
evidence that is missing.  A check that cannot be decided is never rounded to
a pass or a failure.

Two conventions keep the output honest and readable:

* **Every finding carries local evidence.**  The evidence record names the
  path, invocation flag or environment variable it was derived from.  When a
  verdict rests on documented vendor behaviour (what a scope means, what a
  flag excludes, what shadows what) the finding also cites the official
  source pinned in ``sources.json`` with its retrieval date; the manifest is
  the only place that knowledge lives.
* **Failures and unknowns are per item; passes are aggregated.**  A finding
  that needs action names one item (or one duplicate group).  Items that pass
  a check are listed together in one finding per target and check, so the
  report is complete without being eight lines per file.

Sizes are reported as exact bytes and characters with a clearly labeled
estimate; the estimate is never presented as provider billing data.  Model and
reasoning settings are observed for precedence but excluded from dead
configuration: the audit does not tune them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.discovery import CLAUDE_HOOK_EVENTS, ContentSignals, DiscoveredFile
from extensions.harness_audit.models import (
    AuditCheck,
    AuditTarget,
    CheckOutcome,
    EvidenceLevel,
    EvidenceRecord,
    Finding,
    Harness,
    InventoryItem,
    Scope,
    Severity,
    SourceKind,
    VendorCitation,
)
from extensions.harness_audit.sources import SourceManifest

LARGE_CONTEXT_BYTES = 16_384
"""Above this many bytes injected at session start, the size check fails."""

_EXCLUDED_MARKER = "excluded from cleanup"
_START_KINDS = frozenset(
    {
        SourceKind.GLOBAL_INSTRUCTIONS,
        SourceKind.PROJECT_INSTRUCTIONS,
        SourceKind.MEMORY,
        SourceKind.BOT_ADDITION,
    }
)
_CONTENT_KINDS = _START_KINDS | {SourceKind.SKILL, SourceKind.COMMAND, SourceKind.TOOL}
_HOOK_EVENT = re.compile(r"#(?P<event>[A-Za-z]+)\[")
_DANGEROUS_PERMISSION_VALUES = ("danger-full-access", "bypassPermissions", "never")


@dataclass(frozen=True, slots=True)
class _Item:
    """An inventory item joined with the side channels its collector kept."""

    item: InventoryItem
    signals: ContentSignals
    file: DiscoveredFile | None

    @property
    def target(self) -> AuditTarget:
        return self.item.target

    @property
    def level(self) -> EvidenceLevel:
        return self.item.evidence.level

    @property
    def counts_as_context(self) -> bool:
        return self.level in (EvidenceLevel.LOADED, EvidenceLevel.CONFIGURED)

    @property
    def resolved_reference(self) -> str:
        if self.file is not None:
            return self.file.resolved_path
        return self.item.effective_source.reference

    @property
    def start_bytes(self) -> int | None:
        """Bytes injected at session start, or None when nothing is."""
        if self.item.kind is SourceKind.SKILL:
            return self.signals.frontmatter_bytes
        if self.item.kind in _START_KINDS and self.item.size is not None:
            return self.item.size.byte_size
        return None


@dataclass
class _Run:
    manifest: SourceManifest
    large_context_bytes: int
    items: list[_Item] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    passes: dict[tuple[str, AuditCheck], list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def cite(self, harness: Harness, check: AuditCheck) -> tuple[VendorCitation, ...]:
        return tuple(source.citation() for source in self.manifest.for_check(harness, check)[:1])

    def local(
        self, entry: _Item, method: str, detail: str, *, vendor: bool = False
    ) -> EvidenceRecord:
        level = (
            entry.level if entry.level is not EvidenceLevel.UNKNOWN else EvidenceLevel.CONFIGURED
        )
        return EvidenceRecord(
            level=level,
            method=method,
            detail=detail,
            source_reference=entry.item.effective_source.reference,
            depends_on_vendor_behavior=vendor,
        )

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def passed(self, entry: _Item, check: AuditCheck) -> None:
        self.passes[(entry.target.key, check)].append(entry.item.item_id)


def run_rules(
    results: Sequence[CollectionResult],
    *,
    manifest: SourceManifest,
    large_context_bytes: int = LARGE_CONTEXT_BYTES,
) -> tuple[Finding, ...]:
    """Apply every check to every item and return the findings, sorted by id."""
    run = _Run(manifest=manifest, large_context_bytes=large_context_bytes)
    for result in results:
        for item in result.inventory.items:
            run.items.append(
                _Item(
                    item=item,
                    signals=result.signals.get(item.item_id, ContentSignals()),
                    file=result.files.get(item.item_id),
                )
            )
    _check_load_behavior(run)
    _check_duplication(run)
    _check_scope(run)
    _check_size(run)
    _check_permissions(run)
    _check_precedence_and_dead_configuration(run)
    _emit_passes(run)
    return tuple(sorted(run.findings, key=lambda finding: finding.finding_id))


# --------------------------------------------------------------------------- #
# Load behavior
# --------------------------------------------------------------------------- #


def _check_load_behavior(run: _Run) -> None:
    for entry in run.items:
        item = entry.item
        harness = item.target.harness
        if entry.level is EvidenceLevel.UNKNOWN:
            run.add(
                Finding(
                    finding_id=f"load-behavior:{item.target.key}:{item.item_id}",
                    check=AuditCheck.LOAD_BEHAVIOR,
                    outcome=CheckOutcome.UNKNOWN,
                    severity=Severity.MEDIUM,
                    summary=f"{item.label}: whether it loads is unknown — {item.evidence.detail}",
                    item_ids=(item.item_id,),
                    targets=(item.target,),
                    evidence=(item.evidence,),
                    vendor_sources=run.cite(harness, AuditCheck.LOAD_BEHAVIOR),
                    missing_evidence=item.evidence.missing_evidence,
                )
            )
            continue
        run.passed(entry, AuditCheck.LOAD_BEHAVIOR)


# --------------------------------------------------------------------------- #
# Duplication
# --------------------------------------------------------------------------- #


def _check_duplication(run: _Run) -> None:
    groups: dict[str, list[_Item]] = defaultdict(list)
    for entry in run.items:
        if (
            entry.item.kind in _CONTENT_KINDS
            and entry.item.content_hash
            and entry.counts_as_context
        ):
            groups[entry.item.content_hash].append(entry)
    for content_hash, members in sorted(groups.items()):
        if len(members) < 2:
            for entry in members:
                run.passed(entry, AuditCheck.DUPLICATION)
            continue
        resolved = {entry.resolved_reference for entry in members}
        ids = tuple(entry.item.item_id for entry in members)
        targets = tuple(entry.target for entry in members)
        size = max((entry.item.size.byte_size if entry.item.size else 0) for entry in members)
        evidence = tuple(
            run.local(
                entry,
                "salted-content-hash",
                f"{entry.item.effective_source.reference} hashes to {content_hash[:19]}…"
                f" ({entry.level.value})",
            )
            for entry in members
        )
        citations = tuple(
            citation
            for harness in sorted({t.harness for t in targets}, key=lambda h: h.value)
            for citation in run.cite(harness, AuditCheck.DUPLICATION)
        )
        if len(resolved) == 1:
            canonical = next(iter(resolved))
            run.add(
                Finding(
                    finding_id=f"duplication:canonical:{content_hash[7:19]}",
                    check=AuditCheck.DUPLICATION,
                    outcome=CheckOutcome.PASS,
                    severity=Severity.INFO,
                    summary=(
                        f"{len(members)} sources resolve to the canonical {canonical}; one copy"
                        f" of {size} bytes is maintained"
                    ),
                    item_ids=ids,
                    targets=targets,
                    evidence=evidence,
                    vendor_sources=citations,
                    comparison_method="salted sha256 of file bytes; resolved link targets compared",
                    duplicated_bytes=0,
                )
            )
            continue
        loaded = all(entry.level is EvidenceLevel.LOADED for entry in members)
        run.add(
            Finding(
                finding_id=f"duplication:{content_hash[7:19]}",
                check=AuditCheck.DUPLICATION,
                outcome=CheckOutcome.FAIL,
                severity=Severity.HIGH if loaded else Severity.MEDIUM,
                summary=(
                    f"{len(members)} separately maintained copies of the same content"
                    f" ({size} bytes each): {', '.join(sorted(resolved))}; keep one canonical"
                    " source and link the rest"
                ),
                item_ids=ids,
                targets=targets,
                evidence=evidence,
                vendor_sources=citations,
                comparison_method="salted sha256 of file bytes (same salt on every target)",
                duplicated_bytes=size,
            )
        )


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def _check_scope(run: _Run) -> None:
    for entry in run.items:
        item = entry.item
        if not entry.counts_as_context or item.kind not in _CONTENT_KINDS:
            continue
        if item.scope is Scope.GLOBAL and entry.signals.referenced_projects:
            projects = ", ".join(entry.signals.referenced_projects)
            harness = item.target.harness
            run.add(
                Finding(
                    finding_id=f"project-content-is-global:{item.target.key}:{item.item_id}",
                    check=AuditCheck.PROJECT_CONTENT_IS_GLOBAL,
                    outcome=CheckOutcome.FAIL,
                    severity=Severity.MEDIUM,
                    summary=(
                        f"{item.label} is global but names the project(s) {projects}; move the"
                        " project-specific content into that project's own instructions file"
                    ),
                    item_ids=(item.item_id,),
                    targets=(item.target,),
                    evidence=(
                        run.local(
                            entry,
                            "project-name-token-match",
                            f"global-scope content references {projects} by name",
                            vendor=True,
                        ),
                    ),
                    vendor_sources=run.cite(harness, AuditCheck.SCOPE),
                )
            )
            continue
        run.passed(entry, AuditCheck.PROJECT_CONTENT_IS_GLOBAL)
        run.passed(entry, AuditCheck.SCOPE)


# --------------------------------------------------------------------------- #
# Size
# --------------------------------------------------------------------------- #


def _check_size(run: _Run) -> None:
    totals: dict[AuditTarget, tuple[int, int, list[str]]] = {}
    for entry in run.items:
        item = entry.item
        start = entry.start_bytes
        if start is None or item.size is None or not entry.counts_as_context:
            continue
        label = item.size.label
        if item.kind is SourceKind.SKILL:
            label = f"{start} bytes of frontmatter at session start; body {label}"
        outcome = CheckOutcome.FAIL if start > run.large_context_bytes else CheckOutcome.PASS
        run.add(
            Finding(
                finding_id=f"size:{item.target.key}:{item.item_id}",
                check=AuditCheck.SIZE,
                outcome=outcome,
                severity=Severity.MEDIUM if outcome is CheckOutcome.FAIL else Severity.INFO,
                summary=(
                    f"{item.label}: {label}"
                    + (
                        f"; exceeds {run.large_context_bytes} bytes per session start"
                        if outcome is CheckOutcome.FAIL
                        else ""
                    )
                ),
                item_ids=(item.item_id,),
                targets=(item.target,),
                evidence=(run.local(entry, "measured-size", f"exact size {item.size.label}"),),
            )
        )
        if entry.level is EvidenceLevel.LOADED:
            bytes_total, estimate_total, ids = totals.get(item.target, (0, 0, []))
            estimate = item.size.token_count or 0
            if item.kind is SourceKind.SKILL:
                estimate = start // 4
            totals[item.target] = (
                bytes_total + start,
                estimate_total + estimate,
                [*ids, item.item_id],
            )
    for target, (bytes_total, estimate_total, ids) in sorted(
        totals.items(), key=lambda p: p[0].key
    ):
        run.add(
            Finding(
                finding_id=f"size:{target.key}:session-start-total",
                check=AuditCheck.SIZE,
                outcome=CheckOutcome.PASS,
                severity=Severity.INFO,
                summary=(
                    f"{target.key}: {bytes_total} bytes proven loaded at every session start"
                    f" across {len(ids)} items; about {estimate_total} tokens estimated via"
                    " characters/4 heuristic (not provider billing data)"
                ),
                item_ids=tuple(ids),
                targets=(target,),
                evidence=(
                    EvidenceRecord(
                        level=EvidenceLevel.LOADED,
                        method="measured-size",
                        detail="sum of exact byte sizes of items with loaded evidence",
                        source_reference=f"{target.key} inventory",
                    ),
                ),
            )
        )


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


def _check_permissions(run: _Run) -> None:
    for entry in run.items:
        item = entry.item
        harness = item.target.harness
        if item.kind is SourceKind.HARNESS_SETTING and any(
            value in item.label for value in _DANGEROUS_PERMISSION_VALUES
        ):
            run.add(
                Finding(
                    finding_id=f"permissions:{item.target.key}:{item.item_id}",
                    check=AuditCheck.PERMISSIONS,
                    outcome=CheckOutcome.FAIL,
                    severity=Severity.HIGH,
                    summary=f"{item.label} removes the permission checks for every session",
                    item_ids=(item.item_id,),
                    targets=(item.target,),
                    evidence=(run.local(entry, "setting-value", item.label, vendor=True),),
                    vendor_sources=run.cite(harness, AuditCheck.PERMISSIONS),
                )
            )
            continue
        if entry.file is None:
            continue
        mode = item.permissions
        if mode == "unknown" or not re.fullmatch(r"[0-7]{4}", mode):
            run.add(
                Finding(
                    finding_id=f"permissions:{item.target.key}:{item.item_id}",
                    check=AuditCheck.PERMISSIONS,
                    outcome=CheckOutcome.UNKNOWN,
                    severity=Severity.LOW,
                    summary=f"{item.label}: file mode could not be inspected",
                    item_ids=(item.item_id,),
                    targets=(item.target,),
                    evidence=(
                        EvidenceRecord(
                            level=EvidenceLevel.UNKNOWN,
                            method="stat-mode",
                            detail="no POSIX mode bits available (Windows ACLs are not inspected)",
                            source_reference=item.effective_source.reference,
                            missing_evidence=("POSIX mode bits",),
                        ),
                    ),
                    missing_evidence=("POSIX mode bits",),
                )
            )
            continue
        bits = int(mode, 8)
        others_write = bits & 0o022
        others_read = bits & 0o044
        problems: list[str] = []
        if others_write:
            problems.append(f"mode {mode} lets group/others write it")
        if others_read and item.redaction.value == "redacted":
            problems.append(f"mode {mode} lets group/others read a file that holds a secret")
        if not problems:
            run.passed(entry, AuditCheck.PERMISSIONS)
            continue
        runs_code = item.kind in (SourceKind.HOOK, SourceKind.HARNESS_SETTING, SourceKind.PLUGIN)
        run.add(
            Finding(
                finding_id=f"permissions:{item.target.key}:{item.item_id}",
                check=AuditCheck.PERMISSIONS,
                outcome=CheckOutcome.FAIL,
                severity=Severity.HIGH if (runs_code or others_write) else Severity.MEDIUM,
                summary=f"{item.label}: " + "; ".join(problems),
                item_ids=(item.item_id,),
                targets=(item.target,),
                evidence=(run.local(entry, "stat-mode", f"mode {mode}", vendor=runs_code),),
                # PERMISSIONS is a vendor-backed check, so every FAIL must cite
                # an official source — even when the evidence itself (POSIX
                # mode bits) is not vendor behaviour.
                vendor_sources=run.cite(harness, AuditCheck.PERMISSIONS),
            )
        )


# --------------------------------------------------------------------------- #
# Precedence and dead configuration
# --------------------------------------------------------------------------- #


def _check_precedence_and_dead_configuration(run: _Run) -> None:
    commands: dict[tuple[str, str], list[_Item]] = defaultdict(list)
    for entry in run.items:
        item = entry.item
        harness = item.target.harness
        excluded = _EXCLUDED_MARKER in item.effective_behavior
        if len(item.sources) > 1:
            winner = item.effective_source
            others = [s for s in item.sources if s is not winner]
            is_link = entry.file is not None and entry.file.is_symlink
            run.add(
                Finding(
                    finding_id=f"precedence:{item.target.key}:{item.item_id}",
                    check=AuditCheck.PRECEDENCE,
                    outcome=CheckOutcome.PASS,
                    severity=Severity.INFO,
                    summary=(
                        f"{item.label}: {winner.reference} is effective"
                        + (
                            f" (link to {others[0].reference})"
                            if is_link
                            else f"; also set by {', '.join(s.reference for s in others)}"
                        )
                    ),
                    item_ids=(item.item_id,),
                    targets=(item.target,),
                    evidence=(
                        run.local(
                            entry,
                            "source-precedence",
                            "; ".join(
                                f"{s.reference} precedence {s.precedence}" for s in item.sources
                            ),
                            vendor=True,
                        ),
                    ),
                    vendor_sources=run.cite(harness, AuditCheck.PRECEDENCE),
                )
            )
            if not is_link and not excluded:
                _dead(
                    run,
                    entry,
                    f"{item.label}: the value in {', '.join(s.reference for s in others)} is"
                    f" shadowed by {winner.reference} and never takes effect",
                    Severity.LOW,
                )
                continue
        if excluded:
            run.passed(entry, AuditCheck.DEAD_CONFIGURATION)
            continue
        if item.unrecognized:
            run.add(
                Finding(
                    finding_id=f"dead-configuration:{item.target.key}:{item.item_id}",
                    check=AuditCheck.DEAD_CONFIGURATION,
                    outcome=CheckOutcome.UNKNOWN,
                    severity=Severity.LOW,
                    summary=f"{item.label} carries fields this collector does not recognize",
                    item_ids=(item.item_id,),
                    targets=(item.target,),
                    evidence=(
                        EvidenceRecord(
                            level=EvidenceLevel.UNKNOWN,
                            method="unrecognized-fields",
                            detail=", ".join(sorted(item.unrecognized)),
                            source_reference=item.effective_source.reference,
                            missing_evidence=("collector support for these fields",),
                        ),
                    ),
                    missing_evidence=("collector support for these fields",),
                )
            )
            continue
        if item.kind is SourceKind.HOOK:
            match = _HOOK_EVENT.search(item.item_id)
            event = match.group("event") if match else ""
            if harness is Harness.CLAUDE and event and event not in CLAUDE_HOOK_EVENTS:
                _dead(
                    run,
                    entry,
                    f"{item.label}: {event} is not a documented lifecycle event, so the hook"
                    " is registered but can never fire",
                    Severity.MEDIUM,
                )
                continue
        method = item.evidence.method
        if entry.level is EvidenceLevel.INSTALLED_ONLY and (
            method.startswith("invocation-") or "disabled" in method
        ):
            _dead(
                run,
                entry,
                f"{item.label}: present on disk but unreachable for Discord-launched sessions —"
                f" {item.evidence.detail}",
                Severity.LOW,
            )
            continue
        if item.kind is SourceKind.COMMAND and entry.file is not None:
            name = _command_name(entry.file.path)
            commands[(item.target.key, name)].append(entry)
            continue
        run.passed(entry, AuditCheck.DEAD_CONFIGURATION)

    for (_target_key, name), entries in sorted(commands.items()):
        if len(entries) < 2:
            for entry in entries:
                run.passed(entry, AuditCheck.DEAD_CONFIGURATION)
            continue
        project = [e for e in entries if e.item.scope is Scope.PROJECT]
        globals_ = [e for e in entries if e.item.scope is Scope.GLOBAL]
        if not (project and globals_):
            for entry in entries:
                run.passed(entry, AuditCheck.DEAD_CONFIGURATION)
            continue
        for entry in globals_:
            _dead(
                run,
                entry,
                f"{entry.item.label}: /{name} is shadowed by the project command"
                f" {project[0].item.effective_source.reference} whenever that project is open",
                Severity.LOW,
            )
        for entry in project:
            run.passed(entry, AuditCheck.DEAD_CONFIGURATION)


def _dead(run: _Run, entry: _Item, summary: str, severity: Severity) -> None:
    item = entry.item
    run.add(
        Finding(
            finding_id=f"dead-configuration:{item.target.key}:{item.item_id}",
            check=AuditCheck.DEAD_CONFIGURATION,
            outcome=CheckOutcome.FAIL,
            severity=severity,
            summary=summary,
            item_ids=(item.item_id,),
            targets=(item.target,),
            evidence=(run.local(entry, item.evidence.method, item.evidence.detail, vendor=True),),
            vendor_sources=run.cite(item.target.harness, AuditCheck.DEAD_CONFIGURATION),
        )
    )


def _command_name(path: str) -> str:
    marker = "/commands/"
    index = path.rfind(marker)
    tail = path[index + len(marker) :] if index >= 0 else path.rsplit("/", 1)[-1]
    return tail[:-3] if tail.endswith(".md") else tail


# --------------------------------------------------------------------------- #
# Aggregated passes
# --------------------------------------------------------------------------- #


def _emit_passes(run: _Run) -> None:
    by_key = {entry.item.item_id: entry for entry in run.items}
    for (target_key, check), ids in sorted(
        run.passes.items(), key=lambda p: (p[0][0], p[0][1].value)
    ):
        target = AuditTarget.from_key(target_key)
        unique = sorted(set(ids))
        if check is AuditCheck.LOAD_BEHAVIOR:
            _emit_load_passes(run, target, [by_key[i] for i in unique])
            continue
        if check is AuditCheck.DUPLICATION and len(unique) < 2:
            continue  # the schema requires two items to compare; one item has nothing to fail
        run.add(
            Finding(
                finding_id=f"{check.value}:{target_key}:pass",
                check=check,
                outcome=CheckOutcome.PASS,
                severity=Severity.INFO,
                summary=f"{target_key}: {len(unique)} item(s) pass the {check.value} check",
                item_ids=tuple(unique),
                targets=(target,),
                evidence=(
                    EvidenceRecord(
                        level=EvidenceLevel.CONFIGURED,
                        method="rule-evaluation",
                        detail=f"{check.value} evaluated against each item's local evidence",
                        source_reference=f"{target_key} inventory",
                    ),
                ),
                vendor_sources=(),
                comparison_method="salted sha256 of file bytes"
                if check is AuditCheck.DUPLICATION
                else "",
            )
        )


def _emit_load_passes(run: _Run, target: AuditTarget, entries: Iterable[_Item]) -> None:
    buckets: dict[EvidenceLevel, list[_Item]] = defaultdict(list)
    for entry in entries:
        buckets[entry.level].append(entry)
    descriptions: Mapping[EvidenceLevel, str] = {
        EvidenceLevel.LOADED: "proven loaded into the session",
        EvidenceLevel.CONFIGURED: "configured in a read location; no session proves loading",
        EvidenceLevel.INSTALLED_ONLY: "installed but not loaded; not counted toward context",
    }
    for level in (EvidenceLevel.LOADED, EvidenceLevel.CONFIGURED, EvidenceLevel.INSTALLED_ONLY):
        members = buckets.get(level)
        if not members:
            continue
        vendor = any(m.item.evidence.depends_on_vendor_behavior for m in members)
        run.add(
            Finding(
                finding_id=f"load-behavior:{target.key}:{level.value}",
                check=AuditCheck.LOAD_BEHAVIOR,
                outcome=CheckOutcome.PASS,
                severity=Severity.INFO,
                summary=f"{target.key}: {len(members)} item(s) {descriptions[level]}",
                item_ids=tuple(m.item.item_id for m in members),
                targets=(target,),
                evidence=tuple(m.item.evidence for m in members),
                vendor_sources=run.cite(target.harness, AuditCheck.LOAD_BEHAVIOR) if vendor else (),
            )
        )
