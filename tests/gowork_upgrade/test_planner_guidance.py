"""T29 — one shared planner guidance, reached by Claude, Codex and DSH alike.

The guidance is a narrow supporting skill (the T26 rules, the T27 template and
check command, on top of the existing One Question flow) written once under
the shared skills directory. Each harness reaches it through its own
instruction entry — a routing block in the shared ``AGENTS.md`` that
``CLAUDE.md`` imports, and links (never copies) for the others. The installer
only stages into the home it is given, backs up any instruction file it
touches, is idempotent, and rolls back only what it owns. Nothing here reads
or writes the real home.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_code_core import gowork_guidance as gg
from claude_code_core.gowork_guidance import (
    BLOCK_END,
    BLOCK_START,
    GuidanceError,
    GuidanceLayout,
    guidance_digest,
    plan_guidance,
    render_skill,
    resolve_guidance,
    rollback_guidance,
    stage_guidance,
)
from claude_code_core.gowork_prompts import COMMUNICATION_RULES, PLANNER_RULES

HARNESSES = ("claude", "codex", "dsh")
USER_RULES = "# My rules\n\nKeep answers short.\n"
REJECTED = Path(__file__).resolve().parents[2] / ".planning/gowork-flow/planner-skill-draft"


@pytest.fixture
def layout(tmp_path: Path) -> GuidanceLayout:
    """A temporary home that looks like the audited machine: shared AGENTS.md, a
    CLAUDE.md that imports it, an empty Codex home and no DSH home yet."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("@~/AGENTS.md\n", encoding="utf-8")
    (home / "AGENTS.md").write_text(USER_RULES, encoding="utf-8")
    (home / ".codex-ccdb").mkdir()
    return GuidanceLayout.for_home(home, env={"CODEX_HOME": str(home / ".codex-ccdb")})


def _real_skill_files(home: Path) -> list[Path]:
    """Every SKILL.md carrying the guidance, without following links."""
    found: list[Path] = []
    for root, dirs, files in os.walk(home, followlinks=False):
        dirs[:] = [d for d in dirs if not gg.is_link(Path(root) / d)]
        for name in files:
            path = Path(root) / name
            if (
                name == "SKILL.md"
                and not path.is_symlink()
                and "gowork-planning" in path.read_text(encoding="utf-8")
            ):
                found.append(path)
    return found


def test_the_three_harnesses_resolve_the_same_guidance(layout: GuidanceLayout) -> None:
    report = stage_guidance(layout)

    resolved = {h: resolve_guidance(layout, h) for h in HARNESSES}
    assert all(r is not None for r in resolved.values()), resolved
    paths = {r.skill_path.resolve() for r in resolved.values() if r}
    assert paths == {layout.skill_file.resolve()}
    assert {r.digest for r in resolved.values() if r} == {guidance_digest()}
    assert layout.skill_file == layout.home / ".agents" / "skills" / "gowork-planning" / "SKILL.md"
    assert _real_skill_files(layout.home) == [layout.skill_file]  # written once, never copied

    text = layout.skill_file.read_text(encoding="utf-8")
    assert text.startswith("---\nname: gowork-planning\n")
    for rule in (
        *PLANNER_RULES.strip().splitlines()[:3],
        *COMMUNICATION_RULES.strip().splitlines()[:2],
    ):
        assert rule in text
    assert "```gowork-plan" in text and "gowork_export check" in text
    assert "one-question" in text  # builds on the existing flow
    assert "master-planning" not in text and "Recover Before Asking" not in text  # not the draft
    draft = (REJECTED / "SKILL.md").read_text(encoding="utf-8")
    assert "Not accepted as the direction" in draft and draft.splitlines()[1] not in text

    shared = (layout.home / "AGENTS.md").read_text(encoding="utf-8")
    assert shared.startswith(USER_RULES) and shared.count(BLOCK_START) == 1
    assert (layout.home / "AGENTS.md.ccdb-backup").read_text(encoding="utf-8") == USER_RULES
    assert (layout.claude_home / "CLAUDE.md").read_text(encoding="utf-8") == "@~/AGENTS.md\n"
    assert resolved["claude"] is not None and "AGENTS.md" in resolved["claude"].via[-2]
    assert (layout.codex_home / "AGENTS.md").exists() and (layout.dsh_home / "AGENTS.md").exists()
    assert not any(a.kind == "manual" and "AGENTS.md" in str(a.path) for a in report.actions)


def test_install_twice_creates_no_duplication(layout: GuidanceLayout) -> None:
    stage_guidance(layout)
    snapshot = {p: p.read_bytes() for p in layout.home.rglob("*") if p.is_file()}

    second = stage_guidance(layout)

    assert not second.changed, [a for a in second.actions if a.kind != "unchanged"]
    assert {p: p.read_bytes() for p in layout.home.rglob("*") if p.is_file()} == snapshot
    for path in (layout.home / "AGENTS.md", layout.codex_home / "AGENTS.md"):
        text = path.read_text(encoding="utf-8")
        assert text.count(BLOCK_START) <= 1 and text.count(BLOCK_END) == text.count(BLOCK_START)
    assert _real_skill_files(layout.home) == [layout.skill_file]


def test_rollback_restores_only_owned_content(layout: GuidanceLayout) -> None:
    (layout.codex_home / "AGENTS.md").write_text("# codex only\n", encoding="utf-8")
    stage_guidance(layout)
    shared = layout.home / "AGENTS.md"
    shared.write_text(
        shared.read_text(encoding="utf-8") + "\nAdded after install.\n", encoding="utf-8"
    )
    (layout.skill_dir / "notes.txt").write_text("mine\n", encoding="utf-8")

    report = rollback_guidance(layout)

    assert shared.read_text(encoding="utf-8") == USER_RULES + "\nAdded after install.\n"
    assert (layout.home / "AGENTS.md.ccdb-backup").exists()  # the person edited after; kept
    codex = (layout.codex_home / "AGENTS.md").read_text(encoding="utf-8")
    assert codex == "# codex only\n" and not (layout.codex_home / "AGENTS.md.ccdb-backup").exists()
    assert not layout.dsh_home.exists()  # created by us (file and folders), so removed
    assert layout.codex_home.is_dir()  # was there before; stays
    assert (layout.claude_home / "CLAUDE.md").read_text(encoding="utf-8") == "@~/AGENTS.md\n"
    assert not layout.skill_file.exists() and not layout.manifest_path.exists()
    assert (layout.skill_dir / "notes.txt").read_text(encoding="utf-8") == "mine\n"  # not ours
    assert not (layout.claude_home / "skills" / "gowork-planning").exists()
    assert all(resolve_guidance(layout, h) is None for h in HARNESSES)
    assert any(a.kind == "remove" for a in report.actions)
    # A second rollback finds nothing of ours and changes nothing.
    assert not rollback_guidance(layout).changed


def test_an_instruction_file_without_the_import_gets_the_block_with_a_backup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("# Claude\n\nBe brief.\n", encoding="utf-8")
    layout = GuidanceLayout.for_home(home)

    stage_guidance(layout)

    claude = (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith("# Claude\n\nBe brief.\n") and BLOCK_START in claude
    assert (home / ".claude" / "CLAUDE.md.ccdb-backup").read_text(encoding="utf-8") == (
        "# Claude\n\nBe brief.\n"
    )
    assert (home / "AGENTS.md").exists()  # the shared file is created when missing
    assert all(resolve_guidance(layout, h) is not None for h in HARNESSES)

    rollback_guidance(layout)
    assert (home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == "# Claude\n\nBe brief.\n"
    assert not (home / ".claude" / "CLAUDE.md.ccdb-backup").exists()
    assert not (home / "AGENTS.md").exists()


def test_links_are_links_or_reported_never_copies(layout: GuidanceLayout, monkeypatch) -> None:
    def refuse(target: Path, link: Path, *, directory: bool) -> None:
        raise OSError("no symlink privilege")

    monkeypatch.setattr(gg, "_symlink", refuse)
    monkeypatch.setattr(gg, "_junction", refuse)

    report = stage_guidance(layout)

    assert _real_skill_files(layout.home) == [layout.skill_file]
    manual = [a for a in report.actions if a.kind == "manual"]
    assert manual and all("gowork-planning" in a.detail for a in manual)
    assert not (layout.claude_home / "skills" / "gowork-planning").exists()
    # The instruction route still reaches the one skill file on every harness.
    assert {r.skill_path.resolve() for h in HARNESSES if (r := resolve_guidance(layout, h))} == {
        layout.skill_file.resolve()
    }
    codex = layout.codex_home / "AGENTS.md"
    assert not gg.is_link(codex) and BLOCK_START in codex.read_text(encoding="utf-8")


def test_staging_only_ever_touches_the_home_it_is_given(tmp_path: Path) -> None:
    with pytest.raises(GuidanceError, match="not a directory"):
        stage_guidance(GuidanceLayout.for_home(tmp_path / "missing"))
    with pytest.raises(SystemExit):
        gg._main(["stage"])  # no --home: refused, never defaults to the real home
    home = tmp_path / "home"
    home.mkdir()
    layout = GuidanceLayout.for_home(home)
    dry = plan_guidance(layout)
    assert dry.changed and not list(home.rglob("*"))  # a plan writes nothing
    assert gg._main(["stage", "--home", str(home)]) == 0
    assert layout.skill_file.exists()
    assert gg._main(["check", "--home", str(home)]) == 0
    assert gg._main(["rollback", "--home", str(home)]) == 0
    assert not layout.skill_file.exists()
    assert gg._main(["check", "--home", str(home)]) == 1


def test_the_rendered_skill_is_stable_and_repo_owned() -> None:
    assert render_skill() == render_skill()
    assert guidance_digest() == gg.digest_of(render_skill())
    assert "claude_code_core/gowork_prompts.py" in render_skill()
