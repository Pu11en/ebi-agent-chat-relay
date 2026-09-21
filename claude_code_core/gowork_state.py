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
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise StaleAttemptError(f"unreadable task attempt in the build state: {value}") from exc


def attempt_id(build_id: str, task_id: str, attempt: int) -> str:
    return f"{build_id}:{task_id}:{attempt}"


class BuildState:
    """One build's task ledger on disk, safe to reopen at any moment."""

    def __init__(self, path: Path, tree: PlanTree, document: dict) -> None:
        self.path = path
        self.tree = tree
        self._document = document

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
        self._reload()
        return tuple(TaskAttempt.from_json(v) for v in self._document["tasks"].values())

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
        number = record.attempt + 1
        fresh = TaskAttempt(
            task_id=record.task_id,
            plan_id=record.plan_id,
            plan_version=self.plan_version(record.plan_id),
            attempt=number,
            attempt_id=attempt_id(self.build_id, task_id, number),
            owned_files=record.owned_files,
            owned_resources=record.owned_resources,
        )
        return self._apply(task_id, fresh, "retried")

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
    return BuildState(path, tree, document)
