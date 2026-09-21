"""One branch and one checkout per simultaneous task, rooted at its foundation.

A parallel build is only safe while no two workers can write the same file, and
the cheapest way to guarantee that is to never let two workers share a checkout.
So every task that runs at the same time gets the repository's ordinary session
layout — a ``session/<thread-id>`` branch and a ``.worktrees/wt-<thread-id>``
worktree — cut from the *exact* integration commit the scheduler recorded as
containing all of its dependencies. Nothing here is a temporary side directory
hidden under another worker; each workspace has the same Discord identity,
branch, and cleanup rules as any other session.

Three facts are verified rather than assumed, because each one is a way a
worker could quietly build on the wrong thing:

* the session branch descends from the foundation — a branch that predates the
  dependency it needs is refused, not rebased;
* the checkout in the slot belongs to *this* repository and sits on *its*
  session branch — a stray directory or another project's worktree is refused,
  not adopted;
* a checkout is removed only when it is clean, and the branch is never deleted
  by this module — the branch is the durable evidence the verifier reads after
  Ebi has already removed the checkout at turn end.

Recovery is the same call as creation: ``ensure_worker_workspace`` reuses a
checkout that exists (keeping partial work), re-creates one from a branch that
survived cleanup, and creates both only when neither exists. The result says
which happened, so a caller can tell a fresh start from a resumed one.

Git runs through ``claude_code_core.work_copy``'s subprocess helper — never a
shell — and the only paths it is handed are the validated layout below.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path

from claude_code_core import work_copy

WORKTREES_DIR = ".worktrees"
BRANCH_PREFIX = "session/"
CHECKOUT_PREFIX = "wt-"

_THREAD_ID_RE = re.compile(r"^[0-9]{1,25}$")
_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class WorkerWorkspaceError(Exception):
    """The workspace cannot be created or reused safely, so it is not touched."""


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """Where one thread's branch and checkout live. Pure; nothing is created."""

    repo: Path
    thread_id: str
    branch: str
    path: Path


@dataclass(frozen=True, slots=True)
class WorkerWorkspace:
    """A checkout a worker may write to, and the facts that were verified about it."""

    repo: Path
    task_id: str
    thread_id: str
    branch: str
    path: Path
    foundation: str
    head: str
    recovered: bool

    @property
    def layout(self) -> WorkspaceLayout:
        return WorkspaceLayout(
            repo=self.repo, thread_id=self.thread_id, branch=self.branch, path=self.path
        )


@dataclass(frozen=True, slots=True)
class WorkspaceEvidence:
    """What Git still knows about a task after its checkout may be gone.

    ``branch_tip`` is the durable result; ``clean`` is ``None`` when there is no
    checkout to be clean, which is the normal state after Ebi's turn-end cleanup
    and must not be read as lost work.
    """

    branch: str
    path: Path
    branch_tip: str | None
    descends_from_foundation: bool
    checkout_exists: bool
    head: str | None
    clean: bool | None


def _thread_id(raw: object) -> str:
    if not isinstance(raw, str) or not _THREAD_ID_RE.fullmatch(raw):
        raise WorkerWorkspaceError(f"thread id must be a Discord snowflake, got {raw!r}")
    return raw


def _sha(label: str, raw: object) -> str:
    if not isinstance(raw, str) or not _SHA_RE.fullmatch(raw):
        raise WorkerWorkspaceError(f"{label} must be a full 40-character commit SHA, got {raw!r}")
    return raw


def _task_id(raw: object) -> str:
    if not isinstance(raw, str) or not _TASK_ID_RE.fullmatch(raw):
        raise WorkerWorkspaceError(f"task id must be a short identifier, got {raw!r}")
    return raw


async def _git(cwd: Path, *args: str) -> str:
    try:
        return await work_copy._git(cwd, *args)
    except work_copy.WorkCopyError as exc:
        raise WorkerWorkspaceError(str(exc)) from exc


async def _toplevel(repo_dir: Path) -> Path:
    try:
        top = await work_copy._git(repo_dir, "rev-parse", "--show-toplevel")
    except work_copy.WorkCopyError as exc:
        raise WorkerWorkspaceError(f"{repo_dir} is not inside a git repository") from exc
    return Path(top.strip()).resolve()


def _layout(repo: Path, thread_id: str, worktree_root: Path | None) -> WorkspaceLayout:
    root = (worktree_root or repo / WORKTREES_DIR).resolve()
    return WorkspaceLayout(
        repo=repo,
        thread_id=thread_id,
        branch=f"{BRANCH_PREFIX}{thread_id}",
        path=root / f"{CHECKOUT_PREFIX}{thread_id}",
    )


def workspace_layout(
    repo: Path, thread_id: str, *, worktree_root: Path | None = None
) -> WorkspaceLayout:
    """The branch and checkout path for *thread_id*, without touching Git.

    The repository path is taken as given here; ``ensure_worker_workspace``
    resolves it to the toplevel first.
    """
    return _layout(Path(repo), _thread_id(thread_id), worktree_root)


async def _branch_tip(repo: Path, branch: str) -> str | None:
    try:
        return (await work_copy._git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")).strip()
    except work_copy.WorkCopyError:
        return None


async def _is_ancestor(repo: Path, ancestor: str, commit: str) -> bool:
    try:
        await work_copy._git(repo, "merge-base", "--is-ancestor", ancestor, commit)
    except work_copy.WorkCopyError:
        return False
    return True


async def _commit_exists(repo: Path, sha: str) -> bool:
    try:
        await work_copy._git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    except work_copy.WorkCopyError:
        return False
    return True


async def _verify_checkout(repo: Path, layout: WorkspaceLayout) -> str:
    """The checkout in the slot is ours and on its session branch; return its HEAD."""
    try:
        top = await work_copy._git(layout.path, "rev-parse", "--show-toplevel")
        common = await work_copy._git(
            layout.path, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
    except work_copy.WorkCopyError as exc:
        raise WorkerWorkspaceError(
            f"{layout.path} exists but is not a git checkout; not reusing it"
        ) from exc
    if Path(top.strip()).resolve().as_posix() != layout.path.as_posix():
        raise WorkerWorkspaceError(
            f"{layout.path} is a plain directory inside the project, not its own checkout"
        )
    expected = await _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(common.strip()).resolve().as_posix() != Path(expected.strip()).resolve().as_posix():
        raise WorkerWorkspaceError(f"{layout.path} belongs to another repository")
    try:
        ref = (await work_copy._git(layout.path, "symbolic-ref", "HEAD")).strip()
    except work_copy.WorkCopyError as exc:
        raise WorkerWorkspaceError(f"{layout.path} is detached, not on its session branch") from exc
    if ref != f"refs/heads/{layout.branch}":
        raise WorkerWorkspaceError(
            f"{layout.path} is on {ref}, not its session branch {layout.branch}"
        )
    return (await _git(layout.path, "rev-parse", "HEAD")).strip()


async def ensure_worker_workspace(
    repo_dir: Path,
    *,
    task_id: str,
    thread_id: str,
    foundation: str,
    worktree_root: Path | None = None,
) -> WorkerWorkspace:
    """Create the task's branch and checkout, or reattach to the ones that exist.

    Order of preference, each verified before use:

    1. The checkout exists → reuse it (partial work is kept).
    2. Only the branch exists → re-create the checkout from it (Ebi removed a
       clean checkout at turn end; the commits are on the branch).
    3. Neither exists → create the branch at *foundation* and check it out.

    A branch that does not contain *foundation* is refused: the scheduler chose
    that commit because it holds every integrated dependency, and building on
    an older base would let a verified-looking result miss them.
    """
    task = _task_id(task_id)
    thread = _thread_id(thread_id)
    base = _sha("foundation", foundation)
    repo = await _toplevel(Path(repo_dir))
    layout = _layout(repo, thread, worktree_root)
    if not await _commit_exists(repo, base):
        raise WorkerWorkspaceError(f"foundation {base} is not a commit in {repo}")

    tip = await _branch_tip(repo, layout.branch)
    if tip is not None and not await _is_ancestor(repo, base, tip):
        raise WorkerWorkspaceError(
            f"branch {layout.branch} does not descend from foundation {base}; "
            "it is not an ancestor of the branch tip"
        )

    if layout.path.exists():
        if tip is None:
            raise WorkerWorkspaceError(
                f"{layout.path} exists without branch {layout.branch}; not adopting it"
            )
        head = await _verify_checkout(repo, layout)
        return WorkerWorkspace(
            repo=repo,
            task_id=task,
            thread_id=thread,
            branch=layout.branch,
            path=layout.path,
            foundation=base,
            head=head,
            recovered=True,
        )

    layout.path.parent.mkdir(parents=True, exist_ok=True)
    # A directory deleted behind Git's back leaves a stale registration that
    # would make `worktree add` refuse the same path.
    with contextlib.suppress(work_copy.WorkCopyError):
        await work_copy._git(repo, "worktree", "prune")
    if tip is None:
        await _git(repo, "worktree", "add", "-q", "-b", layout.branch, str(layout.path), base)
        head, recovered = base, False
    else:
        await _git(repo, "worktree", "add", "-q", str(layout.path), layout.branch)
        head, recovered = tip, True
    return WorkerWorkspace(
        repo=repo,
        task_id=task,
        thread_id=thread,
        branch=layout.branch,
        path=layout.path,
        foundation=base,
        head=head,
        recovered=recovered,
    )


async def inspect_worker_workspace(
    repo_dir: Path,
    *,
    thread_id: str,
    foundation: str,
    worktree_root: Path | None = None,
) -> WorkspaceEvidence:
    """Report the durable Git evidence for a task without creating anything."""
    thread = _thread_id(thread_id)
    base = _sha("foundation", foundation)
    repo = await _toplevel(Path(repo_dir))
    layout = _layout(repo, thread, worktree_root)
    tip = await _branch_tip(repo, layout.branch)
    descends = tip is not None and await _is_ancestor(repo, base, tip)
    exists = layout.path.exists()
    head: str | None = None
    clean: bool | None = None
    if exists:
        head = await _verify_checkout(repo, layout)
        clean = not (await _git(layout.path, "status", "--porcelain", "--ignored")).strip()
    return WorkspaceEvidence(
        branch=layout.branch,
        path=layout.path,
        branch_tip=tip,
        descends_from_foundation=descends,
        checkout_exists=exists,
        head=head,
        clean=clean,
    )


async def release_worker_workspace(workspace: WorkerWorkspace) -> bool:
    """Remove the checkout when it is clean; never the branch. True when it is gone.

    Tracked, untracked *and* ignored files all keep the checkout: the person may
    have a local log or a half-written note there, and the branch cannot carry
    those. An already-removed checkout counts as released.
    """
    layout = workspace.layout
    if not layout.path.exists():
        with contextlib.suppress(work_copy.WorkCopyError):
            await work_copy._git(layout.repo, "worktree", "prune")
        return True
    await _verify_checkout(layout.repo, layout)
    if (await _git(layout.path, "status", "--porcelain", "--ignored")).strip():
        return False
    if await _branch_tip(layout.repo, layout.branch) is None:
        raise WorkerWorkspaceError(
            f"branch {layout.branch} is missing; the checkout is the only copy"
        )
    await _git(layout.repo, "worktree", "remove", str(layout.path))
    return not layout.path.exists()
