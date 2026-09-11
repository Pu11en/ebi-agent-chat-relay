"""Tests for claude_code_core.work_copy — the worker's own copy of the project."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_code_core import work_copy as wc


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "PLAN.md").write_text("- [ ] Task 1: a\n")
    (r / "app.txt").write_text("v1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


async def test_copy_is_separate_from_the_project(repo: Path, tmp_path: Path) -> None:
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    assert copy.path.is_dir() and copy.path != repo
    assert copy.plan_path == copy.path / "PLAN.md"
    (copy.path / "app.txt").write_text("v2\n")
    _git(copy.path, "commit", "-qam", "change in copy")
    assert (repo / "app.txt").read_text() == "v1\n"  # the real project is untouched
    assert copy.branch.startswith("gowork/")
    assert copy.branch in _git(repo, "branch", "--list", copy.branch)


async def test_uncommitted_plan_is_carried_into_the_copy(repo: Path, tmp_path: Path) -> None:
    (repo / "PLAN.md").write_text("- [ ] Task 1: a\n- [ ] Task 2: b\n")  # edited, not committed
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    assert "Task 2" in copy.plan_path.read_text()
    assert _git(copy.path, "status", "--porcelain", "--untracked-files=no") == ""


async def test_untracked_plan_in_a_subfolder_is_carried(repo: Path, tmp_path: Path) -> None:
    (repo / "docs").mkdir()
    (repo / "docs" / "new-plan.md").write_text("- [ ] x\n")
    copy = await wc.create_work_copy(repo, repo / "docs" / "new-plan.md", root=tmp_path / "c")
    assert copy.plan_path == copy.path / "docs" / "new-plan.md"
    assert copy.plan_path.read_text() == "- [ ] x\n"


async def test_remove_deletes_copy_and_branch(repo: Path, tmp_path: Path) -> None:
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    await wc.remove_work_copy(copy)
    assert not copy.path.exists()
    assert _git(repo, "branch", "--list", copy.branch).strip() == ""


async def test_not_a_repo_raises(tmp_path: Path) -> None:
    (tmp_path / "PLAN.md").write_text("- [ ] a\n")
    with pytest.raises(wc.WorkCopyError):
        await wc.create_work_copy(tmp_path, tmp_path / "PLAN.md", root=tmp_path / "c")
