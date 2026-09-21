"""Read-only discovery tests for the professional harness audit (task 2.1).

Discovery walks a *fake* home handed to it — never the real ``~/.claude`` or
``~/.codex`` — and records safe metadata about every file a harness can read:
kind, scope, permissions, exact size, salted hash and whether the content
matched a secret rule.  It never carries content, it never follows a link out
of the approved roots, and it never writes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from extensions.harness_audit.discovery import (
    DiscoveredFile,
    DiscoveryError,
    DiscoveryRoots,
    discover,
)
from extensions.harness_audit.models import Harness, RedactionStatus, Scope, SourceKind
from tests.harness_audit_fixtures import FAKE_KEY, FAKE_NUMERIC_PASSWORD, fake_home, write


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def by_path(result_files: tuple[DiscoveredFile, ...], display: str) -> DiscoveredFile:
    for entry in result_files:
        if entry.path == display:
            return entry
    raise AssertionError(f"{display} not discovered; have {[f.path for f in result_files]}")


# --------------------------------------------------------------------------- #
# What is found, and how it is described
# --------------------------------------------------------------------------- #


def test_discovers_every_input_kind_with_scope_and_metadata(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    result = discover(roots, salt="test")

    found = {(entry.path, entry.kind, entry.scope, entry.harness) for entry in result.files}
    assert ("~/AGENTS.md", SourceKind.GLOBAL_INSTRUCTIONS, Scope.GLOBAL, None) in found
    assert (
        "~/.claude/CLAUDE.md",
        SourceKind.GLOBAL_INSTRUCTIONS,
        Scope.GLOBAL,
        Harness.CLAUDE,
    ) in found
    assert (
        "~/.claude/settings.json",
        SourceKind.HARNESS_SETTING,
        Scope.GLOBAL,
        Harness.CLAUDE,
    ) in found
    assert ("~/.claude/skills/deploy/SKILL.md", SourceKind.SKILL, Scope.GLOBAL, Harness.CLAUDE) in (
        found
    )
    assert (
        "~/.claude/commands/verify.md",
        SourceKind.COMMAND,
        Scope.GLOBAL,
        Harness.CLAUDE,
    ) in found
    assert ("~/.claude.json", SourceKind.CONNECTOR, Scope.GLOBAL, Harness.CLAUDE) in found
    assert (
        "~/.codex/config.toml",
        SourceKind.HARNESS_SETTING,
        Scope.GLOBAL,
        Harness.CODEX,
    ) in found
    assert (
        "~/.codex/AGENTS.md",
        SourceKind.GLOBAL_INSTRUCTIONS,
        Scope.GLOBAL,
        Harness.CODEX,
    ) in found
    assert (
        "~/.codex/skills/release/SKILL.md",
        SourceKind.SKILL,
        Scope.GLOBAL,
        Harness.CODEX,
    ) in found
    assert (
        "~/projects/relay/CLAUDE.md",
        SourceKind.PROJECT_INSTRUCTIONS,
        Scope.PROJECT,
        Harness.CLAUDE,
    ) in found
    assert ("~/projects/relay/AGENTS.md", SourceKind.PROJECT_INSTRUCTIONS, Scope.PROJECT, None) in (
        found
    )
    assert (
        "~/projects/relay/.claude/commands/verify.md",
        SourceKind.COMMAND,
        Scope.PROJECT,
        Harness.CLAUDE,
    ) in found

    agents = by_path(result.files, "~/AGENTS.md")
    assert agents.size.byte_size == len(b"# Global rules\n\nAlways run the tests.\n")
    assert agents.size.characters == agents.size.byte_size
    assert agents.content_hash.startswith("sha256:")
    assert agents.permissions == "0644"
    assert agents.redaction is RedactionStatus.NONE_NEEDED
    assert not agents.is_symlink


def test_symlinks_are_resolved_and_share_the_canonical_hash(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    result = discover(roots, salt="test")
    canonical = by_path(result.files, "~/AGENTS.md")
    claude_link = by_path(result.files, "~/.claude/CLAUDE.md")
    codex_link = by_path(result.files, "~/.codex/AGENTS.md")
    assert claude_link.is_symlink and codex_link.is_symlink
    assert claude_link.resolved_path == "~/AGENTS.md"
    assert codex_link.resolved_path == "~/AGENTS.md"
    assert claude_link.content_hash == canonical.content_hash == codex_link.content_hash


def test_hash_is_salted_and_deterministic(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    first = by_path(discover(roots, salt="a").files, "~/AGENTS.md")
    again = by_path(discover(roots, salt="a").files, "~/AGENTS.md")
    other = by_path(discover(roots, salt="b").files, "~/AGENTS.md")
    assert first.content_hash == again.content_hash
    assert first.content_hash != other.content_hash


def test_settings_facts_carry_names_and_shapes_but_never_values(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    result = discover(roots, salt="test")
    settings = by_path(result.files, "~/.claude/settings.json")
    assert settings.settings is not None
    assert "permissions" in settings.settings.keys
    assert settings.settings.env_names == ("ANTHROPIC_API_KEY", "EDITOR")
    assert settings.settings.permission_rules == {"allow": 1, "deny": 1}
    assert [(hook.event, hook.matcher) for hook in settings.settings.hooks] == [
        ("PreToolUse", "Bash")
    ]
    assert settings.settings.hooks[0].command_hash.startswith("sha256:")
    assert settings.settings.model == "opus"
    assert settings.redaction is RedactionStatus.REDACTED
    assert settings.signals.secret_matches >= 1

    codex = by_path(result.files, "~/.codex/config.toml")
    assert codex.settings is not None
    assert codex.settings.model == "gpt-5-codex"
    assert codex.settings.reasoning_effort == "high"
    assert codex.settings.mcp_servers == ("github",)
    assert codex.redaction is RedactionStatus.REDACTED

    connectors = by_path(result.files, "~/.claude.json")
    assert connectors.settings is not None
    assert connectors.settings.mcp_servers == ("github",)

    serialized = json.dumps(result.to_dict())
    assert FAKE_KEY not in serialized
    assert FAKE_NUMERIC_PASSWORD not in serialized
    assert "vim" not in serialized
    assert "gh-mcp" not in serialized


def test_signals_name_referenced_projects_and_skill_frontmatter(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    result = discover(roots, salt="test")
    skill = by_path(result.files, "~/.claude/skills/deploy/SKILL.md")
    assert skill.signals.declared_name == "deploy"
    assert 0 < skill.signals.frontmatter_bytes < skill.size.byte_size
    assert skill.signals.referenced_projects == ("ebi-agent-chat-relay",)
    agents = by_path(result.files, "~/AGENTS.md")
    assert agents.signals.referenced_projects == ()


# --------------------------------------------------------------------------- #
# What is refused
# --------------------------------------------------------------------------- #


def test_a_link_outside_the_approved_roots_is_excluded_not_followed(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    outside = write(tmp_path / "elsewhere" / "secrets.md", f"api_key={FAKE_KEY}\n")
    links = dict(roots.links)
    links[roots.claude_home / "commands" / "leak.md"] = outside
    roots = DiscoveryRoots(
        home=roots.home,
        claude_home=roots.claude_home,
        codex_home=roots.codex_home,
        project_dir=roots.project_dir,
        known_projects=roots.known_projects,
        links=links,
        modes=roots.modes,
    )
    result = discover(roots, salt="test")
    assert all(entry.path != "~/.claude/commands/leak.md" for entry in result.files)
    assert any(
        "leak.md" in excluded.path and "outside" in excluded.reason for excluded in result.excluded
    )
    assert FAKE_KEY not in json.dumps(result.to_dict())


def test_unreadable_files_are_reported_rather_than_guessed(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    write(roots.claude_home / "settings.local.json", "{not json")
    write(roots.codex_home / "config.toml", "model = [unterminated")
    result = discover(roots, salt="test")
    local = by_path(result.files, "~/.claude/settings.local.json")
    assert local.settings is not None
    assert local.settings.parse_error
    codex = by_path(result.files, "~/.codex/config.toml")
    assert codex.settings is not None
    assert codex.settings.parse_error


def test_discovery_never_writes_and_reads_only_the_roots_it_was_given(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    before = snapshot(tmp_path)
    discover(roots, salt="test")
    assert snapshot(tmp_path) == before


def test_roots_must_exist_and_project_may_be_absent(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError, match="claude_home"):
        DiscoveryRoots(
            home=tmp_path, claude_home=tmp_path / "missing", codex_home=tmp_path / "missing"
        )
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()
    roots = DiscoveryRoots(
        home=tmp_path, claude_home=tmp_path / ".claude", codex_home=tmp_path / ".codex"
    )
    result = discover(roots, salt="x")
    assert result.files == ()
    assert result.excluded == ()


def test_result_round_trips_deterministically(tmp_path: Path) -> None:
    roots = fake_home(tmp_path)
    result = discover(roots, salt="test")
    payload = json.dumps(result.to_dict(), sort_keys=False)
    assert json.dumps(discover(roots, salt="test").to_dict()) == payload
    for entry in result.files:
        assert DiscoveredFile.from_dict(entry.to_dict()) == entry
