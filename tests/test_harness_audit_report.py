"""Report tests (task 4.1).

The report renders the same evidence two ways — machine-readable JSON and
plain language — and both say the same thing: which targets were covered,
what each one loads, what it costs in bytes (with a clearly labeled token
estimate), what the checks found, what verdict each item got, and where
parity holds.  A missing target is plainly marked partial, never smoothed
over.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from extensions.harness_audit.classify import classify
from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.discovery import DiscoveryRoots, discover
from extensions.harness_audit.models import AuditTarget, Harness, Machine
from extensions.harness_audit.parity import check_parity
from extensions.harness_audit.redaction import (
    RedactedBundle,
    build_redacted_bundle,
    parse_bundle,
    serialize_bundle,
)
from extensions.harness_audit.report import AuditReport, build_report, render_json, render_text
from extensions.harness_audit.rules import run_rules
from tests.harness_audit_fixtures import (
    FAKE_KEY,
    FAKE_SYSTEM_PROMPT,
    claude_invocation,
    codex_invocation,
    fake_home,
    manifest,
    write,
)

COLLECTED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
ALL_TARGET_KEYS = {"drewai/claude", "drewai/codex", "imac/claude", "imac/codex"}


def collect_machine(roots: DiscoveryRoots, machine: Machine) -> list[CollectionResult]:
    assert roots.project_dir is not None
    discovery = discover(roots, salt="shared")
    claude = collect_claude(
        discovery,
        machine=machine,
        invocation=ClaudeInvocation.from_dict(claude_invocation(roots.project_dir)),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
    )
    codex = collect_codex(
        discovery,
        machine=machine,
        invocation=CodexInvocation.from_dict(codex_invocation(roots.project_dir)),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="shared",
    )
    return [claude, codex]


def bundle_for(results: list[CollectionResult], machine: Machine) -> RedactedBundle:
    findings = run_rules(results, manifest=manifest())
    classifications = classify(results, findings)
    bundle = build_redacted_bundle(
        machine=machine,
        created_at=COLLECTED_AT,
        inventories=[r.inventory for r in results],
        findings=findings,
        classifications=classifications,
        coverage_gaps=[r.coverage_gap for r in results if r.coverage_gap is not None],
        private_bodies=[body for r in results for body in r.private_bodies],
        environment={name: "" for r in results for name in r.environment},
        salt="shared",
    )
    return parse_bundle(serialize_bundle(bundle))


def two_machines(tmp_path: Path) -> tuple[RedactedBundle, RedactedBundle]:
    drewai = bundle_for(
        collect_machine(fake_home(tmp_path / "drewai", with_transcripts=True), Machine.DREWAI),
        Machine.DREWAI,
    )
    imac_roots = fake_home(tmp_path / "imac", with_transcripts=True)
    write(imac_roots.codex_home / "config.toml", 'model = "gpt-5-mini"\n')
    imac = bundle_for(collect_machine(imac_roots, Machine.IMAC), Machine.IMAC)
    return drewai, imac


def report_for(bundles: tuple[RedactedBundle, ...]) -> AuditReport:
    inventories = [inv for b in bundles for inv in b.inventories]
    return build_report(
        bundles,
        parity=check_parity(inventories),
        generated_at=COLLECTED_AT,
    )


# --------------------------------------------------------------------------- #
# Complete four-target report
# --------------------------------------------------------------------------- #


def test_four_target_report_is_complete_and_names_every_target(tmp_path: Path) -> None:
    report = report_for(two_machines(tmp_path))
    assert not report.is_partial
    assert {t.key for t in report.covered_targets} == ALL_TARGET_KEYS
    assert report.coverage_gaps == ()
    text = render_text(report)
    assert "COMPLETE" in text
    for key in ALL_TARGET_KEYS:
        assert f"== Inventory: {key} ==" in text
    payload = json.loads(render_json(report))
    assert payload["coverage"]["complete"] is True
    assert {entry["target"] for entry in payload["inventories"]} == ALL_TARGET_KEYS
    assert payload["verdicts"] and payload["findings"] and payload["parity"]["results"]


def test_report_separates_loaded_from_installed_only_and_measures_token_impact(
    tmp_path: Path,
) -> None:
    report = report_for(two_machines(tmp_path))
    text = render_text(report)
    assert "[loaded]" in text and "[installed-only]" in text
    assert "not counted toward effective context" in text
    assert "== Token impact ==" in text
    assert "estimated" in text and "not provider billing data" in text
    payload = json.loads(render_json(report))
    impact = {entry["target"]: entry for entry in payload["token_impact"]}
    assert set(impact) == ALL_TARGET_KEYS
    for entry in impact.values():
        assert entry["loaded_bytes"] >= 0
        assert entry["token_count_kind"] == "estimated"
        assert "not provider billing data" in entry["label"]


def test_report_lists_findings_with_evidence_and_citations_and_every_verdict(
    tmp_path: Path,
) -> None:
    report = report_for(two_machines(tmp_path))
    text = render_text(report)
    assert "== Findings ==" in text
    assert "[FAIL" in text
    assert "retrieved" in text  # a vendor citation with its date
    assert "evidence:" in text
    assert "== Verdicts ==" in text
    for verdict in ("keep", "fix", "move-to-project"):
        assert verdict in text
    payload = json.loads(render_json(report))
    item_ids = {item["item_id"] for inv in payload["inventories"] for item in inv["items"]}
    verdict_ids = {entry["item_id"] for entry in payload["verdicts"]}
    assert item_ids == verdict_ids


def test_report_shows_parity_and_exceptions(tmp_path: Path) -> None:
    report = report_for(two_machines(tmp_path))
    text = render_text(report)
    assert "== Parity ==" in text
    assert "aligned" in text and "exception" in text
    assert "== Machine exceptions ==" in text
    assert "model-availability" in text


# --------------------------------------------------------------------------- #
# Partial report
# --------------------------------------------------------------------------- #


def test_a_missing_target_is_plainly_marked_partial(tmp_path: Path) -> None:
    drewai, _ = two_machines(tmp_path)
    report = build_report(
        (drewai,), parity=check_parity(drewai.inventories), generated_at=COLLECTED_AT
    )
    assert report.is_partial
    missing = {gap.target.key for gap in report.coverage_gaps}
    assert missing == {"imac/claude", "imac/codex"}
    text = render_text(report)
    assert "PARTIAL" in text
    assert "2/4 targets" in text
    assert "imac/codex" in text and "coverage gap" in text.lower()
    payload = json.loads(render_json(report))
    assert payload["coverage"]["complete"] is False
    assert {g["target"] for g in payload["coverage"]["gaps"]} == missing
    assert all(
        AuditTarget.from_dict(t).machine is Machine.DREWAI
        for entry in payload["verdicts"]
        for t in entry["affected_targets"]
    )


def test_an_unavailable_harness_on_a_present_machine_is_a_gap(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    results = collect_machine(roots, Machine.DREWAI)[:1]  # only Claude collected
    bundle = build_redacted_bundle(
        machine=Machine.DREWAI,
        created_at=COLLECTED_AT,
        inventories=[results[0].inventory],
        salt="shared",
    )
    report = build_report((bundle,), parity=None, generated_at=COLLECTED_AT)
    assert AuditTarget(Machine.DREWAI, Harness.CODEX) in {g.target for g in report.coverage_gaps}
    assert "drewai/codex" in render_text(report)
    assert "== Parity ==" in render_text(report)
    assert "not run" in render_text(report)


# --------------------------------------------------------------------------- #
# Safety and determinism
# --------------------------------------------------------------------------- #


def test_rendered_report_carries_no_secret_or_private_body(tmp_path: Path) -> None:
    report = report_for(two_machines(tmp_path))
    for rendered in (render_text(report), render_json(report)):
        assert FAKE_KEY not in rendered
        assert FAKE_SYSTEM_PROMPT not in rendered
        assert "Always run the tests" not in rendered
        assert "private prompt text" not in rendered


def test_rendering_is_deterministic(tmp_path: Path) -> None:
    first = report_for(two_machines(tmp_path / "a"))
    second = report_for(two_machines(tmp_path / "b"))
    assert render_json(first) == render_json(second)
    assert render_text(first) == render_text(second)
