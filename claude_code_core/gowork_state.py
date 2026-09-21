"""Durable task attempts and acceptance evidence for one Go Work build (T05).

The plan says what the tasks are; this file remembers what happened to each
one: which attempt is current, what it produced, which checks and reviews were
run, how many repairs it used, and — separately — whether the build *accepted*
the result. "Finished" is a worker's claim; "accepted" is the build's decision,
and a stale attempt can never turn the first into the second.

The document is replaced whole on every change (write to a temp file, then
rename), so a reader sees either the previous ledger or the new one, and an
interrupted write leaves only a stray temp file that the next open ignores.
Same discipline as the Lockin extension's run ledger, which the shipped
package cannot import.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from claude_code_core.gowork_plan import PlanTree

STATE_VERSION = 1
MAX_TEXT_CHARS = 2000
#: Automatic repairs a task gets over its whole life in a build (T18).
MAX_AUTO_REPAIRS = 1


class StaleAttemptError(ValueError):
    """The ledger refuses a change that would misstate what the build knows."""


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def _text(label: str, value: object) -> str:
    text = str(value).strip()
    if not text:
        raise StaleAttemptError(f"{label} must not be empty")
    return text[:MAX_TEXT_CHARS]


def _atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    """Everything durable about one task's current attempt."""

    task_id: str
    plan_id: str
    plan_version: int
    attempt: int
    attempt_id: str
    owned_files: tuple[str, ...]
    owned_resources: tuple[str, ...]
    status: TaskStatus = TaskStatus.PENDING
    result_commit: str | None = None
    checks: tuple[str, ...] = ()
    review: tuple[str, ...] = ()
    repairs: int = 0
    accepted: bool = False
    reason: str | None = None
    updated_at: str = ""
    #: The worker thread this attempt ran in, and whether it was archived after the save.
    thread_id: int | None = None
    archived: bool = False
    #: The commit the attempt's side copy started from (to tell saved work from none).
    base_commit: str | None = None
    #: Automatic repairs the task has used over all its attempts (T18: at most one).
    lineage_repairs: int = 0
    #: Why the previous attempt was blocked, for the next attempt's handoff.
    previous_failure: str | None = None
    #: Set when this attempt exists because the plan changed (T19), not because work failed.
    rework_reason: str | None = None
    #: The previous attempt's result commit, so a rework adjusts instead of restarting.
    previous_commit: str | None = None

    def to_json(self) -> dict:
        return {
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "attempt": self.attempt,
            "attempt_id": self.attempt_id,
            "owned_files": list(self.owned_files),
            "owned_resources": list(self.owned_resources),
            "status": self.status.value,
            "result_commit": self.result_commit,
            "checks": list(self.checks),
            "review": list(self.review),
            "repairs": self.repairs,
            "accepted": self.accepted,
            "reason": self.reason,
            "updated_at": self.updated_at,
            "thread_id": self.thread_id,
            "archived": self.archived,
            "base_commit": self.base_commit,
            "lineage_repairs": self.lineage_repairs,
            "previous_failure": self.previous_failure,
            "rework_reason": self.rework_reason,
            "previous_commit": self.previous_commit,
        }

    @classmethod
    def from_json(cls, value: Mapping) -> TaskAttempt:
        try:
            return cls(
                task_id=str(value["task_id"]),
                plan_id=str(value["plan_id"]),
                plan_version=int(value["plan_version"]),
                attempt=int(value["attempt"]),
                attempt_id=str(value["attempt_id"]),
                owned_files=tuple(value.get("owned_files") or ()),
                owned_resources=tuple(value.get("owned_resources") or ()),
                status=TaskStatus(str(value["status"])),
                result_commit=value.get("result_commit"),
                checks=tuple(value.get("checks") or ()),
                review=tuple(value.get("review") or ()),
                repairs=int(value.get("repairs", 0)),
                accepted=bool(value.get("accepted", False)),
                reason=value.get("reason"),
                updated_at=str(value.get("updated_at", "")),
                thread_id=int(value["thread_id"]) if value.get("thread_id") is not None else None,
                archived=bool(value.get("archived", False)),
                base_commit=value.get("base_commit"),
                lineage_repairs=int(value.get("lineage_repairs", 0)),
                previous_failure=value.get("previous_failure"),
                rework_reason=value.get("rework_reason"),
                previous_commit=value.get("previous_commit"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise StaleAttemptError(f"unreadable task attempt in the build state: {value}") from exc


def attempt_id(build_id: str, task_id: str, attempt: int) -> str:
    return f"{build_id}:{task_id}:{attempt}"


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What an edited plan changed in the ledger (T19)."""

    changed_plans: dict[str, int]
    reworked_tasks: tuple[str, ...]
    added_tasks: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.changed_plans or self.reworked_tasks or self.added_tasks)


class BuildState:
    """One build's task ledger on disk, safe to reopen at any moment."""

    def __init__(self, path: Path, tree: PlanTree, document: dict) -> None:
        self.path = path
        self.tree = tree
        self._document = document
        self.last_sync = SyncReport({}, (), ())

    @property
    def build_id(self) -> str:
        return str(self._document["build_id"])

    def _reload(self) -> None:
        """Re-read the file: several handles may share one ledger, all on one loop thread."""
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(document, dict) and document.get("build_id") == self.build_id:
            self._document = document

    @property
    def events(self) -> tuple[dict, ...]:
        self._reload()
        return tuple(self._document["events"])

    @property
    def records(self) -> tuple[TaskAttempt, ...]:
        """Every task of the current plan tree (a task an edit removed is kept but not listed)."""
        self._reload()
        current = {task.task_id for task in self.tree.tasks}
        return tuple(
            TaskAttempt.from_json(v)
            for key, v in self._document["tasks"].items()
            if not current or key in current
        )

    def __getitem__(self, task_id: str) -> TaskAttempt:
        self._reload()
        try:
            return TaskAttempt.from_json(self._document["tasks"][task_id])
        except KeyError as exc:
            raise KeyError(f"no task '{task_id}' in build {self.build_id}") from exc

    def __contains__(self, task_id: str) -> bool:
        self._reload()
        return task_id in self._document["tasks"]

    def plan_version(self, plan_id: str) -> int:
        self._reload()
        return int(self._document["plan_versions"][plan_id])

    def accepted_tasks(self) -> tuple[str, ...]:
        return tuple(r.task_id for r in self.records if r.accepted)

    # -- transitions ---------------------------------------------------------

    def begin(self, task_id: str) -> TaskAttempt:
        """A worker starts the current attempt."""
        record = self._require(task_id, TaskStatus.PENDING, "begin")
        return self._apply(
            task_id, replace(record, status=TaskStatus.RUNNING, reason=None), "running"
        )

    def submit_result(
        self, task_id: str, attempt: str, *, commit: str, checks: Iterable[str]
    ) -> TaskAttempt:
        """A worker reports a result commit and the checks it actually ran.

        Reporting the same result twice is harmless; a different result for an
        attempt that already finished, or any result from a stale attempt, is refused.
        """
        record = self._current(task_id, attempt)
        sha = _text("commit", commit)
        ran = tuple(_text("check", c) for c in checks)
        if record.status in (TaskStatus.FINISHED, TaskStatus.ACCEPTED):
            if record.result_commit == sha and record.checks == ran:
                return record
            raise StaleAttemptError(
                f"task '{task_id}' attempt {record.attempt} already finished with "
                f"{record.result_commit}; a new result needs a new attempt"
            )
        if record.status is not TaskStatus.RUNNING:
            raise StaleAttemptError(
                f"task '{task_id}' is {record.status.value}, not running; nothing to finish"
            )
        return self._apply(
            task_id,
            replace(record, status=TaskStatus.FINISHED, result_commit=sha, checks=ran, reason=None),
            "finished",
            commit=sha,
        )

    def record_review(
        self, task_id: str, attempt: str, *, verdict: str, notes: str = ""
    ) -> TaskAttempt:
        record = self._current(task_id, attempt)
        if record.status not in (TaskStatus.FINISHED, TaskStatus.ACCEPTED):
            raise StaleAttemptError(f"task '{task_id}' is {record.status.value}; nothing to review")
        line = _text("verdict", verdict) + (
            f": {notes.strip()[:MAX_TEXT_CHARS]}" if notes.strip() else ""
        )
        return self._apply(task_id, replace(record, review=(*record.review, line)), "reviewed")

    def record_repair(self, task_id: str, attempt: str, reason: str) -> TaskAttempt:
        record = self._current(task_id, attempt)
        return self._apply(
            task_id,
            replace(record, repairs=record.repairs + 1, reason=_text("reason", reason)),
            "repair",
        )

    def accept(self, task_id: str, attempt: str) -> TaskAttempt:
        """The build takes a finished result as the current answer for this task."""
        record = self._current(task_id, attempt)
        if record.status is TaskStatus.ACCEPTED:
            return record
        if record.status is not TaskStatus.FINISHED:
            raise StaleAttemptError(
                f"task '{task_id}' is {record.status.value}, not finished; nothing to accept"
            )
        current = self.plan_version(record.plan_id)
        if record.plan_version != current:
            raise StaleAttemptError(
                f"task '{task_id}' was built against plan '{record.plan_id}' version "
                f"{record.plan_version}, but the plan is now version {current}"
            )
        return self._apply(
            task_id, replace(record, status=TaskStatus.ACCEPTED, accepted=True), "accepted"
        )

    def block(self, task_id: str, reason: str) -> TaskAttempt:
        record = self[task_id]
        if record.status is TaskStatus.ACCEPTED:
            raise StaleAttemptError(f"task '{task_id}' is accepted; it cannot be blocked")
        return self._apply(
            task_id,
            replace(record, status=TaskStatus.BLOCKED, reason=_text("reason", reason)),
            "blocked",
        )

    def retry(self, task_id: str) -> TaskAttempt:
        """Start a fresh attempt with a new identity; the old one can never report again."""
        record = self[task_id]
        if record.status is TaskStatus.RUNNING:
            raise StaleAttemptError(f"task '{task_id}' is still running; stop it before retrying")
        return self._apply(task_id, self._next_attempt(record, record.lineage_repairs), "retried")

    def repairs_left(self, task_id: str) -> int:
        return max(0, MAX_AUTO_REPAIRS - self[task_id].lineage_repairs)

    def repair(self, task_id: str) -> TaskAttempt:
        """The one automatic repair: a fresh attempt that knows why the last one failed.

        Refused when the budget is spent or nothing failed — a person may still `retry`.
        """
        record = self[task_id]
        if record.status is not TaskStatus.BLOCKED:
            raise StaleAttemptError(f"task '{task_id}' is {record.status.value}; nothing to repair")
        if record.lineage_repairs >= MAX_AUTO_REPAIRS:
            raise StaleAttemptError(
                f"task '{task_id}' already used its {MAX_AUTO_REPAIRS} automatic repair"
            )
        fresh = self._next_attempt(record, record.lineage_repairs + 1)
        return self._apply(task_id, fresh, "repaired", reason=record.reason)

    def _next_attempt(self, record: TaskAttempt, lineage_repairs: int) -> TaskAttempt:
        number = record.attempt + 1
        return TaskAttempt(
            task_id=record.task_id,
            plan_id=record.plan_id,
            plan_version=self.plan_version(record.plan_id),
            attempt=number,
            attempt_id=attempt_id(self.build_id, record.task_id, number),
            owned_files=record.owned_files,
            owned_resources=record.owned_resources,
            lineage_repairs=lineage_repairs,
            previous_failure=record.reason,
        )

    def retry_after_block(self, task_id: str, reason: str) -> TaskAttempt:
        self.block(task_id, reason)
        return self.retry(task_id)

    def note_thread(
        self, task_id: str, attempt: str, *, thread_id: int, base_commit: str | None = None
    ) -> TaskAttempt:
        """Remember which worker thread the current attempt runs in (and where it started)."""
        record = self._current(task_id, attempt)
        return self._apply(
            task_id,
            replace(
                record,
                thread_id=int(thread_id),
                archived=False,
                base_commit=base_commit or record.base_commit,
            ),
            "thread",
        )

    def mark_archived(self, task_id: str) -> TaskAttempt:
        """The worker thread was archived (idempotent)."""
        record = self[task_id]
        if record.archived:
            return record
        return self._apply(task_id, replace(record, archived=True), "archived")

    def unarchived_threads(self) -> tuple[tuple[str, int], ...]:
        """Settled tasks whose worker thread still awaits its archive."""
        settled = (TaskStatus.FINISHED, TaskStatus.ACCEPTED, TaskStatus.BLOCKED)
        return tuple(
            (r.task_id, r.thread_id)
            for r in self.records
            if r.thread_id is not None and not r.archived and r.status in settled
        )

    def rework(self, task_id: str, reason: str) -> TaskAttempt:
        """A fresh attempt because the plan changed (T19) — not a repair: the repair budget
        and the previous result are both kept."""
        record = self[task_id]
        if record.status is TaskStatus.RUNNING:
            raise StaleAttemptError(f"task '{task_id}' is still running; finish it first")
        fresh = replace(
            self._next_attempt(record, record.lineage_repairs),
            previous_failure=None,
            rework_reason=_text("reason", reason),
            previous_commit=record.result_commit or record.previous_commit,
        )
        return self._apply(task_id, fresh, "reworked", reason=reason)

    def sync_tree(self, tree: PlanTree) -> SyncReport:
        """Take an edited plan on board immediately (T19).

        New plan versions are saved before anything else happens; a task accepted (or
        finished) at an older version gets a rework attempt; tasks the edit added appear
        pending. Nothing already built is discarded.
        """
        self._reload()
        self.tree = tree
        changed: dict[str, int] = {}
        for plan in tree.plans:
            known = self._document["plan_versions"].get(plan.plan_id)
            if known != plan.version:
                changed[plan.plan_id] = plan.version
                self._document["plan_versions"][plan.plan_id] = plan.version
        added: list[str] = []
        now = _now()
        for task in tree.tasks:
            if task.task_id not in self._document["tasks"]:
                self._document["tasks"][task.task_id] = TaskAttempt(
                    task_id=task.task_id,
                    plan_id=task.plan_id,
                    plan_version=task.plan_version,
                    attempt=1,
                    attempt_id=attempt_id(self.build_id, task.task_id, 1),
                    owned_files=task.owned_files,
                    owned_resources=task.owned_resources,
                    updated_at=now,
                ).to_json()
                added.append(task.task_id)
        if changed or added:
            self._document["events"].append(
                {"at": now, "task": None, "change": "plan-edit", "plans": changed, "added": added}
            )
            self._write()
        reworked: list[str] = []
        for task in tree.tasks:
            record = self[task.task_id]
            if task.plan_id not in changed:
                continue
            if record.status is TaskStatus.PENDING and record.plan_version != task.plan_version:
                # Not started yet: it simply runs at the new version.
                self._apply(
                    task.task_id, replace(record, plan_version=task.plan_version), "version"
                )
                continue
            if record.status in (TaskStatus.ACCEPTED, TaskStatus.FINISHED):
                self.rework(
                    task.task_id,
                    f"the plan '{task.plan_id}' changed to version {changed[task.plan_id]} "
                    f"after this was built at version {record.plan_version}",
                )
                reworked.append(task.task_id)
        report = SyncReport(changed, tuple(reworked), tuple(added))
        self.last_sync = report
        return report

    def note_plan_version(self, plan_id: str, version: int) -> None:
        """Requirements changed: remember the new version so old attempts cannot be accepted."""
        self._reload()
        if plan_id not in self._document["plan_versions"]:
            raise KeyError(f"no plan '{plan_id}' in build {self.build_id}")
        self._document["plan_versions"][plan_id] = int(version)
        self._document["events"].append(
            {
                "at": _now(),
                "task": None,
                "change": "plan-version",
                "plan": plan_id,
                "version": version,
            }
        )
        self._write()

    # -- internals -----------------------------------------------------------

    def _require(self, task_id: str, status: TaskStatus, action: str) -> TaskAttempt:
        record = self[task_id]
        if record.status is not status:
            raise StaleAttemptError(
                f"cannot {action} task '{task_id}': it is {record.status.value}, not {status.value}"
            )
        return record

    def _current(self, task_id: str, attempt: str) -> TaskAttempt:
        record = self[task_id]
        if attempt != record.attempt_id:
            stale = attempt.rsplit(":", 1)[-1]
            raise StaleAttemptError(
                f"attempt {stale} of task '{task_id}' is stale (current: attempt "
                f"{record.attempt}); a stale attempt cannot report or be accepted"
            )
        return record

    def _apply(
        self, task_id: str, record: TaskAttempt, change: str, **detail: object
    ) -> TaskAttempt:
        # `record` came from a fresh read (every accessor reloads); merge onto the current file.
        self._reload()
        stamped = replace(record, updated_at=_now())
        self._document["tasks"][task_id] = stamped.to_json()
        self._document["events"].append(
            {
                "at": stamped.updated_at,
                "task": task_id,
                "attempt": stamped.attempt,
                "change": change,
                **detail,
            }
        )
        self._write()
        return stamped

    def _write(self) -> None:
        self._document["updated_at"] = _now()
        _atomic(self.path, self._document)


def _new_document(tree: PlanTree, build_id: str) -> dict:
    now = _now()
    return {
        "version": STATE_VERSION,
        "build_id": build_id,
        "created_at": now,
        "updated_at": now,
        "plan_versions": {plan.plan_id: plan.version for plan in tree.plans},
        "tasks": {
            task.task_id: TaskAttempt(
                task_id=task.task_id,
                plan_id=task.plan_id,
                plan_version=task.plan_version,
                attempt=1,
                attempt_id=attempt_id(build_id, task.task_id, 1),
                owned_files=task.owned_files,
                owned_resources=task.owned_resources,
                updated_at=now,
            ).to_json()
            for task in tree.tasks
        },
        "events": [],
    }


def open_build_state(path: Path, tree: PlanTree, *, build_id: str) -> BuildState:
    """Open the ledger for *build_id*, creating it from *tree* on first use."""
    if not path.is_file():
        state = BuildState(path, tree, _new_document(tree, build_id))
        state._write()
        return state
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StaleAttemptError(f"the build state at {path} is not readable JSON") from exc
    if not isinstance(document, dict) or document.get("version") != STATE_VERSION:
        raise StaleAttemptError(
            f"the build state at {path} is not a version {STATE_VERSION} ledger"
        )
    if document.get("build_id") != build_id:
        raise StaleAttemptError(
            f"the build state at {path} belongs to build {document.get('build_id')}, not {build_id}"
        )
    for key in ("tasks", "events", "plan_versions"):
        if not isinstance(document.get(key), (dict, list)):
            raise StaleAttemptError(f"the build state at {path} has no '{key}' section")
    state = BuildState(path, tree, document)
    state.sync_tree(tree)
    return state
