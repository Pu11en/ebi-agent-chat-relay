"""Remember running /gowork builds so a bot restart doesn't end them.

Everything a build needs to carry on lives on disk already — the plan's ticked
boxes and the build's own copy of the project. This file only remembers which
builds were running and where they report, so on startup the bot can pick each
one up at its next unticked task, in the same worker thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from claude_code_core.work_copy import is_gowork_branch, is_under_work_root

logger = logging.getLogger(__name__)

#: A build id names the ledger file ``builds/<build_id>.json``: no separators, ever.
_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

DEFAULT_PATH = Path(
    os.environ.get(
        "CCDB_GOWORK_STATE", Path.home() / ".local" / "state" / "ccdb" / "gowork-loops.json"
    )
)


@dataclass(frozen=True)
class LoopRecord:
    repo_dir: str
    plan_path: str
    copy_path: str
    copy_plan: str
    branch: str
    worker_thread_id: int
    report_channel_id: int
    notify_user_id: int | None = None
    harness: str | None = None
    model: str | None = None
    #: Used automatically when the build's AI hits its usage limit (no question asked).
    fallback_harness: str | None = None
    fallback_model: str | None = None
    #: A plan with no Goal line starts with the goal interview (people starting a
    #: build ask for it; an API caller that already knows the goal need not).
    ask_goal: bool = False
    #: Before every step a quick AI picks which AI does it (idea 3).
    per_step_ai: bool = False
    #: Started from the build queue (idea 6): the line moves on when it waits.
    queued: bool = False
    #: "cheap", "balanced" or "careful" — where the build sits between cost and quality.
    mode: str = "balanced"
    #: Stable identity of this build. Several builds may run in one project, so the
    #: project path is not enough; the worker thread is unique per build and survives
    #: restarts, which is what legacy records (saved without an id) derive it from.
    build_id: str = ""

    def __post_init__(self) -> None:
        if not self.build_id:
            object.__setattr__(self, "build_id", f"thread-{self.worker_thread_id}")


def record_problem(record: LoopRecord, work_root: Path | None) -> str | None:
    """Why a record read from disk must not be acted on, or None when it is sound.

    The record names a worktree to remove with ``--force``, a branch to delete and
    a ledger file to open, so each of those is checked against the shape this code
    writes (E2). With *work_root*, the copy must also lie inside that area.
    """
    if not _BUILD_ID_RE.fullmatch(record.build_id):
        return f"build_id {record.build_id!r} is not a plain name"
    if not is_gowork_branch(record.branch):
        return f"branch {record.branch!r} is not a gowork build branch"
    copy = Path(record.copy_path)
    if work_root is not None and not is_under_work_root(copy, work_root):
        return f"copy_path {record.copy_path!r} is not inside the work-copy area {work_root}"
    if not is_under_work_root(Path(record.copy_plan), copy):
        return f"copy_plan {record.copy_plan!r} is not inside copy_path"
    return None


class LoopStore:
    """A small JSON file: one record per running build."""

    def __init__(self, path: Path | None = None, *, work_root: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        #: Where every build's copy lives; a record whose copy is elsewhere is skipped.
        #: None = shape checks only (the cog always sets it, ``DEFAULT_ROOT`` by default).
        self.work_root = work_root

    def all(self) -> list[LoopRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning("gowork state file unreadable; nothing to resume", exc_info=True)
            return []
        records = []
        for item in raw if isinstance(raw, list) else []:
            try:
                record = LoopRecord(**item)
            except TypeError:
                logger.warning("skipping malformed gowork record: %r", item)
                continue
            problem = record_problem(record, self.work_root)
            if problem is not None:
                logger.warning("skipping untrusted gowork record %s: %s", record.build_id, problem)
                continue
            records.append(record)
        return records

    def get(self, build_id: str) -> LoopRecord | None:
        return next((r for r in self.all() if r.build_id == build_id), None)

    def for_repo(self, repo_dir: str) -> list[LoopRecord]:
        return [r for r in self.all() if r.repo_dir == repo_dir]

    def _write(self, records: list[LoopRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def save(self, record: LoopRecord) -> None:
        others = [r for r in self.all() if r.build_id != record.build_id]
        self._write([*others, record])

    def remove(self, build_id_or_repo_dir: str) -> None:
        """Forget one build by id, or — for callers that predate ids — every build of a repo."""
        records = self.all()
        kept = [r for r in records if r.build_id != build_id_or_repo_dir]
        if len(kept) == len(records):
            kept = [r for r in records if r.repo_dir != build_id_or_repo_dir]
        self._write(kept)

    def migrate(self) -> int:
        """Write derived build ids to disk. Repeatable; returns how many records lacked one."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return 0
        missing = sum(1 for item in raw if isinstance(item, dict) and not item.get("build_id"))
        if missing:
            self._write(self.all())
        return missing
