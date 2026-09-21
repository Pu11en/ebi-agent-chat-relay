"""Professional harness configuration audit for Discord-launched Claude and Codex.

Local operational tooling under ``extensions/``: it inventories what each
harness actually loads on DrewAI and the iMac, applies deterministic checks
backed by pinned vendor guidance, classifies every item, compares behavioural
parity, and drives a reversible quarantine.  It never runs a model, never
spawns a CLI, and — outside the explicit quarantine step — never writes into
a harness directory.  See ``docs/harness-configuration-audit.md``.

Pipeline::

    discover → collect_claude / collect_codex → run_rules → classify
             → build_redacted_bundle → check_parity → build_report
             → plan_quarantine → apply_quarantine → verify → rollback

Run it with ``python -m extensions.harness_audit.cli --help``.
"""

from __future__ import annotations

from extensions.harness_audit.classify import choose_verdict, classify
from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.collection import CollectionResult, Invocation
from extensions.harness_audit.discovery import DiscoveryResult, DiscoveryRoots, discover
from extensions.harness_audit.models import (
    AuditTarget,
    Classification,
    ClassificationRecord,
    CoverageGap,
    EvidenceLevel,
    Finding,
    Harness,
    HarnessInventory,
    InventoryItem,
    Machine,
    MachineException,
    SchemaError,
)
from extensions.harness_audit.parity import MachineOverlay, ParityReport, check_parity
from extensions.harness_audit.quarantine import (
    QuarantineManifest,
    apply_quarantine,
    plan_quarantine,
    removal_eligibility,
    rollback_quarantine,
)
from extensions.harness_audit.redaction import (
    RedactedBundle,
    build_redacted_bundle,
    parse_bundle,
    serialize_bundle,
)
from extensions.harness_audit.report import AuditReport, build_report, render_json, render_text
from extensions.harness_audit.rules import run_rules
from extensions.harness_audit.sources import SourceManifest, load_manifest

__all__ = [
    "AuditReport",
    "AuditTarget",
    "Classification",
    "ClassificationRecord",
    "ClaudeInvocation",
    "CodexInvocation",
    "CollectionResult",
    "CoverageGap",
    "DiscoveryResult",
    "DiscoveryRoots",
    "EvidenceLevel",
    "Finding",
    "Harness",
    "HarnessInventory",
    "InventoryItem",
    "Invocation",
    "Machine",
    "MachineException",
    "MachineOverlay",
    "ParityReport",
    "QuarantineManifest",
    "RedactedBundle",
    "SchemaError",
    "SourceManifest",
    "apply_quarantine",
    "build_redacted_bundle",
    "build_report",
    "check_parity",
    "choose_verdict",
    "classify",
    "collect_claude",
    "collect_codex",
    "discover",
    "load_manifest",
    "parse_bundle",
    "plan_quarantine",
    "removal_eligibility",
    "render_json",
    "render_text",
    "rollback_quarantine",
    "run_rules",
    "serialize_bundle",
]
