"""Parity tests (task 3.3).

Parity compares canonical identity and required outcomes — never byte-identical
files.  Portable behaviour (global/project instructions, skills, the bot
addition) must reach both harnesses on both machines from one canonical
source; harness adapters (settings, hooks, permissions) and machine overlays
(subscription, models, installed tools) are named exceptions, and an
unavailable target is a gap, not a parity failure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.discovery import DiscoveryRoots, discover
from extensions.harness_audit.models import (
    AuditCheck,
    AuditTarget,
    CheckOutcome,
    ExceptionKind,
    Harness,
    HarnessInventory,
    Machine,
)
from extensions.harness_audit.parity import (
    MachineOverlay,
    ParityStatus,
    check_parity,
    portable_identity,
)
from tests.harness_audit_fixtures import (
    GLOBAL_RULES,
    claude_invocation,
    codex_invocation,
    fake_home,
    manifest,
    write,
)

COLLECTED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
IMAC_CODEX = AuditTarget(Machine.IMAC, Harness.CODEX)


def inventories_for(
    roots: DiscoveryRoots, machine: Machine, *, claude_model: str = "opus"
) -> tuple[HarnessInventory, HarnessInventory]:
    assert roots.project_dir is not None
    discovery = discover(roots, salt="shared")
    claude_args = claude_invocation(roots.project_dir, "--model", claude_model)
    claude = collect_claude(
        discovery,
        machine=machine,
        invocation=ClaudeInvocation.from_dict(claude_args),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="shared",
    )
    codex = collect_codex(
        discovery,
        machine=machine,
        invocation=CodexInvocation.from_dict(codex_invocation(roots.project_dir)),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="shared",
    )
    return claude.inventory, codex.inventory


def imac_home(tmp_path: Path) -> DiscoveryRoots:
    """The iMac: same canonical rules, a smaller subscription, no GitHub MCP."""
    roots = fake_home(tmp_path / "imac", with_transcripts=True)
    write(
        roots.codex_home / "config.toml",
        'model = "gpt-5-mini"\nmodel_reasoning_effort = "medium"\n',
    )
    write(roots.home / ".claude.json", json.dumps({"mcpServers": {}}))
    return roots


def imac_overlay() -> MachineOverlay:
    return MachineOverlay.from_dict(
        {
            "machine": "imac",
            "exceptions": [
                {
                    "exception_id": "imac-subscription",
                    "kind": "subscription",
                    "description": "the iMac subscription cannot use the larger models",
                    "preserved_outcome": "the same rules, skills and project guidance load",
                    "covers": ["setting:model", "setting:model_reasoning_effort"],
                },
                {
                    "exception_id": "imac-no-github-mcp",
                    "kind": "installed-tool",
                    "description": "gh-mcp is not installed on the iMac",
                    "preserved_outcome": "GitHub work is done through the gh CLI there",
                    "covers": ["connector:github"],
                },
            ],
        }
    )


def four_targets(tmp_path: Path) -> list[HarnessInventory]:
    drewai = inventories_for(fake_home(tmp_path / "drewai", with_transcripts=True), Machine.DREWAI)
    imac = inventories_for(imac_home(tmp_path), Machine.IMAC, claude_model="sonnet")
    return [*drewai, *imac]


def results_for(report, identity: str):
    return [r for r in report.results if r.identity == identity]


# --------------------------------------------------------------------------- #
# Aligned canonical behaviour
# --------------------------------------------------------------------------- #


def test_the_canonical_global_rules_are_aligned_across_all_four_targets(tmp_path: Path) -> None:
    report = check_parity(four_targets(tmp_path), overlays=(imac_overlay(),))
    assert report.complete
    global_rules = results_for(report, "global-instructions")
    assert global_rules
    assert all(r.status is ParityStatus.ALIGNED for r in global_rules)
    compared = {t.key for r in global_rules for t in r.targets}
    assert compared == {"drewai/claude", "drewai/codex", "imac/claude", "imac/codex"}
    # The only drift in the fixture is real: ~/.codex/skills is a directory Claude never scans.
    failing = [f for f in report.findings if f.outcome is CheckOutcome.FAIL]
    assert failing and all("skill:release" in f.summary for f in failing)
    assert all("link the canonical source" in f.summary for f in failing)


def test_portable_identity_ignores_harness_file_format(tmp_path: Path) -> None:
    claude, codex = inventories_for(fake_home(tmp_path, with_transcripts=True), Machine.DREWAI)
    claude_global = claude.get("claude/global-instructions/~/.claude/CLAUDE.md")
    codex_global = codex.get("codex/global-instructions/~/.codex/AGENTS.md")
    assert claude_global is not None and codex_global is not None
    assert (
        portable_identity(claude_global) == portable_identity(codex_global) == "global-instructions"
    )
    deploy = claude.get("claude/skill/~/.claude/skills/deploy/SKILL.md")
    assert deploy is not None and portable_identity(deploy) == "skill:deploy"


# --------------------------------------------------------------------------- #
# Exceptions are not failures
# --------------------------------------------------------------------------- #


def test_model_and_subscription_differences_are_exceptions_not_failures(tmp_path: Path) -> None:
    report = check_parity(four_targets(tmp_path), overlays=(imac_overlay(),))
    model = results_for(report, "setting:model")
    assert model and all(r.status is ParityStatus.EXCEPTION for r in model)
    kinds = {e.kind for e in report.exceptions}
    assert ExceptionKind.SUBSCRIPTION in kinds
    assert all(e.is_parity_failure is False for e in report.exceptions)
    failing = [f for f in report.findings if f.outcome is CheckOutcome.FAIL]
    assert not any("setting:model" in f.summary for f in failing)
    assert not any("fabricat" in f.summary for f in failing)


def test_an_undeclared_model_difference_is_still_an_exception(tmp_path: Path) -> None:
    report = check_parity(four_targets(tmp_path))
    model = results_for(report, "setting:model")
    assert model and all(r.status is ParityStatus.EXCEPTION for r in model)
    auto = [e for e in report.exceptions if e.kind is ExceptionKind.MODEL_AVAILABILITY]
    assert auto and all("does not recommend" in e.description for e in auto)


def test_a_missing_tool_is_an_exception_only_when_declared(tmp_path: Path) -> None:
    declared = check_parity(four_targets(tmp_path), overlays=(imac_overlay(),))
    github = results_for(declared, "connector:github")
    assert github and all(r.status is ParityStatus.EXCEPTION for r in github)
    assert all(r.exception_id == "imac-no-github-mcp" for r in github)

    undeclared = check_parity(four_targets(tmp_path))
    github = results_for(undeclared, "connector:github")
    assert github and any(r.status is ParityStatus.DRIFT for r in github)
    failing = [f for f in undeclared.findings if f.outcome is CheckOutcome.FAIL]
    assert any("connector:github" in f.summary for f in failing)
    assert all(f.check is AuditCheck.PARITY for f in failing)


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #


def test_two_maintained_copies_that_differ_recommend_one_canonical_source(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    assert roots.project_dir is not None
    write(roots.project_dir / "AGENTS.md", "# Relay project\n\nUse uv. Codex-only tweak.\n")
    report = check_parity(inventories_for(roots, Machine.DREWAI))
    project = results_for(report, "project-instructions")
    assert project and project[0].status is ParityStatus.DRIFT
    failing = [f for f in report.findings if f.outcome is CheckOutcome.FAIL]
    drifted = [f for f in failing if "project-instructions" in f.summary]
    assert drifted
    summary = drifted[0].summary
    assert "canonical" in summary and "adapter" in summary and "risk" in summary.lower()
    assert set(drifted[0].item_ids) == {
        "claude/project-instructions/~/projects/relay/CLAUDE.md",
        "codex/project-instructions/~/projects/relay/AGENTS.md",
    }


def test_canonical_drift_between_machines_is_reported(tmp_path: Path) -> None:
    drewai = inventories_for(fake_home(tmp_path / "drewai", with_transcripts=True), Machine.DREWAI)
    imac_roots = imac_home(tmp_path)
    write(imac_roots.home / "AGENTS.md", GLOBAL_RULES + "\nExtra iMac-only rule.\n")
    imac = inventories_for(imac_roots, Machine.IMAC)
    report = check_parity([*drewai, *imac], overlays=(imac_overlay(),))
    drift = [
        r for r in results_for(report, "global-instructions") if r.status is ParityStatus.DRIFT
    ]
    assert drift
    assert any({t.machine for t in r.targets} == {Machine.DREWAI, Machine.IMAC} for r in drift)


# --------------------------------------------------------------------------- #
# Coverage gaps
# --------------------------------------------------------------------------- #


def test_a_missing_target_is_a_gap_and_never_a_parity_conclusion(tmp_path: Path) -> None:
    three = four_targets(tmp_path)[:3]
    report = check_parity(three, overlays=(imac_overlay(),))
    assert not report.complete
    assert [t.key for t in report.missing_targets] == ["imac/codex"]
    unknown = [r for r in report.results if r.status is ParityStatus.UNKNOWN]
    assert unknown and all(IMAC_CODEX in r.targets for r in unknown)
    assert not any(
        r.status is ParityStatus.DRIFT and IMAC_CODEX in r.targets for r in report.results
    )
    gap_findings = [f for f in report.findings if f.outcome is CheckOutcome.UNKNOWN]
    assert gap_findings and all(f.missing_evidence for f in gap_findings)


def test_report_is_deterministic_and_serializable(tmp_path: Path) -> None:
    once = check_parity(four_targets(tmp_path), overlays=(imac_overlay(),)).to_dict()
    twice = check_parity(four_targets(tmp_path), overlays=(imac_overlay(),)).to_dict()
    assert json.dumps(once) == json.dumps(twice)
    assert "Always run the tests" not in json.dumps(once)
