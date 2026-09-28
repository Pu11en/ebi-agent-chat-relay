"""Regression coverage for skill libraries and packages installed through directory links."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_discord.ai_setup_adapters import claude_home_root, codex_home_root, project_root
from claude_discord.ai_setup_inventory import AvailabilityState, SetupKind
from tests.test_ai_setup_shared_adapter import by_name, collect, context, write


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
class TestLinkedSkills:
    @pytest.mark.parametrize("layout", ["claude", "codex", "project-claude", "project-codex"])
    @pytest.mark.parametrize("name", ["grilling", "synced/docs"])
    def test_linked_library_keeps_skill_identity_and_metadata(
        self, tmp_path: Path, layout: str, name: str
    ) -> None:
        home = tmp_path / "home"
        shared = home / ".agents" / "skills"
        manifest = write(shared / name / "SKILL.md", "---\ndescription: Shared skill\n---\n")
        if layout == "claude":
            root = claude_home_root(home / ".claude", owner="drew", home=home)
            installed = root.path / "skills"
        elif layout == "codex":
            root = codex_home_root(home / ".codex-ccdb", owner="drew", home=home)
            installed = root.path / "skills"
        else:
            root = project_root(home / "project", name="project", home=home)
            installed = (
                root.path / (".claude" if layout == "project-claude" else ".agents") / "skills"
            )
        installed.parent.mkdir(parents=True)
        installed.symlink_to(shared, target_is_directory=True)
        before = (manifest.read_bytes(), manifest.stat().st_mtime_ns, installed.readlink())

        result = collect(root)

        skill = by_name(result.snapshot.items, SetupKind.SKILL, name)
        assert skill.identity.key == f"skill:{root.key}:{name}"
        assert skill.source.locator == root.locator_for(installed / name / "SKILL.md")
        assert skill.scope == root.scope_for(context())
        assert skill.summary == "Shared skill"
        assert skill.measurement.byte_size == manifest.stat().st_size
        assert not skill.has_verified_availability
        assert result.snapshot.diagnostics == ()
        assert before == (manifest.read_bytes(), manifest.stat().st_mtime_ns, installed.readlink())

    @pytest.mark.parametrize("name", ["grilling", "synced/docs"])
    def test_linked_package_inside_linked_library_is_discovered(
        self, tmp_path: Path, name: str
    ) -> None:
        home = tmp_path / "home"
        root = claude_home_root(home / ".claude", owner="drew", home=home)
        shared = home / ".agents" / "skills"
        package = tmp_path / "Skill Sources" / "package"
        write(package / "SKILL.md", "---\ndescription: Linked package\n---\n")
        (shared / name).parent.mkdir(parents=True)
        (shared / name).symlink_to(package, target_is_directory=True)
        root.path.mkdir()
        (root.path / "skills").symlink_to(shared, target_is_directory=True)

        result = collect(root)

        skill = by_name(result.snapshot.items, SetupKind.SKILL, name)
        assert skill.summary == "Linked package"
        assert skill.state_for("claude") is AvailabilityState.DISCOVERED
        assert skill.state_for("codex") is AvailabilityState.DISCOVERED
        assert result.snapshot.diagnostics == ()

    def test_linked_package_does_not_allow_manifest_or_instruction_escape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = claude_home_root(tmp_path / ".claude", owner="drew")
        package = tmp_path / "packages" / "escape"
        package.mkdir(parents=True)
        outside = write(tmp_path / "private" / "secret.txt", "must not be read\n")
        (package / "SKILL.md").symlink_to(outside)
        (root.path / "skills").mkdir(parents=True)
        (root.path / "skills" / "escape").symlink_to(package, target_is_directory=True)
        (root.path / "CLAUDE.md").symlink_to(outside)
        read_bytes = Path.read_bytes

        def guarded_read(path: Path) -> bytes:
            assert path.resolve() != outside, "inventory followed a file link outside its boundary"
            return read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", guarded_read)

        result = collect(root)

        assert result.failed_adapters == ()
        assert result.snapshot.items == ()
        assert len(result.snapshot.diagnostics) == 2
        assert all("outside" in note.message for note in result.snapshot.diagnostics)
