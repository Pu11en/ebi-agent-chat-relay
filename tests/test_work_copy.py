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


async def test_keep_works_when_the_plan_was_never_saved(repo: Path, tmp_path: Path) -> None:
    (repo / "NEW-PLAN.md").write_text("- [ ] a\n")  # written and built in one breath
    copy = await wc.create_work_copy(repo, repo / "NEW-PLAN.md", root=tmp_path / "copies")
    copy.plan_path.write_text("- [x] a\n")
    _git(copy.path, "commit", "-qam", "tick")

    ok, msg = await wc.keep_work(copy)

    assert ok, msg
    assert (repo / "NEW-PLAN.md").read_text() == "- [x] a\n"


async def test_side_copies_merge_back_into_the_build(repo: Path, tmp_path: Path) -> None:
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    a = await wc.create_side_copy(copy, "a")
    b = await wc.create_side_copy(copy, "b")
    (a.path / "a.txt").write_text("a\n")
    _git(a.path, "add", ".")
    _git(a.path, "commit", "-qm", "a")
    (b.path / "app.txt").write_text("changed by b\n")
    _git(b.path, "commit", "-qam", "b")

    assert await wc.merge_side_copy(copy, a)
    assert await wc.merge_side_copy(copy, b)
    assert (copy.path / "a.txt").exists()
    assert (copy.path / "app.txt").read_text() == "changed by b\n"
    assert not a.path.exists() and not b.path.exists()


async def test_a_clashing_side_copy_is_refused_and_nothing_is_half_merged(
    repo: Path, tmp_path: Path
) -> None:
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    a = await wc.create_side_copy(copy, "a")
    b = await wc.create_side_copy(copy, "b")
    for side, text in ((a, "from a\n"), (b, "from b\n")):
        (side.path / "app.txt").write_text(text)
        _git(side.path, "commit", "-qam", "edit")

    assert await wc.merge_side_copy(copy, a)
    assert not await wc.merge_side_copy(copy, b)
    assert (copy.path / "app.txt").read_text() == "from a\n"
    assert _git(copy.path, "status", "--porcelain").strip() == ""


async def test_a_leftover_side_copy_from_a_restart_is_replaced(repo: Path, tmp_path: Path) -> None:
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    await wc.create_side_copy(copy, "a")  # left behind by an interrupted group
    again = await wc.create_side_copy(copy, "a")
    assert again.path.exists()


async def test_saving_a_new_plan_never_commits_what_the_person_staged(
    repo: Path, tmp_path: Path
) -> None:
    (repo / "NEW-PLAN.md").write_text("- [ ] a\n")
    copy = await wc.create_work_copy(repo, repo / "NEW-PLAN.md", root=tmp_path / "copies")
    copy.plan_path.write_text("- [x] a\n")
    _git(copy.path, "commit", "-qam", "tick")
    (repo / "app.txt").write_text("staged by the person\n")
    _git(repo, "add", "app.txt")

    ok, msg = await wc.keep_work(copy)

    assert not ok and "unsaved" in msg  # refused, as before
    assert "app.txt" in _git(repo, "diff", "--cached", "--name-only")  # still staged, not committed


async def test_project_copy_needs_no_plan_and_takes_side_copies(repo: Path, tmp_path: Path) -> None:
    """T11b: a manifest build works in other projects too, which hold no plan file."""
    copy = await wc.create_project_copy(repo, label="website", root=tmp_path / "copies")
    assert copy.path.is_dir() and copy.path != repo
    assert copy.branch.startswith("gowork/website-")
    assert _git(copy.path, "rev-parse", "HEAD").strip() == _git(repo, "rev-parse", "HEAD").strip()

    side = await wc.create_side_copy(copy, "catalog-page")
    (side.path / "page.txt").write_text("hi")
    _git(side.path, "add", ".")
    _git(side.path, "commit", "-qm", "page")
    assert await wc.side_has_new_work(copy, side)
    assert await wc.merge_side_copy(copy, side)
    assert (copy.path / "page.txt").read_text() == "hi"
    assert await wc.head_commit(copy.path) == _git(copy.path, "rev-parse", "HEAD").strip()
    await wc.remove_work_copy(copy)
    assert not copy.path.exists()


async def test_a_clash_can_keep_the_side_copy_for_repair(repo: Path, tmp_path: Path) -> None:
    """T15: conflict keeps both versions — the build's and the worker's — nothing is thrown away."""
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    side = await wc.create_side_copy(copy, "clash")
    (copy.path / "app.txt").write_text("build version\n")
    _git(copy.path, "commit", "-qam", "build edits app")
    (side.path / "app.txt").write_text("worker version\n")
    _git(side.path, "commit", "-qam", "worker edits app")

    assert await wc.merge_side_copy(copy, side, keep_on_clash=True) is False
    assert (copy.path / "app.txt").read_text() == "build version\n"  # the build's copy is clean
    assert side.path.is_dir() and (side.path / "app.txt").read_text() == "worker version\n"
    assert _git(copy.path, "status", "--porcelain").strip() == ""
    assert not await wc.side_is_merged(copy, side)


async def test_an_already_merged_side_is_recognised_without_merging_twice(
    repo: Path, tmp_path: Path
) -> None:
    """T15: a crash after the merge but before the ledger write is reconciled, not repeated."""
    copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    side = await wc.create_side_copy(copy, "once")
    (side.path / "new.txt").write_text("x\n")
    _git(side.path, "add", ".")
    _git(side.path, "commit", "-qm", "new file")
    assert not await wc.side_is_merged(copy, side)

    _git(copy.path, "merge", "--no-edit", side.branch)  # merged… then a crash before cleanup
    head = _git(copy.path, "rev-parse", "HEAD").strip()
    assert await wc.side_is_merged(copy, side)  # the branch is an ancestor now
    assert not await wc.side_has_new_work(copy, side)  # so a second merge has nothing to do
    assert await wc.merge_side_copy(copy, side, keep_on_clash=True)  # harmless, only cleans up
    assert _git(copy.path, "rev-parse", "HEAD").strip() == head
