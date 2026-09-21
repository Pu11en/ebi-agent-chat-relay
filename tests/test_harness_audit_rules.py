"""Deterministic rule tests (task 3.1).

Every finding must cite local evidence (a source reference on the machine)
and, when the verdict depends on documented vendor behaviour, an official
pinned source.  The checks are pure functions of the collected inventories:
no model, no filesystem, no network, and the same input yields the same bytes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.discovery import DiscoveryRoots, discover
from extensions.harness_audit.models import (
    AuditCheck,
    AuditTarget,
    CheckOutcome,
    Finding,
    Harness,
    Machine,
    Severity,
)
from extensions.harness_audit.rules import LARGE_CONTEXT_BYTES, run_rules
from extensions.harness_audit.sources import VENDOR_BACKED_CHECKS
from tests.harness_audit_fixtures import (
    PROJECT_RULES,
    claude_invocation,
    codex_invocation,
    fake_home,
    manifest,
    write,
)

COLLECTED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
DREWAI_CLAUDE = AuditTarget(Machine.DREWAI, Harness.CLAUDE)
DREWAI_CODEX = AuditTarget(Machine.DREWAI, Harness.CODEX)


def collect_both(
    roots: DiscoveryRoots, *, claude_extra: tuple[str, ...] = (), codex_extra: tuple[str, ...] = ()
) -> tuple[CollectionResult, CollectionResult]:
    assert roots.project_dir is not None
    discovery = discover(roots, salt="t")
    claude = collect_claude(
        discovery,
        machine=Machine.DREWAI,
        invocation=ClaudeInvocation.from_dict(claude_invocation(roots.project_dir, *claude_extra)),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
    )
    codex = collect_codex(
        discovery,
        machine=Machine.DREWAI,
        invocation=CodexInvocation.from_dict(codex_invocation(roots.project_dir, *codex_extra)),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    return claude, codex


def findings_for(
    findings: tuple[Finding, ...], check: AuditCheck, *, outcome: CheckOutcome | None = None
):
    return [
        finding
        for finding in findings
        if finding.check is check and (outcome is None or finding.outcome is outcome)
    ]


def mentioning(findings: list[Finding], fragment: str) -> list[Finding]:
    return [f for f in findings if any(fragment in item_id for item_id in f.item_ids)]


# --------------------------------------------------------------------------- #
# The contract every finding keeps
# --------------------------------------------------------------------------- #


def test_every_finding_cites_local_evidence_and_official_guidance_when_required(
    tmp_path: Path,
) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(collect_both(roots), manifest=manifest())
    assert findings
    for finding in findings:
        assert finding.evidence, finding.finding_id
        assert all(record.source_reference for record in finding.evidence), finding.finding_id
        assert finding.targets
        if any(record.depends_on_vendor_behavior for record in finding.evidence):
            assert finding.vendor_sources, finding.finding_id
        if finding.check in VENDOR_BACKED_CHECKS and finding.outcome is CheckOutcome.FAIL:
            assert finding.vendor_sources, finding.finding_id
            assert all(c.retrieved_on for c in finding.vendor_sources)
        if finding.outcome is CheckOutcome.UNKNOWN:
            assert finding.missing_evidence, finding.finding_id


def test_rules_are_deterministic_and_never_carry_content(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    once = run_rules(collect_both(roots), manifest=manifest())
    twice = run_rules(collect_both(roots), manifest=manifest())
    assert [f.to_dict() for f in once] == [f.to_dict() for f in twice]
    serialized = json.dumps([f.to_dict() for f in once])
    assert "Always run the tests" not in serialized
    assert "Use uv." not in serialized


# --------------------------------------------------------------------------- #
# Duplication
# --------------------------------------------------------------------------- #


def test_two_maintained_copies_of_the_same_rule_are_a_duplication_failure(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(collect_both(roots), manifest=manifest())
    failures = findings_for(findings, AuditCheck.DUPLICATION, outcome=CheckOutcome.FAIL)
    project_pair = mentioning(failures, "~/projects/relay/CLAUDE.md")
    assert len(project_pair) == 1
    finding = project_pair[0]
    assert any("~/projects/relay/AGENTS.md" in i for i in finding.item_ids)
    assert finding.duplicated_bytes == len(PROJECT_RULES.encode())
    assert "sha256" in finding.comparison_method
    assert set(finding.targets) == {DREWAI_CLAUDE, DREWAI_CODEX}
    assert finding.severity is Severity.HIGH


def test_a_symlink_pair_to_one_canonical_file_is_not_a_duplicate(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(collect_both(roots), manifest=manifest())
    failures = findings_for(findings, AuditCheck.DUPLICATION, outcome=CheckOutcome.FAIL)
    assert not mentioning(failures, "~/.claude/CLAUDE.md")
    passes = findings_for(findings, AuditCheck.DUPLICATION, outcome=CheckOutcome.PASS)
    canonical = mentioning(passes, "~/.claude/CLAUDE.md")
    assert canonical and "~/AGENTS.md" in canonical[0].summary


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def test_project_specific_content_in_a_global_file_recommends_a_move(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(collect_both(roots), manifest=manifest())
    failures = findings_for(
        findings, AuditCheck.PROJECT_CONTENT_IS_GLOBAL, outcome=CheckOutcome.FAIL
    )
    skill = mentioning(failures, "claude/skill/~/.claude/skills/deploy/SKILL.md")
    assert len(skill) == 1
    assert "ebi-agent-chat-relay" in skill[0].summary
    assert "move" in skill[0].summary.lower()
    assert skill[0].vendor_sources
    assert not mentioning(failures, "~/.claude/CLAUDE.md")


# --------------------------------------------------------------------------- #
# Size
# --------------------------------------------------------------------------- #


def test_size_findings_carry_exact_bytes_and_a_labeled_estimate(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(collect_both(roots), manifest=manifest())
    sizes = findings_for(findings, AuditCheck.SIZE)
    memory = mentioning(sizes, "claude/global-instructions/~/.claude/CLAUDE.md")
    assert memory
    summary = memory[0].summary
    assert "bytes" in summary and "characters" in summary
    assert "estimated" in summary and "not provider billing data" in summary
    totals = [f for f in sizes if "session-start" in f.finding_id]
    assert {t.key for f in totals for t in f.targets} == {"drewai/claude", "drewai/codex"}


def test_a_large_file_loaded_every_session_fails_the_size_check(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    assert roots.project_dir is not None
    write(
        roots.project_dir / "CLAUDE.md",
        "# Big\n" + ("x" * 79 + "\n") * (LARGE_CONTEXT_BYTES // 80 + 5),
    )
    findings = run_rules(collect_both(roots), manifest=manifest())
    failures = findings_for(findings, AuditCheck.SIZE, outcome=CheckOutcome.FAIL)
    assert mentioning(failures, "~/projects/relay/CLAUDE.md")


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


def test_world_writable_hook_settings_and_unknown_modes_are_reported(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    modes: dict[Path, int | None] = dict(roots.modes)
    modes[roots.claude_home / "settings.json"] = 0o666
    modes[roots.home / "AGENTS.md"] = None  # mode not inspectable (Windows ACLs, say)
    roots = DiscoveryRoots(
        home=roots.home,
        claude_home=roots.claude_home,
        codex_home=roots.codex_home,
        project_dir=roots.project_dir,
        known_projects=roots.known_projects,
        links=roots.links,
        modes=modes,
    )
    findings = run_rules(collect_both(roots), manifest=manifest())
    failures = findings_for(findings, AuditCheck.PERMISSIONS, outcome=CheckOutcome.FAIL)
    settings = mentioning(failures, "~/.claude/settings.json")
    assert settings and settings[0].severity is Severity.HIGH
    assert "0666" in settings[0].summary
    unknowns = findings_for(findings, AuditCheck.PERMISSIONS, outcome=CheckOutcome.UNKNOWN)
    assert unknowns, "files without a known POSIX mode are reported unknown, not passed"
    assert all(f.missing_evidence for f in unknowns)


def test_dangerous_sandbox_and_bypass_modes_fail_permissions(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(
        collect_both(
            roots,
            claude_extra=("--permission-mode", "bypassPermissions"),
            codex_extra=("--sandbox", "danger-full-access"),
        ),
        manifest=manifest(),
    )
    failures = findings_for(findings, AuditCheck.PERMISSIONS, outcome=CheckOutcome.FAIL)
    assert mentioning(failures, "invocation#permission-mode")
    assert mentioning(failures, "codex/harness-setting/sandbox_mode")


# --------------------------------------------------------------------------- #
# Precedence, load behavior, dead configuration
# --------------------------------------------------------------------------- #


def test_command_line_override_wins_and_model_settings_are_not_cleanup_targets(
    tmp_path: Path,
) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(collect_both(roots), manifest=manifest())
    precedence = mentioning(findings_for(findings, AuditCheck.PRECEDENCE), "model_reasoning_effort")
    assert precedence and precedence[0].outcome is CheckOutcome.PASS
    assert "invocation -c model_reasoning_effort" in precedence[0].summary
    dead = findings_for(findings, AuditCheck.DEAD_CONFIGURATION, outcome=CheckOutcome.FAIL)
    assert not mentioning(dead, "model_reasoning_effort")
    assert not mentioning(dead, "harness-setting/model")


def test_shadowed_command_and_unknown_hook_event_are_dead_configuration(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    write(
        roots.claude_home / "settings.json",
        json.dumps({"hooks": {"OnSave": [{"matcher": "", "hooks": [{"command": "x"}]}]}}),
    )
    findings = run_rules(collect_both(roots), manifest=manifest())
    dead = findings_for(findings, AuditCheck.DEAD_CONFIGURATION, outcome=CheckOutcome.FAIL)
    shadowed = mentioning(dead, "claude/command/~/.claude/commands/verify.md")
    assert shadowed and "~/projects/relay/.claude/commands/verify.md" in shadowed[0].summary
    assert shadowed[0].vendor_sources
    hook = mentioning(dead, "#OnSave[")
    assert hook and "OnSave" in hook[0].summary


def test_invocation_excluded_scope_is_dead_configuration(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    findings = run_rules(
        collect_both(roots, claude_extra=("--setting-sources", "project")), manifest=manifest()
    )
    dead = findings_for(findings, AuditCheck.DEAD_CONFIGURATION, outcome=CheckOutcome.FAIL)
    assert mentioning(dead, "claude/global-instructions/~/.claude/CLAUDE.md")


def test_unknown_evidence_is_reported_unknown_with_what_is_missing(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    write(roots.claude_home / "settings.local.json", "{not json")
    findings = run_rules(collect_both(roots), manifest=manifest())
    unknown = findings_for(findings, AuditCheck.LOAD_BEHAVIOR, outcome=CheckOutcome.UNKNOWN)
    local = mentioning(unknown, "settings.local.json")
    assert local and local[0].missing_evidence
    installed = findings_for(findings, AuditCheck.LOAD_BEHAVIOR, outcome=CheckOutcome.PASS)
    assert mentioning(installed, "claude/command/~/.claude/commands/verify.md")
    assert any("not counted" in f.summary for f in installed)
