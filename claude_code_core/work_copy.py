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


async def remove_work_copy(copy: WorkCopy) -> None:
    """Delete the copy and its branch. Only after the work was kept or thrown away."""
    await _git(copy.source_repo, "worktree", "remove", "--force", str(copy.path))
    await _git(copy.source_repo, "branch", "-D", copy.branch)


async def keep_work(copy: WorkCopy) -> tuple[bool, str]:
    """ "Looks good": merge the build into the project, then remove the copy.

    Local only — nothing is pushed. Refuses (and loses nothing) when the
    project has unsaved changes or the merge conflicts.
    """
    status = await _git(copy.source_repo, "status", "--porcelain", "--untracked-files=no")
    if status.strip():
        return False, "your project has unsaved changes, so I didn't combine anything yet"
    try:
        await _git(copy.source_repo, "merge", "--no-edit", copy.branch)
    except WorkCopyError as exc:
        with contextlib.suppress(WorkCopyError):
            await _git(copy.source_repo, "merge", "--abort")
        return False, f"the work didn't combine cleanly ({exc})"
    await remove_work_copy(copy)
    return True, "added to your project (on this computer only — nothing went to GitHub)"


async def commit_all(path: Path, message: str) -> None:
    """Commit every tracked change in *path* (used when a fix task is added)."""
    await _git(path, "add", "-A")
    await _git(path, "commit", "-q", "-m", message)
