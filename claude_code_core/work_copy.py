"""The /gowork worker's own copy of the project (a git worktree on its own branch).

A build must never touch the real project before the person has tried the
result and said it's good, and it must not collide with other sessions working
in the same folder. So every build gets a separate checkout:

* it lives outside the project (``~/.local/state/ccdb/gowork/…`` by default),
  so the project's own ``git status`` never shows it;
* it is on a new ``gowork/<plan>-<time>`` branch cut from the project's HEAD;
* the plan is carried over even when it was never committed — planners often
  write the plan and start the build in one breath.

Merging the finished branch back, and removing the copy afterwards, are the
caller's decision (the try-it step); this module only creates and deletes.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(
    os.environ.get("CCDB_GOWORK_ROOT", Path.home() / ".local" / "state" / "ccdb" / "gowork")
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class WorkCopyError(RuntimeError):
    """The copy could not be made (not a git repo, git refused, …)."""


@dataclass(frozen=True)
class WorkCopy:
    source_repo: Path
    path: Path
    branch: str
    plan_path: Path


async def _git(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise WorkCopyError(f"git {' '.join(args)}: {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace")


async def create_work_copy(
    repo_dir: Path, plan_path: Path, *, root: Path | None = None
) -> WorkCopy:
    """Make a fresh worktree of *repo_dir* holding the current *plan_path*."""
    repo = Path((await _git(repo_dir, "rev-parse", "--show-toplevel")).strip()).resolve()
    plan = plan_path.resolve()
    try:
        rel = plan.relative_to(repo)
    except ValueError as exc:
        raise WorkCopyError(f"the plan is not inside {repo}") from exc

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _SLUG_RE.sub("-", plan.stem.lower()).strip("-")[:40] or "plan"
    branch = f"gowork/{slug}-{stamp}"
    path = (root or DEFAULT_ROOT) / f"{repo.name}-{slug}-{stamp}"
    path.parent.mkdir(parents=True, exist_ok=True)
    await _git(repo, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")

    copy_plan = path / rel
    content = plan.read_bytes()
    if not copy_plan.exists() or copy_plan.read_bytes() != content:
        copy_plan.parent.mkdir(parents=True, exist_ok=True)
        copy_plan.write_bytes(content)
        await _git(path, "add", "--", str(rel))
        await _git(path, "commit", "-q", "-m", "gowork: plan")
    return WorkCopy(source_repo=repo, path=path, branch=branch, plan_path=copy_plan)


async def create_project_copy(repo_dir: Path, *, label: str, root: Path | None = None) -> WorkCopy:
    """A fresh worktree of *repo_dir* for a manifest build's other project (no plan file).

    ``plan_path`` points at the copy's root so callers that only name the copy
    still have a path; nothing is written into the project.
    """
    repo = Path((await _git(repo_dir, "rev-parse", "--show-toplevel")).strip()).resolve()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _SLUG_RE.sub("-", label.lower()).strip("-")[:40] or "project"
    branch = f"gowork/{slug}-{stamp}"
    path = (root or DEFAULT_ROOT) / f"{repo.name}-{slug}-{stamp}"
    path.parent.mkdir(parents=True, exist_ok=True)
    await _git(repo, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    return WorkCopy(source_repo=repo, path=path, branch=branch, plan_path=path)


async def head_commit(path: Path) -> str:
    """The commit a copy is at right now."""
    return (await _git(path, "rev-parse", "HEAD")).strip()


async def remove_work_copy(copy: WorkCopy) -> None:
    """Delete the copy and its branch. Only after the work was kept or thrown away."""
    await _git(copy.source_repo, "worktree", "remove", "--force", str(copy.path))
    await _git(copy.source_repo, "branch", "-D", copy.branch)


async def keep_work(copy: WorkCopy, *, prefer_build: bool = False) -> tuple[bool, str]:
    """ "Looks good": merge the build into the project, then remove the copy.

    Local only — nothing is pushed. Refuses (and loses nothing) when the
    project has unsaved changes or the merge conflicts. With *prefer_build*,
    a clash is settled by taking the build's version of the clashing files —
    what a re-run of an already-kept plan needs.
    """
    if not copy.path.exists():
        return False, "the build's own copy is gone from this computer"
    plan_rel = str(copy.plan_path.relative_to(copy.path))
    real_plan = copy.source_repo / plan_rel
    if (
        real_plan.exists()
        and not (await _git(copy.source_repo, "ls-files", "--", plan_rel)).strip()
    ):
        # A plan written and built in one breath was never saved; save it now so
        # the finished plan can come back over it.
        await _git(copy.source_repo, "add", "--", plan_rel)
        await _git(
            copy.source_repo,
            "commit",
            "-q",
            "-m",
            "plan: saved before keeping the build",
            "--",
            plan_rel,  # only the plan: never sweep up what the person had staged
        )
    status = await _git(copy.source_repo, "status", "--porcelain", "--untracked-files=no")
    changed = [line[3:].strip() for line in status.splitlines() if line.strip()]
    if changed == [plan_rel]:
        # The planning session edited the plan while the build ran; the build
        # already took those edits in, so save them and carry on.
        await _git(
            copy.source_repo,
            "commit",
            "-q",
            "-m",
            "plan: edits from the planning session",
            "--",
            plan_rel,
        )
    elif changed:
        return False, "your project has unsaved changes, so I didn't combine anything yet"
    try:
        await _git(copy.source_repo, "merge", "--no-edit", copy.branch)
    except WorkCopyError as exc:
        conflicted = await _git(copy.source_repo, "diff", "--name-only", "--diff-filter=U")
        if prefer_build and conflicted.split():
            for path in conflicted.split():
                with contextlib.suppress(WorkCopyError):
                    await _git(copy.source_repo, "checkout", "--theirs", "--", path)
                await _git(copy.source_repo, "add", "--", path)
            await _git(copy.source_repo, "commit", "-q", "--no-edit")
        elif conflicted.split() == [plan_rel]:
            # Only the plan clashed: the build's copy has every step, ticked or not.
            await _git(copy.source_repo, "checkout", "--theirs", "--", plan_rel)
            await _git(copy.source_repo, "add", "--", plan_rel)
            await _git(copy.source_repo, "commit", "-q", "--no-edit")
        else:
            with contextlib.suppress(WorkCopyError):
                await _git(copy.source_repo, "merge", "--abort")
            return False, f"the work didn't combine cleanly ({exc})"
    await remove_work_copy(copy)
    return True, "added to your project (on this computer only — nothing went to GitHub)"


async def commit_all(path: Path, message: str) -> None:
    """Commit every tracked change in *path* (used when a fix task is added)."""
    await _git(path, "add", "-A")
    if not (await _git(path, "status", "--porcelain")).strip():
        return  # nothing changed
    await _git(path, "commit", "-q", "-m", message)


@dataclass(frozen=True)
class SideCopy:
    """One parallel step's own checkout, branched from the build's copy."""

    path: Path
    branch: str


def side_copy_for(copy: WorkCopy, name: str) -> SideCopy:
    """Where *name*'s side copy lives (deterministic, so a restart can find it)."""
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")[:48] or "step"
    return SideCopy(
        path=copy.path.parent / f"{copy.path.name}-{slug}", branch=f"{copy.branch}-{slug}"
    )


async def create_side_copy(copy: WorkCopy, name: str) -> SideCopy:
    """A fresh worktree of the build's current state for one parallel step."""
    existing = side_copy_for(copy, name)
    branch, path = existing.branch, existing.path
    # A restart mid-group can leave the last attempt behind; start clean.
    await remove_side_copy(copy, SideCopy(path=path, branch=branch))
    with contextlib.suppress(WorkCopyError):
        await _git(copy.path, "worktree", "prune")
    await _git(copy.path, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    return SideCopy(path=path, branch=branch)


async def remove_side_copy(copy: WorkCopy, side: SideCopy) -> None:
    with contextlib.suppress(WorkCopyError):
        await _git(copy.path, "worktree", "remove", "--force", str(side.path))
    with contextlib.suppress(WorkCopyError):
        await _git(copy.path, "branch", "-D", side.branch)


async def side_has_new_work(copy: WorkCopy, side: SideCopy) -> bool:
    """True when the side copy has commits the build's copy doesn't."""
    count = await _git(copy.path, "rev-list", "--count", f"HEAD..{side.branch}")
    return int(count.strip() or 0) > 0


async def merge_side_copy(copy: WorkCopy, side: SideCopy, *, keep_on_clash: bool = False) -> bool:
    """Bring a parallel step's work into the build's copy; False (and no change) on a clash.

    The side copy is removed after a successful merge. On a clash it is removed
    too — unless *keep_on_clash*, in which case the worker's version stays on its
    branch and worktree for repair while the build's copy is left exactly as it was.
    """
    try:
        await _git(copy.path, "merge", "--no-edit", side.branch)
    except WorkCopyError:
        with contextlib.suppress(WorkCopyError):
            await _git(copy.path, "merge", "--abort")
        if not keep_on_clash:
            await remove_side_copy(copy, side)
        return False
    await remove_side_copy(copy, side)
    return True


async def side_is_merged(copy: WorkCopy, side: SideCopy, *, base: str | None = None) -> bool:
    """True when the side branch's work is already in the build's copy (a merge that landed).

    With *base* (the commit the side started from) an untouched side — whose tip is
    still that base and so trivially an ancestor — is not mistaken for merged work.
    """
    try:
        if base is not None:
            tip = (await _git(copy.path, "rev-parse", side.branch)).strip()
            if tip == base:
                return False
        await _git(copy.path, "merge-base", "--is-ancestor", side.branch, "HEAD")
        return True
    except WorkCopyError:
        return False


# ---------------------------------------------------------------------------
# Automatic local integration (T23)
# ---------------------------------------------------------------------------

_integration_locks: dict[Path, asyncio.Lock] = {}


def integration_lock(repo: Path) -> asyncio.Lock:
    """One lock per project: builds for the same repository integrate one at a time."""
    key = repo.resolve()
    lock = _integration_locks.get(key)
    if lock is None:
        lock = _integration_locks[key] = asyncio.Lock()
    return lock


@dataclass(frozen=True)
class IntegrationResult:
    ok: bool
    message: str
    commit: str | None = None
    check_output: str = ""


async def _run_check(cwd: Path, argv: list[str], timeout: float = 600.0) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        return False, f"could not start the check: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return False, "the check took too long"
    text = out.decode(errors="replace")
    return proc.returncode == 0, "\n".join(text.strip().splitlines()[-8:])


async def integrate_build(copy: WorkCopy, *, check: list[str] | None) -> IntegrationResult:
    """Combine a finished build into its project — locally, safely, one build at a time.

    The merge and the combined check happen in a scratch checkout of the project, so
    the person's working tree is never used as a test bench. Only when the exact
    combined result passes is the project moved forward — and only by fast-forward,
    which git refuses rather than overwriting an unsaved edit or an untracked file.
    A clash, a failed check or a refused fast-forward leaves the build's copy in
    place for repair. Nothing is ever pushed.
    """
    if not copy.path.exists():
        return IntegrationResult(False, "the build's own copy is gone from this computer")
    repo = copy.source_repo
    async with integration_lock(repo):
        base = (await _git(repo, "rev-parse", "HEAD")).strip()
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        scratch = copy.path.parent / f"{repo.name}-integrate-{stamp}"
        try:
            await _git(repo, "worktree", "add", "-q", "--detach", str(scratch), base)
        except WorkCopyError as exc:
            return IntegrationResult(
                False, f"couldn't make a scratch checkout to combine in ({exc})"
            )
        try:
            try:
                await _git(scratch, "merge", "--no-edit", copy.branch)
            except WorkCopyError as exc:
                conflicted = (await _git(scratch, "diff", "--name-only", "--diff-filter=U")).split()
                with contextlib.suppress(WorkCopyError):
                    await _git(scratch, "merge", "--abort")
                names = ", ".join(conflicted) or str(exc)
                return IntegrationResult(
                    False,
                    f"the build's work clashes with what the project has now ({names}); "
                    "both versions are kept and nothing was changed",
                )
            merged = (await _git(scratch, "rev-parse", "HEAD")).strip()
            if check:
                ok, tail = await _run_check(scratch, check)
                if not ok:
                    return IntegrationResult(
                        False,
                        f"the combined result fails its check ({' '.join(check)}); "
                        "the project was not changed",
                        commit=merged,
                        check_output=tail,
                    )
            if (await _git(repo, "rev-parse", "HEAD")).strip() != base:
                return IntegrationResult(
                    False, "the project moved on while the build was being checked; try again"
                )
            try:
                await _git(repo, "merge", "--ff-only", merged)
            except WorkCopyError as exc:
                text = str(exc)
                return IntegrationResult(
                    False,
                    "your unsaved changes would be overwritten, so nothing was changed: "
                    + _overwritten_files(text),
                    commit=merged,
                )
        finally:
            with contextlib.suppress(WorkCopyError):
                await _git(repo, "worktree", "remove", "--force", str(scratch))
        await remove_work_copy(copy)
        return IntegrationResult(
            True,
            "added to your project (on this computer only — nothing went to GitHub)",
            commit=merged,
        )


def _overwritten_files(error_text: str) -> str:
    lines = [ln.strip() for ln in error_text.splitlines()]
    files = [
        ln
        for ln in lines
        if ln and not ln.startswith(("git ", "error:", "Please", "Aborting", "fatal"))
    ]
    return ", ".join(files) or error_text
