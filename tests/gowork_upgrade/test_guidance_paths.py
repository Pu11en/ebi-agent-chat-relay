"""E2 — the guidance installer never writes through a link, and rollback only
touches paths inside the layout it was given.

``stage`` edits instruction files in place. When one of them is a symlink the
write lands wherever the link points — outside ``--home`` if someone planted
it — and rollback then writes through it again. ``rollback`` used to take every
path in ``.ccdb-install.json`` verbatim; a tampered manifest could name any
file on the machine to unlink or rewrite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_code_core import gowork_guidance as gg
from claude_code_core.gowork_guidance import (
    BLOCK_START,
    GuidanceLayout,
    rollback_guidance,
    stage_guidance,
)

USER_RULES = "# My rules\n\nKeep answers short.\n"


@pytest.fixture
def layout(tmp_path: Path) -> GuidanceLayout:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("@~/AGENTS.md\n", encoding="utf-8")
    (home / "AGENTS.md").write_text(USER_RULES, encoding="utf-8")
    (home / ".codex-ccdb").mkdir()
    return GuidanceLayout.for_home(home, env={"CODEX_HOME": str(home / ".codex-ccdb")})


def _pretend_symlink(monkeypatch: pytest.MonkeyPatch, *linked: Path) -> None:
    """Make *linked* look like symlinks without needing the Windows privilege."""
    targets = {p.resolve() for p in linked}
    original = gg._is_symlink

    def fake(path: Path) -> bool:
        return path.resolve() in targets or original(path)

    monkeypatch.setattr(gg, "_is_symlink", fake)


def test_stage_refuses_to_write_through_a_symlinked_shared_file(
    layout: GuidanceLayout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = tmp_path / "outside" / "AGENTS.md"
    outside.parent.mkdir()
    outside.write_text(USER_RULES, encoding="utf-8")
    shared = layout.home / "AGENTS.md"
    _pretend_symlink(monkeypatch, shared)

    report = stage_guidance(layout)

    assert shared.read_text(encoding="utf-8") == USER_RULES  # nothing written through it
    assert outside.read_text(encoding="utf-8") == USER_RULES
    assert not (layout.home / "AGENTS.md.ccdb-backup").exists()
    manual = [a for a in report.manual if a.path == shared]
    assert manual and "link" in manual[0].detail
    assert layout.skill_file.is_file()  # the rest of the install still happened


def test_stage_refuses_to_write_through_a_symlinked_claude_entry(
    layout: GuidanceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = layout.claude_home / "CLAUDE.md"
    entry.write_text("# no import here\n", encoding="utf-8")
    _pretend_symlink(monkeypatch, entry)

    report = stage_guidance(layout)

    assert entry.read_text(encoding="utf-8") == "# no import here\n"
    assert any(a.path == entry and "link" in a.detail for a in report.manual)
    # Rollback afterwards does not write through it either.
    rollback_guidance(layout)
    assert entry.read_text(encoding="utf-8") == "# no import here\n"


@pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="symlinks are not available on this platform"
)
def test_stage_refuses_a_real_symlink_when_the_platform_allows_one(
    layout: GuidanceLayout, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# elsewhere\n", encoding="utf-8")
    shared = layout.home / "AGENTS.md"
    shared.unlink()
    try:
        os.symlink(outside, shared)
    except OSError:
        pytest.skip("this account cannot create symlinks")

    report = stage_guidance(layout)

    assert outside.read_text(encoding="utf-8") == "# elsewhere\n"
    assert any(a.path == shared for a in report.manual)


def test_rollback_refuses_manifest_paths_outside_the_layout(
    layout: GuidanceLayout, tmp_path: Path
) -> None:
    stage_guidance(layout)
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    victim_file = victim_dir / "CLAUDE.md"
    victim_file.write_text(f"{BLOCK_START}\nplanted\n", encoding="utf-8")
    victim_skill = victim_dir / "SKILL.md"
    victim_skill.write_text("mine\n", encoding="utf-8")
    victim_link = victim_dir / "link"
    victim_link.mkdir()
    empty_dir = tmp_path / "empty-victim"
    empty_dir.mkdir()

    manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    manifest["links"].append(str(victim_link))
    manifest["instruction_files"][str(victim_file)] = {"created": True, "link": False}
    manifest["instruction_files"][str(victim_dir / "AGENTS.md")] = {"created": True, "link": True}
    manifest["instruction_files"][str(layout.home / ".." / "victim" / "CLAUDE.md")] = {
        "created": True,
        "link": False,
    }
    manifest["skill_file"] = str(victim_skill)
    manifest["created_dirs"].append(str(empty_dir))
    layout.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = rollback_guidance(layout)

    assert victim_file.read_text(encoding="utf-8") == f"{BLOCK_START}\nplanted\n"
    assert victim_skill.read_text(encoding="utf-8") == "mine\n"
    assert victim_link.is_dir() and empty_dir.is_dir()
    refused = {a.path for a in report.manual}
    assert {victim_file, victim_skill, victim_link, empty_dir} <= {p for p in refused}
    assert all("outside" in a.detail for a in report.manual)
    # Everything that really was ours is still rolled back.
    assert not layout.manifest_path.exists()
    assert (layout.home / "AGENTS.md").read_text(encoding="utf-8") == USER_RULES
    codex = layout.codex_home / "AGENTS.md"
    assert not codex.exists() or BLOCK_START not in codex.read_text(encoding="utf-8")


def test_rollback_keeps_the_real_skill_file_when_the_manifest_points_elsewhere(
    layout: GuidanceLayout, tmp_path: Path
) -> None:
    stage_guidance(layout)
    manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    manifest["skill_file"] = str(tmp_path / "not-ours" / "SKILL.md")
    layout.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = rollback_guidance(layout)

    # The manifest's claim was refused; the layout's own skill file is what we remove.
    assert any(a.kind == "manual" and "outside" in a.detail for a in report.actions)
    assert not layout.skill_file.exists()
