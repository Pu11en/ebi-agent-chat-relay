"""Working copies stay usable, but are not advertised as new projects."""

from __future__ import annotations

from pathlib import Path

from claude_discord.folder_search import rank_folders, scan_project_folders
from claude_discord.project_catalog import ProjectDiscovery
from tests.test_project_catalog import make_root


def test_linked_worktree_hidden_in_both_discovery_surfaces(tmp_path: Path) -> None:
    project = tmp_path / "project"
    metadata = project / ".git" / "worktrees" / "copy"
    metadata.mkdir(parents=True)
    copy = tmp_path / "work-copy"
    copy.mkdir()
    (copy / ".git").write_text(f"gitdir: {metadata}\n")
    (metadata / "commondir").write_text("../..\n")
    (metadata / "gitdir").write_text(f"{copy / '.git'}\n")
    (project / "src").mkdir()
    scanned = scan_project_folders([str(tmp_path)])
    assert str(project) in scanned
    assert str(copy) not in scanned
    assert str(project / "src") not in scanned
    discovery = ProjectDiscovery([make_root(path=str(tmp_path))])
    snapshot = discovery.snapshot()
    assert {p.identity.name for p in snapshot.projects} == {"project"}
    # Explicit history continues to offer it; no existing session is moved.
    assert rank_folders("work-copy", candidates=scanned, recents=[str(copy)])[0] == str(copy)


def test_git_file_is_not_enough_to_hide_a_project(tmp_path: Path) -> None:
    for name, content in (("submodule", "gitdir: ../.git/modules/sub"), ("broken", "bad")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / ".git").write_text(content)
    assert {Path(p).name for p in scan_project_folders([str(tmp_path)])} >= {"submodule", "broken"}
