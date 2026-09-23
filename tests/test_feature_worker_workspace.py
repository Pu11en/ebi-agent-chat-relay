"""A worker's workspace is a claim about Git, so it is tested against real Git.

Every simultaneously active task gets its own ``session/<thread-id>`` branch and
its own ``.worktrees/wt-<thread-id>`` checkout, both rooted at the exact
foundation commit the scheduler recorded. These tests use temporary repositories
and pin the properties the rest of the build leans on:

* two tasks never share a branch, a checkout, or an identity;
* the branch descends from the foundation, and a branch that does not is refused;
* a checkout that belongs to another repository, or is not on its session
  branch, is refused rather than reused;
* a clean checkout may be removed once the branch is durable, a dirty one never;
* a checkout that was removed is recovered from its branch without treating the
  task's work as lost.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from extensions.feature_workflow.worker_workspace import (
    WorkerWorkspace,
    WorkerWorkspaceError,
    ensure_worker_workspace,
    inspect_worker_workspace,
    release_worker_workspace,
    workspace_layout,
)

THREAD_A = "1551148966035333194"
THREAD_B = "1551148966035333195"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-qm", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _commit(r, "README.md", "hello\n")
    return r


@pytest.fixture
def foundation(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


class TestLayout:
    def test_layout_is_the_session_convention_inside_the_project(self, repo: Path) -> None:
        layout = workspace_layout(repo, THREAD_A)
        assert layout.branch == f"session/{THREAD_A}"
        assert layout.path == repo / ".worktrees" / f"wt-{THREAD_A}"

    @pytest.mark.parametrize("thread_id", ["", "abc", "12 34", "-1", "../x"])
    def test_thread_id_must_be_a_snowflake(self, repo: Path, thread_id: str) -> None:
        with pytest.raises(WorkerWorkspaceError):
            workspace_layout(repo, thread_id)


class TestCreate:
    async def test_two_tasks_get_distinct_branches_checkouts_and_identities(
        self, repo: Path, foundation: str
    ) -> None:
        first = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        second = await ensure_worker_workspace(
            repo, task_id="1.2", thread_id=THREAD_B, foundation=foundation
        )
        assert isinstance(first, WorkerWorkspace)
        assert first.branch != second.branch
        assert first.path != second.path
        assert first.task_id == "1.1" and second.task_id == "1.2"
        assert first.foundation == second.foundation == foundation
        for workspace in (first, second):
            assert workspace.path.is_dir()
            assert _git(workspace.path, "rev-parse", "HEAD") == foundation
            assert _git(workspace.path, "symbolic-ref", "HEAD") == f"refs/heads/{workspace.branch}"
            assert not workspace.recovered
        # The project's own checkout is untouched.
        assert _git(repo, "symbolic-ref", "HEAD") != f"refs/heads/{first.branch}"

    async def test_branch_is_rooted_at_the_foundation_not_at_head(self, repo: Path) -> None:
        foundation = _git(repo, "rev-parse", "HEAD")
        later = _commit(repo, "later.txt", "after the foundation\n")
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        assert _git(workspace.path, "rev-parse", "HEAD") == foundation
        assert _git(repo, "rev-parse", workspace.branch) == foundation != later
        assert not (workspace.path / "later.txt").exists()

    async def test_works_from_a_subdirectory_of_the_project(
        self, repo: Path, foundation: str
    ) -> None:
        (repo / "pkg").mkdir()
        workspace = await ensure_worker_workspace(
            repo / "pkg", task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        assert workspace.repo == repo.resolve()
        assert workspace.path == repo.resolve() / ".worktrees" / f"wt-{THREAD_A}"

    async def test_layout_uses_posix_separators_in_git_paths(
        self, repo: Path, foundation: str
    ) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        listed = _git(repo, "worktree", "list", "--porcelain")
        assert workspace.path.resolve().as_posix() in listed.replace("\\", "/")


class TestRefusals:
    @pytest.mark.parametrize("foundation", ["", "HEAD", "abc123", "0" * 39, "G" * 40])
    async def test_foundation_must_be_a_full_sha(self, repo: Path, foundation: str) -> None:
        with pytest.raises(WorkerWorkspaceError):
            await ensure_worker_workspace(
                repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
            )
        assert not (repo / ".worktrees").exists()

    async def test_foundation_must_exist_in_the_repository(self, repo: Path) -> None:
        with pytest.raises(WorkerWorkspaceError, match="foundation"):
            await ensure_worker_workspace(
                repo, task_id="1.1", thread_id=THREAD_A, foundation="0" * 40
            )
        assert not (repo / ".worktrees").exists()

    @pytest.mark.parametrize("task_id", ["", " ", "a b", "x/../y", "a" * 65])
    async def test_task_id_must_be_a_short_identifier(
        self, repo: Path, foundation: str, task_id: str
    ) -> None:
        with pytest.raises(WorkerWorkspaceError):
            await ensure_worker_workspace(
                repo, task_id=task_id, thread_id=THREAD_A, foundation=foundation
            )

    async def test_not_a_repository(self, tmp_path: Path) -> None:
        with pytest.raises(WorkerWorkspaceError):
            await ensure_worker_workspace(
                tmp_path, task_id="1.1", thread_id=THREAD_A, foundation="0" * 40
            )

    async def test_branch_that_does_not_descend_from_the_foundation_is_refused(
        self, repo: Path
    ) -> None:
        old = _git(repo, "rev-parse", "HEAD")
        _git(repo, "branch", f"session/{THREAD_A}", old)
        foundation = _commit(repo, "newer.txt", "the dependency landed\n")
        with pytest.raises(WorkerWorkspaceError, match="ancestor"):
            await ensure_worker_workspace(
                repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
            )
        assert not (repo / ".worktrees" / f"wt-{THREAD_A}").exists()

    async def test_checkout_of_another_repository_is_refused(
        self, repo: Path, foundation: str, tmp_path: Path
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        _git(other, "init", "-q")
        _git(other, "config", "user.email", "t@t")
        _git(other, "config", "user.name", "t")
        _commit(other, "x.txt", "x\n")
        target = repo / ".worktrees" / f"wt-{THREAD_A}"
        target.parent.mkdir()
        _git(other, "worktree", "add", "-q", "-b", f"session/{THREAD_A}", str(target), "HEAD")
        _git(repo, "branch", f"session/{THREAD_A}", foundation)
        with pytest.raises(WorkerWorkspaceError, match="another repository"):
            await ensure_worker_workspace(
                repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
            )

    async def test_plain_directory_in_the_slot_is_refused(
        self, repo: Path, foundation: str
    ) -> None:
        target = repo / ".worktrees" / f"wt-{THREAD_A}"
        target.mkdir(parents=True)
        (target / "stray.txt").write_text("not a checkout\n")
        with pytest.raises(WorkerWorkspaceError):
            await ensure_worker_workspace(
                repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
            )
        assert (target / "stray.txt").exists()

    async def test_checkout_on_the_wrong_branch_is_refused(
        self, repo: Path, foundation: str
    ) -> None:
        target = repo / ".worktrees" / f"wt-{THREAD_A}"
        target.parent.mkdir()
        _git(repo, "worktree", "add", "-q", "-b", "feature/other", str(target), foundation)
        _git(repo, "branch", f"session/{THREAD_A}", foundation)
        with pytest.raises(WorkerWorkspaceError, match="session branch"):
            await ensure_worker_workspace(
                repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
            )


class TestRecovery:
    async def test_existing_checkout_is_reused_and_partial_work_is_kept(
        self, repo: Path, foundation: str
    ) -> None:
        first = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        (first.path / "partial.txt").write_text("half done\n")
        again = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        assert again.path == first.path and again.branch == first.branch
        assert again.recovered
        assert (again.path / "partial.txt").read_text() == "half done\n"

    async def test_checkout_removed_after_commit_is_recovered_from_the_branch(
        self, repo: Path, foundation: str
    ) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        result = _commit(workspace.path, "work.txt", "the task's work\n")
        # Ebi removes clean session checkouts at turn end; the branch stays.
        _git(repo, "worktree", "remove", str(workspace.path))
        assert not workspace.path.exists()

        recovered = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        assert recovered.recovered
        assert recovered.path == workspace.path
        assert _git(recovered.path, "rev-parse", "HEAD") == result
        assert (recovered.path / "work.txt").read_text() == "the task's work\n"

    async def test_stale_worktree_registration_does_not_block_recovery(
        self, repo: Path, foundation: str
    ) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        _commit(workspace.path, "work.txt", "x\n")
        # A crash can delete the directory without telling Git.
        import shutil

        shutil.rmtree(workspace.path)
        recovered = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        assert recovered.recovered and (recovered.path / "work.txt").exists()

    async def test_inspect_reports_durable_evidence_without_a_checkout(
        self, repo: Path, foundation: str
    ) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        result = _commit(workspace.path, "work.txt", "x\n")
        _git(repo, "worktree", "remove", str(workspace.path))

        evidence = await inspect_worker_workspace(repo, thread_id=THREAD_A, foundation=foundation)
        assert evidence.branch == workspace.branch
        assert evidence.branch_tip == result
        assert evidence.descends_from_foundation
        assert not evidence.checkout_exists
        assert evidence.clean is None

    async def test_inspect_reports_a_missing_branch(self, repo: Path, foundation: str) -> None:
        evidence = await inspect_worker_workspace(repo, thread_id=THREAD_A, foundation=foundation)
        assert evidence.branch_tip is None
        assert not evidence.descends_from_foundation
        assert not evidence.checkout_exists

    async def test_inspect_reports_a_dirty_checkout(self, repo: Path, foundation: str) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        (workspace.path / "dirty.txt").write_text("unsaved\n")
        evidence = await inspect_worker_workspace(repo, thread_id=THREAD_A, foundation=foundation)
        assert evidence.checkout_exists and evidence.clean is False
        assert evidence.head == foundation


class TestRelease:
    async def test_clean_checkout_is_removed_and_the_branch_stays(
        self, repo: Path, foundation: str
    ) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        result = _commit(workspace.path, "work.txt", "x\n")
        assert await release_worker_workspace(workspace)
        assert not workspace.path.exists()
        assert _git(repo, "rev-parse", workspace.branch) == result

    async def test_dirty_checkout_is_kept(self, repo: Path, foundation: str) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        (workspace.path / "unsaved.txt").write_text("not committed\n")
        assert not await release_worker_workspace(workspace)
        assert (workspace.path / "unsaved.txt").exists()

    async def test_ignored_files_also_keep_the_checkout(self, repo: Path, foundation: str) -> None:
        _commit(repo, ".gitignore", "*.log\n")
        head = _git(repo, "rev-parse", "HEAD")
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=head
        )
        (workspace.path / "debug.log").write_text("local only\n")
        assert not await release_worker_workspace(workspace)
        assert workspace.path.exists()

    async def test_release_of_an_already_removed_checkout_is_success(
        self, repo: Path, foundation: str
    ) -> None:
        workspace = await ensure_worker_workspace(
            repo, task_id="1.1", thread_id=THREAD_A, foundation=foundation
        )
        _git(repo, "worktree", "remove", str(workspace.path))
        assert await release_worker_workspace(workspace)
