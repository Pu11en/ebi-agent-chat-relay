"""Quarantine tests (task 4.2).

Quarantine is the only step of the audit that writes to a harness directory,
and it does so reversibly: a manifest records the original path, the hash,
the disabled location and the rollback action; applying verifies every hash
before moving anything; rollback puts the bytes back where they were; and
nothing is ever deleted.  Remove stays ineligible until every affected target
passes its post-quarantine checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from extensions.harness_audit.classify import classify
from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.discovery import DiscoveryRoots, discover
from extensions.harness_audit.models import (
    AuditTarget,
    Classification,
    ClassificationRecord,
    EvidenceLevel,
    EvidenceRecord,
    Harness,
    Machine,
    Scope,
)
from extensions.harness_audit.quarantine import (
    QuarantineError,
    QuarantineManifest,
    absolute_paths,
    apply_quarantine,
    plan_quarantine,
    reclassify_after_rollback,
    removal_eligibility,
    rollback_quarantine,
    target_passes,
)
from extensions.harness_audit.rules import LARGE_CONTEXT_BYTES, run_rules
from tests.harness_audit_fixtures import (
    claude_invocation,
    codex_invocation,
    fake_home,
    manifest,
    write,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
DREWAI_CLAUDE = AuditTarget(Machine.DREWAI, Harness.CLAUDE)
DREWAI_CODEX = AuditTarget(Machine.DREWAI, Harness.CODEX)


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def collect_both(roots: DiscoveryRoots) -> list[CollectionResult]:
    assert roots.project_dir is not None
    discovery = discover(roots, salt="t")
    return [
        collect_claude(
            discovery,
            machine=Machine.DREWAI,
            invocation=ClaudeInvocation.from_dict(claude_invocation(roots.project_dir)),
            manifest=manifest(),
            collected_at=NOW,
            salt="t",
        ),
        collect_codex(
            discovery,
            machine=Machine.DREWAI,
            invocation=CodexInvocation.from_dict(codex_invocation(roots.project_dir)),
            manifest=manifest(),
            collected_at=NOW,
            salt="t",
        ),
    ]


def planned(tmp_path: Path) -> tuple[DiscoveryRoots, QuarantineManifest, list[CollectionResult]]:
    roots = fake_home(tmp_path, with_transcripts=True)
    assert roots.project_dir is not None
    write(
        roots.project_dir / "CLAUDE.md",
        "# Big\n" + ("x" * 79 + "\n") * (LARGE_CONTEXT_BYTES // 80 + 5),
    )
    write(
        roots.claude_home / "settings.json",
        json.dumps({"hooks": {"OnSave": [{"matcher": "", "hooks": [{"command": "x"}]}]}}),
    )
    results = collect_both(roots)
    findings = run_rules(results, manifest=manifest())
    classifications = classify(results, findings)
    plan = plan_quarantine(
        classifications,
        absolute_paths(results, roots),
        quarantine_dir=tmp_path / "quarantine",
        machine=Machine.DREWAI,
        created_at=NOW,
    )
    return roots, plan, results


def remove_record(item_id: str, reference: str) -> ClassificationRecord:
    return ClassificationRecord(
        item_id=item_id,
        classification=Classification.REMOVE,
        reason="dead everywhere",
        evidence=(
            EvidenceRecord(
                level=EvidenceLevel.INSTALLED_ONLY,
                method="settings-disabled-server",
                detail="disabled",
                source_reference=reference,
            ),
        ),
        affected_targets=(DREWAI_CLAUDE, DREWAI_CODEX),
        proposed_scope=Scope.GLOBAL,
        risk="medium",
        reversible_action="quarantine with a manifest",
    )


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def test_plan_names_file_backed_candidates_and_skips_what_cannot_be_moved(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    ids = {entry.item_id for entry in plan.entries}
    assert "claude/skill/~/.claude/skills/deploy/SKILL.md" in ids  # move-to-project
    assert "claude/project-instructions/~/projects/relay/CLAUDE.md" in ids  # load-on-demand
    assert not any("#OnSave" in item_id for item_id in ids)
    skipped = {entry.item_id: entry.reason for entry in plan.skipped}
    assert any("#OnSave" in item_id for item_id in skipped)
    assert all("version control" in reason for reason in skipped.values())
    for entry in plan.entries:
        assert entry.content_hash == sha256(Path(entry.original_path))
        assert entry.disabled_location.startswith(str(tmp_path / "quarantine"))
        assert entry.rollback_action
        assert entry.applied_at is None
    assert plan.applied_at is None
    assert not any(e.classification is Classification.KEEP for e in plan.entries)


def test_manifest_round_trips_through_json(tmp_path: Path) -> None:
    _, plan, _ = planned(tmp_path)
    path = tmp_path / "manifest.json"
    plan.save(path)
    loaded = QuarantineManifest.load(path)
    assert loaded == plan
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_planning_writes_nothing(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    before = tree(tmp_path)
    results = collect_both(roots)
    plan_quarantine(
        classify(results, run_rules(results, manifest=manifest())),
        absolute_paths(results, roots),
        quarantine_dir=tmp_path / "quarantine",
        machine=Machine.DREWAI,
        created_at=NOW,
    )
    assert tree(tmp_path) == before
    assert not (tmp_path / "quarantine").exists()


# --------------------------------------------------------------------------- #
# Apply and rollback
# --------------------------------------------------------------------------- #


def test_apply_moves_files_reversibly_and_never_deletes(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    before = tree(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    after = tree(tmp_path)
    assert applied.applied_at == LATER
    assert sorted(before.values()) == sorted(after.values()), "every byte is still on disk"
    for entry in applied.entries:
        assert not Path(entry.original_path).exists()
        assert sha256(Path(entry.disabled_location)) == entry.content_hash
        assert entry.applied_at == LATER


def test_hash_mismatch_aborts_before_anything_moves(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    assert roots.project_dir is not None
    write(roots.project_dir / "CLAUDE.md", "# edited after planning\n")
    before = tree(tmp_path)
    with pytest.raises(QuarantineError, match="hash"):
        apply_quarantine(plan, now=LATER, roots=roots)
    assert tree(tmp_path) == before
    assert not (tmp_path / "quarantine").exists()


def test_apply_refuses_a_manifest_that_was_already_applied(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    with pytest.raises(QuarantineError, match="already applied"):
        apply_quarantine(applied, now=LATER, roots=roots)


def test_rollback_restores_every_fixture_by_hash(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    before = tree(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    restored = rollback_quarantine(applied, now=LATER, roots=roots)
    assert tree(tmp_path) == before
    assert restored.rolled_back_at == LATER
    for entry in restored.entries:
        assert sha256(Path(entry.original_path)) == entry.content_hash
        assert not Path(entry.disabled_location).exists()


def test_rollback_refuses_to_overwrite_a_changed_original(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    first = applied.entries[0]
    Path(first.original_path).write_text("someone recreated this\n", encoding="utf-8")
    with pytest.raises(QuarantineError, match="exists"):
        rollback_quarantine(applied, now=LATER, roots=roots)
    assert Path(first.disabled_location).exists()


# --------------------------------------------------------------------------- #
# The manifest is data, not authority
# --------------------------------------------------------------------------- #


def test_plan_refuses_a_display_path_that_escapes_the_quarantine_dir(tmp_path: Path) -> None:
    """A bundle's source_reference decides the disabled location; ``..`` in it
    would plan a move to anywhere on disk."""
    roots = fake_home(tmp_path)
    victim = write(roots.claude_home / "skills" / "x" / "SKILL.md", "# x\n")
    record = remove_record("claude/skill/~/.claude/skills/x/SKILL.md", "~/../../escape/SKILL.md")
    with pytest.raises(QuarantineError, match="quarantine directory"):
        plan_quarantine(
            [record],
            {record.item_id: victim},
            quarantine_dir=tmp_path / "quarantine",
            machine=Machine.DREWAI,
            created_at=NOW,
        )


def test_apply_refuses_an_original_outside_the_audited_roots(tmp_path: Path) -> None:
    """An edited manifest must not be able to move ~/.ssh/id_rsa."""
    roots, plan, _ = planned(tmp_path)
    secret = write(tmp_path / "elsewhere" / "id_rsa", "PRIVATE\n")
    first = plan.entries[0]
    hijacked = replace(
        plan,
        entries=(
            replace(
                first,
                original_path=str(secret),
                content_hash="sha256:" + hashlib.sha256(secret.read_bytes()).hexdigest(),
                byte_size=secret.stat().st_size,
            ),
            *plan.entries[1:],
        ),
    )
    before = tree(tmp_path)
    with pytest.raises(QuarantineError, match="outside"):
        apply_quarantine(hijacked, now=LATER, roots=roots)
    assert tree(tmp_path) == before
    assert secret.is_file()


def test_apply_refuses_a_disabled_location_outside_the_manifest_dir(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    first = plan.entries[0]
    hijacked = replace(
        plan,
        entries=(
            replace(first, disabled_location=str(tmp_path / "elsewhere" / "dropped")),
            *plan.entries[1:],
        ),
    )
    before = tree(tmp_path)
    with pytest.raises(QuarantineError, match="quarantine directory"):
        apply_quarantine(hijacked, now=LATER, roots=roots)
    assert tree(tmp_path) == before


def test_rollback_refuses_to_restore_into_an_unaudited_path(tmp_path: Path) -> None:
    """After apply, an edited original_path would let rollback plant a file anywhere."""
    roots, plan, _ = planned(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    first = applied.entries[0]
    hijacked = replace(
        applied,
        entries=(
            replace(first, original_path=str(tmp_path / "elsewhere" / "planted.md")),
            *applied.entries[1:],
        ),
    )
    before = tree(tmp_path)
    with pytest.raises(QuarantineError, match="outside"):
        rollback_quarantine(hijacked, now=LATER, roots=roots)
    assert tree(tmp_path) == before


def test_rollback_refuses_a_quarantined_copy_outside_the_manifest_dir(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    first = applied.entries[0]
    hijacked = replace(
        applied,
        entries=(
            replace(first, disabled_location=str(roots.home / "AGENTS.md")),
            *applied.entries[1:],
        ),
    )
    before = tree(tmp_path)
    with pytest.raises(QuarantineError, match="quarantine directory"):
        rollback_quarantine(hijacked, now=LATER, roots=roots)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("manifest_id", ["..", ".", "a/b", "..\\b", ""])
def test_a_manifest_id_is_one_plain_path_segment(tmp_path: Path, manifest_id: str) -> None:
    roots, plan, _ = planned(tmp_path)
    with pytest.raises(QuarantineError, match="manifest_id"):
        apply_quarantine(replace(plan, manifest_id=manifest_id), now=LATER, roots=roots)


# --------------------------------------------------------------------------- #
# Removal eligibility
# --------------------------------------------------------------------------- #


def test_remove_is_ineligible_until_every_affected_target_passes(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    stale = write(roots.claude_home / "commands" / "stale.md", "old command\n")
    record = remove_record(
        "claude/command/~/.claude/commands/stale.md", "~/.claude/commands/stale.md"
    )
    plan = plan_quarantine(
        (record,),
        {record.item_id: stale},
        quarantine_dir=tmp_path / "quarantine",
        machine=Machine.DREWAI,
        created_at=NOW,
    )
    assert [e.item_id for e in plan.entries] == [record.item_id]

    before = removal_eligibility(plan, {DREWAI_CLAUDE: True, DREWAI_CODEX: True})
    assert before[0].eligible is False and "not applied" in before[0].reason

    applied = apply_quarantine(plan, now=LATER, roots=roots)
    partial = removal_eligibility(applied, {DREWAI_CLAUDE: True})
    assert partial[0].eligible is False and "drewai/codex" in partial[0].reason
    failing = removal_eligibility(applied, {DREWAI_CLAUDE: True, DREWAI_CODEX: False})
    assert failing[0].eligible is False
    passing = removal_eligibility(applied, {DREWAI_CLAUDE: True, DREWAI_CODEX: True})
    assert passing[0].eligible is True
    assert passing[0].rollback_reference == applied.manifest_id

    restored = rollback_quarantine(applied, now=LATER, roots=roots)
    assert (
        removal_eligibility(restored, {DREWAI_CLAUDE: True, DREWAI_CODEX: True})[0].eligible
        is False
    )


def test_non_remove_entries_are_never_removal_eligible(tmp_path: Path) -> None:
    roots, plan, _ = planned(tmp_path)
    applied = apply_quarantine(plan, now=LATER, roots=roots)
    verdicts = removal_eligibility(applied, {DREWAI_CLAUDE: True, DREWAI_CODEX: True})
    assert verdicts and all(v.eligible is False for v in verdicts)
    assert all("not classified Remove" in v.reason for v in verdicts)


def test_target_passes_requires_no_high_failure_and_no_unknown(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    results = collect_both(roots)
    findings = run_rules(results, manifest=manifest())
    verdict = target_passes(findings, DREWAI_CLAUDE)
    assert verdict.passed is False  # the fixture has a HIGH duplication failure
    assert verdict.blocking
    assert all(f.targets for f in verdict.blocking)


def test_reclassify_after_rollback_keeps_the_item(tmp_path: Path) -> None:
    record = remove_record(
        "claude/command/~/.claude/commands/stale.md", "~/.claude/commands/stale.md"
    )
    kept = reclassify_after_rollback(
        record, "post-quarantine check: /stale is required by the relay"
    )
    assert kept.classification is Classification.KEEP
    assert kept.item_id == record.item_id
    assert "restored" in kept.reason and "required" in kept.reason
    assert kept.reversible_action
