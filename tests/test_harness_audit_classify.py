"""Classification tests (task 3.2).

Every audited item gets exactly one of the five verdicts, chosen by a fixed
precedence over the failing checks, and incomplete evidence can never produce
``Remove`` — an item nobody can prove is unused stays ``Keep`` or ``Fix`` with
the uncertainty written down.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

from extensions.harness_audit.classify import VERDICT_PRECEDENCE, choose_verdict, classify
from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.discovery import DiscoveryRoots, discover
from extensions.harness_audit.models import (
    AuditCheck,
    Classification,
    ClassificationRecord,
    EvidenceLevel,
    Machine,
    Scope,
    SourceKind,
)
from extensions.harness_audit.rules import LARGE_CONTEXT_BYTES, run_rules
from tests.harness_audit_fixtures import (
    add_claude_transcript,
    claude_invocation,
    codex_invocation,
    fake_home,
    manifest,
    write,
)

COLLECTED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def collect_both(roots: DiscoveryRoots, *, claude_version: str = "2.0.5"):
    assert roots.project_dir is not None
    discovery = discover(roots, salt="t")
    claude_record = ClaudeInvocation.from_dict(claude_invocation(roots.project_dir))
    claude_record = ClaudeInvocation(
        claude_record.argv, claude_record.cwd, claude_record.environment_names, claude_version
    )
    claude = collect_claude(
        discovery,
        machine=Machine.DREWAI,
        invocation=claude_record,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    codex = collect_codex(
        discovery,
        machine=Machine.DREWAI,
        invocation=CodexInvocation.from_dict(codex_invocation(roots.project_dir)),
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    return (claude, codex)


def classify_home(roots: DiscoveryRoots, **kwargs: str) -> tuple[ClassificationRecord, ...]:
    results = collect_both(roots, **kwargs)
    return classify(results, run_rules(results, manifest=manifest()))


def record(records: tuple[ClassificationRecord, ...], suffix: str) -> ClassificationRecord:
    for entry in records:
        if entry.item_id.endswith(suffix):
            return entry
    raise AssertionError(f"no record ends with {suffix!r}; have {[r.item_id for r in records]}")


# --------------------------------------------------------------------------- #
# Exactly one verdict per item
# --------------------------------------------------------------------------- #


def test_every_item_gets_exactly_one_verdict_with_reason_evidence_and_action(
    tmp_path: Path,
) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    results = collect_both(roots)
    records = classify(results, run_rules(results, manifest=manifest()))
    expected = {item.item_id for result in results for item in result.inventory.items}
    assert {entry.item_id for entry in records} == expected
    assert len(records) == len(expected)
    for entry in records:
        assert entry.classification in Classification
        assert entry.reason and entry.risk and entry.reversible_action
        assert entry.evidence and entry.affected_targets
        assert entry.proposed_scope in Scope
    serialized = json.dumps([entry.to_dict() for entry in records])
    assert "Always run the tests" not in serialized


def test_classification_is_deterministic(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    once = [entry.to_dict() for entry in classify_home(roots)]
    twice = [entry.to_dict() for entry in classify_home(roots)]
    assert once == twice


# --------------------------------------------------------------------------- #
# The verdicts
# --------------------------------------------------------------------------- #


def test_project_specific_global_content_moves_to_the_project(tmp_path: Path) -> None:
    records = classify_home(fake_home(tmp_path, with_transcripts=True))
    skill = record(records, "claude/skill/~/.claude/skills/deploy/SKILL.md")
    assert skill.classification is Classification.MOVE_TO_PROJECT
    assert skill.proposed_scope is Scope.PROJECT
    assert "ebi-agent-chat-relay" in skill.reason
    assert skill.finding_ids


def test_large_session_start_content_loads_only_when_needed(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    assert roots.project_dir is not None
    write(
        roots.project_dir / "CLAUDE.md",
        "# Big\n" + ("x" * 79 + "\n") * (LARGE_CONTEXT_BYTES // 80 + 5),
    )
    entry = record(classify_home(roots), "claude/project-instructions/~/projects/relay/CLAUDE.md")
    assert entry.classification is Classification.LOAD_ON_DEMAND
    assert entry.activation_boundary
    assert "skill" in entry.activation_boundary.lower()


def test_two_maintained_copies_are_fixed_not_removed(tmp_path: Path) -> None:
    records = classify_home(fake_home(tmp_path, with_transcripts=True))
    claude_copy = record(records, "claude/project-instructions/~/projects/relay/CLAUDE.md")
    codex_copy = record(records, "codex/project-instructions/~/projects/relay/AGENTS.md")
    assert claude_copy.classification is Classification.FIX
    assert codex_copy.classification is Classification.FIX
    assert "canonical" in claude_copy.reason


def test_configuration_that_can_never_take_effect_is_a_remove_candidate(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    write(
        roots.claude_home / "settings.json",
        json.dumps({"hooks": {"OnSave": [{"matcher": "", "hooks": [{"command": "x"}]}]}}),
    )
    write(
        roots.codex_home / "config.toml",
        'model = "gpt-5-codex"\n[mcp_servers.old]\ncommand = "old"\nenabled = false\n',
    )
    records = classify_home(roots)
    hook = record(records, "#OnSave[*]")
    assert hook.classification is Classification.REMOVE
    assert "quarantine" in hook.reversible_action.lower()
    assert not hook.uncertainty
    server = record(records, "config.toml#old")
    assert server.classification is Classification.REMOVE


def test_shadowed_configuration_is_fixed_rather_than_removed(tmp_path: Path) -> None:
    records = classify_home(fake_home(tmp_path, with_transcripts=True))
    shadowed = record(records, "claude/command/~/.claude/commands/verify.md")
    assert shadowed.classification is Classification.FIX
    assert "shadow" in shadowed.reason.lower()


def test_model_and_reasoning_settings_are_kept_and_excluded(tmp_path: Path) -> None:
    records = classify_home(fake_home(tmp_path, with_transcripts=True))
    effort = record(records, "codex/harness-setting/model_reasoning_effort")
    assert effort.classification is Classification.KEEP
    assert "excluded" in effort.reason
    model = record(records, "claude/harness-setting/invocation#model")
    assert model.classification is Classification.KEEP


def test_clean_items_are_kept(tmp_path: Path) -> None:
    records = classify_home(fake_home(tmp_path, with_transcripts=True))
    memory = record(records, "claude/global-instructions/~/.claude/CLAUDE.md")
    assert memory.classification is Classification.KEEP
    assert memory.evidence[0].level is EvidenceLevel.LOADED


# --------------------------------------------------------------------------- #
# Incomplete evidence can never produce Remove
# --------------------------------------------------------------------------- #


def test_a_remove_candidate_with_unknown_evidence_becomes_fix_with_uncertainty(
    tmp_path: Path,
) -> None:
    roots = fake_home(tmp_path, with_transcripts=False)
    assert roots.project_dir is not None
    write(
        roots.claude_home / "settings.json",
        json.dumps({"hooks": {"OnSave": [{"matcher": "", "hooks": [{"command": "x"}]}]}}),
    )
    add_claude_transcript(roots.home, roots.project_dir, version="1.0.0")
    records = classify_home(roots, claude_version="1.0.0")
    hook = record(records, "#OnSave[*]")
    assert hook.evidence[0].level is EvidenceLevel.UNKNOWN
    assert hook.classification is Classification.FIX
    assert hook.uncertainty
    assert all(entry.classification is not Classification.REMOVE for entry in records)


def test_choose_verdict_never_returns_remove_on_incomplete_evidence() -> None:
    checks = list(AuditCheck)
    for size in range(len(checks) + 1):
        for failing in itertools.combinations(checks, size):
            for removable in (False, True):
                verdict = choose_verdict(
                    failing=frozenset(failing),
                    incomplete=True,
                    excluded=False,
                    removable=removable,
                    kind=SourceKind.HOOK,
                    multi_source=False,
                )
                assert verdict is not Classification.REMOVE, failing
    assert (
        choose_verdict(
            failing=frozenset({AuditCheck.DEAD_CONFIGURATION}),
            incomplete=False,
            excluded=False,
            removable=True,
            kind=SourceKind.HOOK,
            multi_source=False,
        )
        is Classification.REMOVE
    )


def test_precedence_is_fixed_and_the_most_specific_check_wins() -> None:
    assert VERDICT_PRECEDENCE[0] is AuditCheck.DEAD_CONFIGURATION
    verdict = choose_verdict(
        failing=frozenset(
            {AuditCheck.PROJECT_CONTENT_IS_GLOBAL, AuditCheck.SIZE, AuditCheck.DUPLICATION}
        ),
        incomplete=False,
        excluded=False,
        removable=False,
        kind=SourceKind.GLOBAL_INSTRUCTIONS,
        multi_source=False,
    )
    assert verdict is Classification.MOVE_TO_PROJECT
    verdict = choose_verdict(
        failing=frozenset({AuditCheck.SIZE, AuditCheck.DUPLICATION}),
        incomplete=False,
        excluded=False,
        removable=False,
        kind=SourceKind.GLOBAL_INSTRUCTIONS,
        multi_source=False,
    )
    assert verdict is Classification.LOAD_ON_DEMAND
    assert (
        choose_verdict(
            failing=frozenset({AuditCheck.SIZE}),
            incomplete=False,
            excluded=True,
            removable=False,
            kind=SourceKind.HARNESS_SETTING,
            multi_source=True,
        )
        is Classification.KEEP
    )
