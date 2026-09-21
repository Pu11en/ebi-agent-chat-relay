"""Claude effective-input collector tests (task 2.2).

The collector turns discovery output plus the Discord invocation plus existing
session transcripts into a :class:`HarnessInventory` whose evidence levels are
*earned*: ``loaded`` needs a transcript proving the CLI ran in that project on
a version the pinned guidance covers, ``configured`` is a file in a read
location with no session proof, ``installed-only`` is something the CLI does
not read at start (a command, a scope the invocation excludes), and ``unknown``
is an unparseable file or an uncovered CLI version.  No model, no subprocess.
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from extensions.harness_audit.claude_collector import (
    ClaudeInvocation,
    collect_claude,
    read_claude_sessions,
)
from extensions.harness_audit.discovery import discover
from extensions.harness_audit.models import (
    AuditTarget,
    EvidenceLevel,
    Harness,
    InventoryItem,
    Machine,
    Scope,
    SourceKind,
    to_json,
)
from extensions.harness_audit.redaction import build_redacted_bundle, serialize_bundle
from tests.harness_audit_fixtures import (
    FAKE_KEY,
    FAKE_SYSTEM_PROMPT,
    add_claude_transcript,
    claude_invocation,
    fake_home,
    manifest,
    write,
)

COLLECTED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
TARGET = AuditTarget(Machine.DREWAI, Harness.CLAUDE)


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """The collector must never run the real CLI or ask a model."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("collector attempted to start a subprocess")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)


def item(items: tuple[InventoryItem, ...], path: str) -> InventoryItem:
    for entry in items:
        if entry.item_id.endswith(path):
            return entry
    raise AssertionError(f"no item ends with {path!r}; have {[i.item_id for i in items]}")


def collect(
    tmp_path: Path, *, transcripts: bool, invocation: bool = True, extra: tuple[str, ...] = ()
):
    roots = fake_home(tmp_path, with_transcripts=transcripts)
    assert roots.project_dir is not None
    record = ClaudeInvocation.from_dict(claude_invocation(roots.project_dir, *extra))
    return collect_claude(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record if invocation else None,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
    )


# --------------------------------------------------------------------------- #
# Evidence levels are distinguished
# --------------------------------------------------------------------------- #


def test_session_transcript_plus_invocation_proves_loaded(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True)
    items = result.inventory.items
    global_memory = item(items, "~/.claude/CLAUDE.md")
    assert global_memory.evidence.level is EvidenceLevel.LOADED
    assert global_memory.evidence.depends_on_vendor_behavior
    assert "transcript" in global_memory.evidence.method
    assert item(items, "~/projects/relay/CLAUDE.md").evidence.level is EvidenceLevel.LOADED
    assert item(items, "~/.claude/settings.json").evidence.level is EvidenceLevel.LOADED
    assert item(items, "settings.json#PreToolUse[Bash]").evidence.level is EvidenceLevel.LOADED
    assert item(items, "~/.claude/skills/deploy/SKILL.md").evidence.level is EvidenceLevel.LOADED


def test_a_file_on_disk_without_session_proof_is_only_configured(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=False)
    items = result.inventory.items
    assert item(items, "~/.claude/CLAUDE.md").evidence.level is EvidenceLevel.CONFIGURED
    assert item(items, "~/projects/relay/CLAUDE.md").evidence.level is EvidenceLevel.CONFIGURED
    assert (
        item(items, "~/.claude/skills/deploy/SKILL.md").evidence.level is EvidenceLevel.CONFIGURED
    )
    effective_kinds = {entry.kind for entry in result.inventory.effective_items}
    assert effective_kinds <= {
        SourceKind.BOT_ADDITION,
        SourceKind.ENVIRONMENT,
        SourceKind.HARNESS_SETTING,
    }


def test_commands_and_non_claude_files_are_installed_only(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True)
    items = result.inventory.items
    command = item(items, "~/.claude/commands/verify.md")
    assert command.evidence.level is EvidenceLevel.INSTALLED_ONLY
    assert command.evidence.depends_on_vendor_behavior
    assert item(items, "~/projects/relay/AGENTS.md").evidence.level is EvidenceLevel.INSTALLED_ONLY
    assert command not in result.inventory.effective_items


def test_setting_sources_flag_turns_excluded_scopes_installed_only(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True, extra=("--setting-sources", "project"))
    items = result.inventory.items
    assert item(items, "~/.claude/CLAUDE.md").evidence.level is EvidenceLevel.INSTALLED_ONLY
    assert item(items, "~/.claude/settings.json").evidence.level is EvidenceLevel.INSTALLED_ONLY
    assert item(items, "~/projects/relay/CLAUDE.md").evidence.level is EvidenceLevel.LOADED


def test_unparseable_settings_and_uncovered_versions_are_unknown(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=False)
    assert roots.project_dir is not None
    write(roots.claude_home / "settings.local.json", "{not json")
    add_claude_transcript(roots.home, roots.project_dir, version="1.0.0", session="old")
    record = ClaudeInvocation.from_dict(claude_invocation(roots.project_dir))
    record = ClaudeInvocation(record.argv, record.cwd, record.environment_names, cli_version="")
    result = collect_claude(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
    )
    items = result.inventory.items
    local = item(items, "~/.claude/settings.local.json")
    assert local.evidence.level is EvidenceLevel.UNKNOWN
    assert local.evidence.missing_evidence
    memory = item(items, "~/.claude/CLAUDE.md")
    assert memory.evidence.level is EvidenceLevel.UNKNOWN
    assert any("1.0.0" in missing for missing in memory.evidence.missing_evidence)


def test_connectors_never_exceed_configured_without_tool_evidence(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True)
    connector = item(result.inventory.items, "~/.claude.json#github")
    assert connector.kind is SourceKind.CONNECTOR
    assert connector.evidence.level is EvidenceLevel.CONFIGURED
    strict = collect(tmp_path, transcripts=True, extra=("--strict-mcp-config",))
    assert (
        item(strict.inventory.items, "~/.claude.json#github").evidence.level
        is EvidenceLevel.INSTALLED_ONLY
    )


# --------------------------------------------------------------------------- #
# Sources, symlinks, bot additions, settings
# --------------------------------------------------------------------------- #


def test_symlinked_memory_lists_both_sources_and_the_canonical_target(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True)
    memory = item(result.inventory.items, "~/.claude/CLAUDE.md")
    references = [source.reference for source in memory.sources]
    assert references == ["~/.claude/CLAUDE.md", "~/AGENTS.md"]
    assert memory.effective_source.reference == "~/.claude/CLAUDE.md"
    assert "~/AGENTS.md" in memory.effective_behavior
    assert memory.scope is Scope.GLOBAL
    assert memory.content_hash == result.canonical_hash("~/AGENTS.md")


def test_bot_addition_is_loaded_and_its_body_is_withheld(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=False)
    addition = item(result.inventory.items, "append-system-prompt")
    assert addition.kind is SourceKind.BOT_ADDITION
    assert addition.evidence.level is EvidenceLevel.LOADED
    assert addition.size is not None
    assert addition.size.byte_size == len(FAKE_SYSTEM_PROMPT.encode())
    assert addition.size.token_count_kind.value == "estimated"
    assert [body.field for body in result.private_bodies] == [
        "claude/bot-addition/append-system-prompt"
    ]
    serialized = to_json(result.inventory)
    assert FAKE_SYSTEM_PROMPT not in serialized
    assert "Private operator prompt" not in serialized


def test_model_and_permission_settings_are_recorded_but_marked_excluded(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=False)
    model = item(result.inventory.items, "invocation#model")
    assert model.kind is SourceKind.HARNESS_SETTING
    assert model.scope is Scope.SESSION
    assert "excluded from cleanup" in model.effective_behavior
    assert "opus" in model.label
    permission = item(result.inventory.items, "invocation#permission-mode")
    assert "acceptEdits" in permission.label


def test_environment_names_are_kept_and_values_never_exist(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=False)
    env = item(result.inventory.items, "environment/ANTHROPIC_API_KEY")
    assert env.kind is SourceKind.ENVIRONMENT
    assert env.evidence.level is EvidenceLevel.LOADED
    assert result.environment == {
        "ANTHROPIC_API_KEY": "[redacted]",
        "CCDB_API_URL": "[redacted]",
        "PATH": "[redacted]",
    }


def test_without_an_invocation_nothing_is_loaded_and_the_gap_is_named(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True, invocation=False)
    assert all(entry.evidence.level is not EvidenceLevel.LOADED for entry in result.inventory.items)
    assert all(entry.kind is not SourceKind.BOT_ADDITION for entry in result.inventory.items)
    assert "invocation" in " ".join(result.notes).lower()


def test_result_serializes_into_a_bundle_without_any_fixture_secret(tmp_path: Path) -> None:
    result = collect(tmp_path, transcripts=True)
    bundle = build_redacted_bundle(
        machine=Machine.DREWAI,
        created_at=COLLECTED_AT,
        inventories=(result.inventory,),
        private_bodies=result.private_bodies,
        environment=result.environment,
    )
    serialized = serialize_bundle(bundle)
    assert FAKE_KEY not in serialized
    assert FAKE_SYSTEM_PROMPT not in serialized
    assert "private prompt text" not in serialized
    assert result.inventory.target == TARGET


def test_read_claude_sessions_keeps_metadata_only(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    sessions = read_claude_sessions(discover(roots, salt="t").transcripts)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.version == "2.0.5"
    assert session.cwd.endswith("projects/relay")
    assert "private" not in to_json(session)
