"""Shared setup adapter tests (task 2.1).

The shared adapter inventories instructions, memory and skills from *declared*
source roots — fixture homes built under ``tmp_path``, never the real
``~/.claude`` or ``~/.codex`` — and assigns each item a stable identity, the
right source, and the right user-facing scope.  It reads nothing outside the
roots it was given, and a file it cannot summarize costs one diagnostic, not
the run.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_discord.ai_setup_adapters import (
    HarnessLayout,
    SetupRoot,
    SharedSetupAdapter,
    claude_home_root,
    codex_home_root,
    project_root,
)
from claude_discord.ai_setup_collector import CollectionContext, InventoryCollector
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    InventoryItem,
    MeasurementMethod,
    OwnershipClass,
    ScopeKind,
    SetupKind,
)

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
LEAKED = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake, used to prove redaction


def write(path: Path, text: str = "# custom\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    home = tmp_path / "home" / ".claude"
    write(home / "CLAUDE.md", "Always answer in British English.\n")
    write(home / "skills" / "grilling" / "SKILL.md", "---\nname: grilling\n---\nHow to grill.\n")
    write(home / "skills" / "synced" / "docs" / "SKILL.md", "Synced skill.\n")
    write(home / "projects" / "C--work-ccdb" / "memory" / "MEMORY.md", "Drew prefers uv.\n")
    return home


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / "home" / ".codex"
    write(home / "AGENTS.md", "Prefer small diffs.\n")
    write(home / "skills" / "release" / "SKILL.md", "Release checklist.\n")
    write(home / "memories" / "notes.md", "Codex memory.\n")
    return home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "work" / "ccdb"
    write(root / "CLAUDE.md", "Run the tests with uv.\n")
    write(root / "AGENTS.md", "Mirror of CLAUDE.md.\n")
    write(root / ".claude" / "skills" / "verify" / "SKILL.md", "Verify gate.\n")
    write(root / "CLAUDE.local.md", "Only on this machine.\n")
    return root


def context(**overrides: object) -> CollectionContext:
    defaults: dict[str, object] = {
        "computer": "drewai",
        "owner": "drew",
        "collected_at": NOW,
        "harnesses": ("claude", "codex"),
    }
    defaults.update(overrides)
    return CollectionContext(**defaults)  # pyright: ignore[reportArgumentType]


def collect(*roots: SetupRoot, ctx: CollectionContext | None = None):
    collector = InventoryCollector()
    collector.register(SharedSetupAdapter(roots))
    return collector.collect(ctx or context())


def by_name(items: tuple[InventoryItem, ...], kind: SetupKind, name: str) -> InventoryItem:
    for item in items:
        if item.kind is kind and item.identity.name == name:
            return item
    keys = [i.identity.key for i in items]
    raise AssertionError(f"no {kind.value} item named {name!r} in {keys}")


# ---------------------------------------------------------------------------
# Declared roots
# ---------------------------------------------------------------------------


class TestSetupRoot:
    def test_home_roots_carry_their_harness_and_a_profile_ownership(self, claude_home: Path):
        root = claude_home_root(claude_home, owner="drew")
        assert root.key == "claude-home"
        assert root.harness == "claude"
        assert root.layout is HarnessLayout.CLAUDE_HOME
        assert root.ownership is OwnershipClass.MEGA_GLOBAL
        assert root.owner == "drew"

    def test_codex_root_and_project_root(self, codex_home: Path, project: Path):
        codex = codex_home_root(codex_home, owner="drew")
        assert (codex.key, codex.harness, codex.layout) == (
            "codex-home",
            "codex",
            HarnessLayout.CODEX_HOME,
        )
        proj = project_root(project, name="ccdb")
        assert proj.key == "project-ccdb"
        assert proj.ownership is OwnershipClass.PROJECT
        assert proj.project == "ccdb"
        assert proj.harness is None

    def test_a_root_key_is_a_portable_token(self, project: Path):
        assert project_root(project, name="My Project!").key == "project-my-project"

    def test_locator_hides_the_home_directory(self, claude_home: Path, tmp_path: Path):
        root = claude_home_root(claude_home, owner="drew", home=tmp_path / "home")
        assert root.locator_for(claude_home / "CLAUDE.md") == "~/.claude/CLAUDE.md"


# ---------------------------------------------------------------------------
# Instructions, memory and skills from each declared root
# ---------------------------------------------------------------------------


class TestSharedInventory:
    def test_declares_its_boundary(self, claude_home: Path):
        adapter = SharedSetupAdapter([claude_home_root(claude_home, owner="drew")])
        assert adapter.name == "shared-setup"
        assert set(adapter.kinds) == {SetupKind.INSTRUCTION, SetupKind.MEMORY, SetupKind.SKILL}
        assert list(adapter.source_keys) == ["claude-home"]

    def test_inventories_every_kind_from_a_claude_home(self, claude_home: Path, tmp_path: Path):
        result = collect(claude_home_root(claude_home, owner="drew", home=tmp_path / "home"))
        items = result.snapshot.items

        assert result.failed_adapters == ()
        instructions = by_name(items, SetupKind.INSTRUCTION, "CLAUDE.md")
        assert instructions.source.locator == "~/.claude/CLAUDE.md"
        assert instructions.source.key == "claude-home"
        assert instructions.scope.kind is ScopeKind.SHARED_PROFILE
        assert instructions.scope.label == "Shared Drew profile"
        assert instructions.classification is Classification.CUSTOM

        skill = by_name(items, SetupKind.SKILL, "grilling")
        assert skill.identity.key == "skill:claude-home:grilling"
        assert skill.source.locator.endswith("skills/grilling/SKILL.md")
        assert by_name(items, SetupKind.SKILL, "synced/docs")

        memory = by_name(items, SetupKind.MEMORY, "C--work-ccdb/MEMORY.md")
        assert memory.scope.kind is ScopeKind.SHARED_PROFILE

    def test_inventories_a_codex_home_and_a_project(
        self, codex_home: Path, project: Path, tmp_path: Path
    ):
        result = collect(
            codex_home_root(codex_home, owner="drew", home=tmp_path / "home"),
            project_root(project, name="ccdb"),
        )
        items = result.snapshot.items

        agents = by_name(items, SetupKind.INSTRUCTION, "AGENTS.md")
        assert agents.source.key == "codex-home"
        assert agents.scope.kind is ScopeKind.SHARED_PROFILE
        assert by_name(items, SetupKind.SKILL, "release").source.key == "codex-home"
        assert by_name(items, SetupKind.MEMORY, "notes.md").source.key == "codex-home"

        project_claude = [
            item
            for item in items
            if item.kind is SetupKind.INSTRUCTION and item.source.key == "project-ccdb"
        ]
        assert {item.identity.name for item in project_claude} == {
            "CLAUDE.md",
            "AGENTS.md",
            "CLAUDE.local.md",
        }
        for item in project_claude:
            if item.identity.name == "CLAUDE.local.md":
                assert item.scope.kind is ScopeKind.COMPUTER
                assert item.scope.computer == "drewai"
                assert item.ownership is OwnershipClass.MACHINE
            else:
                assert item.scope.kind is ScopeKind.PROJECT
                assert item.scope.project == "ccdb"
        verify = by_name(items, SetupKind.SKILL, "verify")
        assert verify.source.key == "project-ccdb"
        assert verify.scope.kind is ScopeKind.PROJECT

    def test_another_owners_home_is_their_profile(self, claude_home: Path):
        result = collect(
            claude_home_root(claude_home, owner="david", key="claude-home-david"),
            ctx=context(owner="drew", primary_owner="drew"),
        )
        item = by_name(result.snapshot.items, SetupKind.INSTRUCTION, "CLAUDE.md")
        assert item.scope.kind is ScopeKind.OTHER_PROFILE
        assert item.scope.label == "David's profile"
        assert "mega" not in item.scope.label.lower()

    def test_identity_is_the_same_on_every_computer(self, claude_home: Path, tmp_path: Path):
        drewai = collect(claude_home_root(claude_home, owner="drew", home=tmp_path / "home"))
        imac = collect(
            claude_home_root(claude_home, owner="drew", home=tmp_path / "elsewhere"),
            ctx=context(computer="imac"),
        )
        assert {i.identity.key for i in drewai.snapshot.items} == {
            i.identity.key for i in imac.snapshot.items
        }
        assert all(item.computer == "imac" for item in imac.snapshot.items)


# ---------------------------------------------------------------------------
# Facts: measurement, fingerprint, modification time, availability
# ---------------------------------------------------------------------------


class TestFacts:
    def test_measures_bytes_and_estimates_tokens_without_false_precision(self, claude_home: Path):
        result = collect(claude_home_root(claude_home, owner="drew"))
        item = by_name(result.snapshot.items, SetupKind.INSTRUCTION, "CLAUDE.md")
        data = (claude_home / "CLAUDE.md").read_bytes()

        assert item.measurement.byte_size == len(data)
        assert item.measurement.character_count == len(data.decode("utf-8"))
        assert item.measurement.token_method is MeasurementMethod.ESTIMATED
        assert item.measurement.token_label.startswith("~")
        assert "estimated" in item.measurement.token_label
        assert item.fingerprint is not None
        assert item.last_changed_at is not None
        assert item.last_changed_at.tzinfo is not None
        assert item.source.modified_at == item.last_changed_at

    def test_content_never_appears_on_the_item(self, claude_home: Path):
        write(claude_home / "CLAUDE.md", f"Use {LEAKED} for the docs connector.\n")
        result = collect(claude_home_root(claude_home, owner="drew"))
        item = by_name(result.snapshot.items, SetupKind.INSTRUCTION, "CLAUDE.md")
        rendered = repr(item)
        assert LEAKED not in rendered
        assert "docs connector" not in rendered
        assert all(LEAKED not in d.message for d in result.snapshot.diagnostics)

    def test_a_skill_in_the_claude_home_is_discovered_by_claude_and_codex(self, claude_home: Path):
        result = collect(claude_home_root(claude_home, owner="drew"))
        skill = by_name(result.snapshot.items, SetupKind.SKILL, "grilling")
        assert skill.state_for("claude") is AvailabilityState.DISCOVERED
        assert skill.state_for("codex") is AvailabilityState.DISCOVERED
        assert not skill.has_verified_availability

    def test_a_claude_only_file_is_unsupported_by_codex_and_vice_versa(
        self, claude_home: Path, codex_home: Path
    ):
        result = collect(
            claude_home_root(claude_home, owner="drew"), codex_home_root(codex_home, owner="drew")
        )
        claude_md = by_name(result.snapshot.items, SetupKind.INSTRUCTION, "CLAUDE.md")
        assert claude_md.state_for("claude") is AvailabilityState.DISCOVERED
        assert claude_md.state_for("codex") is AvailabilityState.UNSUPPORTED
        agents_md = by_name(result.snapshot.items, SetupKind.INSTRUCTION, "AGENTS.md")
        assert agents_md.state_for("codex") is AvailabilityState.DISCOVERED
        assert agents_md.state_for("claude") is AvailabilityState.UNSUPPORTED

    def test_only_harnesses_the_run_declared_are_reported(self, claude_home: Path):
        result = collect(
            claude_home_root(claude_home, owner="drew"), ctx=context(harnesses=("claude",))
        )
        skill = by_name(result.snapshot.items, SetupKind.SKILL, "grilling")
        assert skill.state_for("codex") is AvailabilityState.UNKNOWN
        assert skill.state_for("claude") is AvailabilityState.DISCOVERED

    def test_an_undeclared_harness_list_reports_every_known_harness(self, claude_home: Path):
        result = collect(claude_home_root(claude_home, owner="drew"), ctx=context(harnesses=()))
        skill = by_name(result.snapshot.items, SetupKind.SKILL, "grilling")
        assert skill.state_for("codex") is AvailabilityState.DISCOVERED
        assert skill.state_for("claude") is AvailabilityState.DISCOVERED

    def test_skill_summary_comes_from_frontmatter_description_only(self, claude_home: Path):
        write(
            claude_home / "skills" / "grilling" / "SKILL.md",
            f"---\nname: grilling\ndescription: Grill safely\napi_key: {LEAKED}\n---\nbody\n",
        )
        result = collect(claude_home_root(claude_home, owner="drew"))
        skill = by_name(result.snapshot.items, SetupKind.SKILL, "grilling")
        assert skill.summary == "Grill safely"
        assert LEAKED not in repr(skill)


# ---------------------------------------------------------------------------
# Boundaries and failures
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_reads_nothing_outside_the_declared_roots(self, claude_home: Path, tmp_path: Path):
        write(tmp_path / "home" / ".codex" / "AGENTS.md", "not declared\n")
        write(claude_home.parent / "CLAUDE.md", "sibling, outside the root\n")
        result = collect(claude_home_root(claude_home, owner="drew"))
        keys = {item.source.key for item in result.snapshot.items}
        assert keys == {"claude-home"}
        assert not any("codex" in i.source.locator for i in result.snapshot.items)

    def test_ignores_unrelated_files_in_the_root(self, claude_home: Path):
        write(claude_home / "history.jsonl", '{"secret": "x"}\n')
        write(claude_home / "settings.json", '{"apiKey": "x"}\n')
        write(claude_home / "skills" / "grilling" / "notes.txt", "scratch\n")
        result = collect(claude_home_root(claude_home, owner="drew"))
        locators = [item.source.locator for item in result.snapshot.items]
        assert not any("history.jsonl" in loc or "settings.json" in loc for loc in locators)
        assert not any("notes.txt" in loc for loc in locators)

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
    def test_a_symlink_escaping_the_root_is_reported_not_followed(
        self, claude_home: Path, tmp_path: Path
    ):
        outside = write(tmp_path / "outside" / "SKILL.md", "outside\n")
        (claude_home / "skills" / "escape").mkdir()
        os.symlink(outside, claude_home / "skills" / "escape" / "SKILL.md")
        result = collect(claude_home_root(claude_home, owner="drew"))
        skills = [item for item in result.snapshot.items if item.kind is SetupKind.SKILL]
        assert "escape" not in {item.identity.name for item in skills}
        assert any("outside" in d.message for d in result.snapshot.diagnostics)

    def test_a_missing_root_is_a_diagnostic_and_the_rest_still_collects(
        self, claude_home: Path, tmp_path: Path
    ):
        result = collect(
            claude_home_root(claude_home, owner="drew"),
            codex_home_root(tmp_path / "nowhere" / ".codex", owner="drew"),
        )
        assert by_name(result.snapshot.items, SetupKind.SKILL, "grilling")
        assert result.failed_adapters == ()
        notes = result.snapshot.diagnostics_for("codex-home")
        assert notes and "not present" in notes[0].message

    def test_an_unreadable_file_costs_one_diagnostic(self, claude_home: Path):
        (claude_home / "skills" / "broken").mkdir()
        (claude_home / "skills" / "broken" / "SKILL.md").mkdir()  # a directory, not a file
        result = collect(claude_home_root(claude_home, owner="drew"))
        assert by_name(result.snapshot.items, SetupKind.SKILL, "grilling")
        assert any(
            d.source_key == "claude-home" and "broken" in d.message
            for d in result.snapshot.diagnostics
        )

    def test_collection_changes_nothing_in_the_root(self, claude_home: Path):
        before = {p: p.stat().st_mtime_ns for p in claude_home.rglob("*") if p.is_file()}
        collect(claude_home_root(claude_home, owner="drew"))
        after = {p: p.stat().st_mtime_ns for p in claude_home.rglob("*") if p.is_file()}
        assert before == after
