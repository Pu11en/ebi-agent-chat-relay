"""The durable ledger of what each task has already done, so a restart is boring.

A parallel build makes side effects the local process cannot take back: a
Discord thread, a relay turn, a worktree. The dangerous moment is always the
same one — the request went out and the answer never came back. Re-asking
duplicates a worker; assuming failure loses one. This module removes the guess
by writing intent *before* the side effect and giving that intent a stable
correlation identity the external system can be searched for afterwards.

Three rules hold the whole thing together.

*Intent first.* `begin_spawn` records `spawning` with a correlation ID derived
from the run, the task's plan-pinned uid, and the attempt number. The same task
in the same attempt always produces the same ID, in this process or the next
one, which is what makes reconciliation possible rather than hopeful.

*Uncertainty is a state, not a retry.* Opening a ledger that contains an
in-flight spawn converts it to `ambiguous` and says so in the event log. The
scheduler counts `ambiguous` as active, so nothing re-dispatches on its own.
Only `reconcile_found` (the thread existed) or `reconcile_absent` (it provably
did not) moves it on, and absence starts a *new* attempt with a new identity so
the retry can never be mistaken for the original.

*Every transition is explicit.* Moves live in one table; anything outside it
raises `RunStateError` and writes nothing. Repeating a move with identical facts
is accepted — that is a retried call, not a new event — but repeating it with
different facts is refused, because two thread IDs for one task is exactly the
corruption this ledger exists to catch.

The record is replaced atomically (temp file, fsync, rename, fsync directory)
under an advisory lock, and every accepted move also appends to an event log
that keeps superseded evidence after a record is reset for a retry. Statuses are
`scheduler.TaskStatus` values and `statuses()` feeds `compute_ready_set`
directly. This module owns no Git, no Discord, and no policy about *which* task
should run — only the question of what has already happened.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from extensions.feature_workflow.scheduler import TaskStatus
from extensions.feature_workflow.task_graph import TaskGraph

RUN_STATE_VERSION = 1

# Guards, not policy: a ledger that grows without bound is a different outage.
MAX_EVIDENCE_ITEMS = 50
MAX_EVIDENCE_CHARS = 4000
MAX_TEXT_CHARS = 2000

# The only moves that exist. Anything else is a bug in the caller, and saying so
# loudly beats a ledger that repairs itself into a state nobody can explain.
ALLOWED: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.SPAWNING, TaskStatus.BLOCKED}),
    TaskStatus.SPAWNING: frozenset(
        {TaskStatus.QUEUED, TaskStatus.DISPATCHED, TaskStatus.AMBIGUOUS, TaskStatus.BLOCKED}
    ),
    TaskStatus.QUEUED: frozenset({TaskStatus.DISPATCHED, TaskStatus.AMBIGUOUS, TaskStatus.BLOCKED}),
    TaskStatus.AMBIGUOUS: frozenset(
        {TaskStatus.DISPATCHED, TaskStatus.PENDING, TaskStatus.BLOCKED}
    ),
    TaskStatus.DISPATCHED: frozenset({TaskStatus.VERIFIED, TaskStatus.BLOCKED}),
    TaskStatus.VERIFIED: frozenset({TaskStatus.INTEGRATED, TaskStatus.BLOCKED}),
    TaskStatus.INTEGRATED: frozenset(),
    TaskStatus.BLOCKED: frozenset({TaskStatus.PENDING}),
}


class RunStateError(Exception):
    """A move the ledger will not make, or a ledger that does not match this plan."""


def correlation_id(run_id: str, uid: str, attempt: int) -> str:
    """The identity a spawn carries into the outside world.

    Stable for one (run, task, attempt) so a restart can search for the thread
    it may have created; different for a new attempt so a deliberate retry is
    never confused with the spawn it replaces.
    """
    if attempt < 1:
        raise RunStateError(f"Attempt must be positive, got {attempt}")
    material = f"run-state/{RUN_STATE_VERSION}\n{run_id}\n{uid}\n{attempt}"
    return "gw-" + hashlib.sha256(material.encode()).hexdigest()[:24]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(label: str, value: str, *, limit: int = MAX_TEXT_CHARS) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise RunStateError(f"A {label} is required")
    if len(cleaned) > limit:
        raise RunStateError(f"The {label} is longer than {limit} characters")
    return cleaned


def _evidence(values: Iterable[str]) -> tuple[str, ...]:
    """Check evidence is the point of a verified record, so an empty one is refused."""
    items = tuple(
        _text("check evidence entry", str(value), limit=MAX_EVIDENCE_CHARS) for value in values
    )
    if not items:
        raise RunStateError("A verified result needs at least one check evidence entry")
    if len(items) > MAX_EVIDENCE_ITEMS:
        raise RunStateError(f"More than {MAX_EVIDENCE_ITEMS} check evidence entries")
    return items


def _atomic(path: Path, value: object) -> None:
    """Replace the file whole: a reader either sees the old ledger or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Everything durable about one task, and nothing a worker could re-derive."""

    id: str
    uid: str
    status: TaskStatus
    attempt: int
    correlation_id: str
    thread_id: str | None = None
    branch: str | None = None
    worktree: str | None = None
    foundation: str | None = None
    commit: str | None = None
    evidence: tuple[str, ...] = ()
    integration_commit: str | None = None
    archived: bool = False
    reason: str | None = None
    updated_at: str = ""

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "status": self.status.value,
            "attempt": self.attempt,
            "correlation_id": self.correlation_id,
            "thread_id": self.thread_id,
            "branch": self.branch,
            "worktree": self.worktree,
            "foundation": self.foundation,
            "commit": self.commit,
            "evidence": list(self.evidence),
            "integration_commit": self.integration_commit,
            "archived": self.archived,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, value: Mapping) -> TaskRecord:
        try:
            status = TaskStatus(str(value["status"]))
        except (KeyError, ValueError) as exc:
            raise RunStateError(f"Unreadable task status in the run state: {value}") from exc
        return cls(
            id=str(value["id"]),
            uid=str(value["uid"]),
            status=status,
            attempt=int(value["attempt"]),
            correlation_id=str(value["correlation_id"]),
            thread_id=value.get("thread_id"),
            branch=value.get("branch"),
            worktree=value.get("worktree"),
            foundation=value.get("foundation"),
            commit=value.get("commit"),
            evidence=tuple(value.get("evidence") or ()),
            integration_commit=value.get("integration_commit"),
            archived=bool(value.get("archived", False)),
            reason=value.get("reason"),
            updated_at=str(value.get("updated_at", "")),
        )


class RunState:
    """One plan revision's ledger, on disk, safe to reopen at any moment.

    Construct it with `open_run_state`. Every mutating method re-reads the file
    under an exclusive lock, applies one transition, and replaces the file — so
    a second process holding an older object cannot silently overwrite a move it
    never saw.
    """

    def __init__(self, path: Path, graph: TaskGraph, document: dict) -> None:
        self.path = path
        self.graph = graph
        self._document = document

    # ---- reading -------------------------------------------------------

    @property
    def run_id(self) -> str:
        return str(self._document["run_id"])

    @property
    def revision(self) -> str:
        return str(self._document["revision"])

    @property
    def approval_digest(self) -> str:
        return str(self._document["approval_digest"])

    @property
    def events(self) -> tuple[dict, ...]:
        return tuple(dict(event) for event in self._document["events"])

    def records(self) -> tuple[TaskRecord, ...]:
        return tuple(self[task.id] for task in self.graph)

    def __getitem__(self, task_id: str) -> TaskRecord:
        try:
            return TaskRecord.from_json(self._document["tasks"][task_id])
        except KeyError as exc:
            raise RunStateError(f"Unknown task: {task_id}") from exc

    def __contains__(self, task_id: str) -> bool:
        return task_id in self._document["tasks"]

    def statuses(self) -> dict[str, TaskStatus]:
        """The mapping `compute_ready_set` takes, with no translation in between."""
        return {record.id: record.status for record in self.records()}

    def attention(self) -> tuple[TaskRecord, ...]:
        """Tasks a person has to look at: blocked, or uncertain about its own spawn."""
        return tuple(
            record
            for record in self.records()
            if record.status in {TaskStatus.BLOCKED, TaskStatus.AMBIGUOUS}
        )

    def counts(self) -> dict[str, int]:
        tally = {status.value: 0 for status in TaskStatus}
        for record in self.records():
            tally[record.status.value] += 1
        return tally

    # ---- transitions ---------------------------------------------------

    def begin_spawn(
        self,
        task_id: str,
        *,
        foundation: str,
        branch: str | None = None,
        worktree: str | None = None,
    ) -> TaskRecord:
        """Record the intent to start a worker, before anything external is touched."""
        commit = _text("foundation commit", foundation)
        return self._apply(
            task_id,
            TaskStatus.SPAWNING,
            lambda record: replace(
                record, foundation=commit, branch=branch, worktree=worktree, reason=None
            ),
            detail={"foundation": commit},
        )

    def mark_queued(self, task_id: str, reason: str) -> TaskRecord:
        """Submitted, and infrastructure is holding it — not a `/gowork` dependency."""
        stated = _text("reason", reason)
        return self._apply(
            task_id,
            TaskStatus.QUEUED,
            lambda record: replace(record, reason=stated),
            detail={"reason": stated},
        )

    def record_dispatch(
        self,
        task_id: str,
        thread_id: str,
        *,
        branch: str | None = None,
        worktree: str | None = None,
    ) -> TaskRecord:
        """The spawn came back with a thread: the uncertainty is over."""
        thread = _text("thread identity", str(thread_id))
        return self._apply(
            task_id,
            TaskStatus.DISPATCHED,
            lambda record: replace(
                record,
                thread_id=thread,
                branch=branch or record.branch,
                worktree=worktree or record.worktree,
                reason=None,
            ),
            detail={"thread_id": thread},
        )

    def mark_ambiguous(self, task_id: str, reason: str) -> TaskRecord:
        """The spawn may or may not have happened; reconcile it, never retry it."""
        stated = _text("reason", reason)
        return self._apply(
            task_id,
            TaskStatus.AMBIGUOUS,
            lambda record: replace(record, reason=stated),
            detail={"reason": stated},
        )

    def reconcile_found(
        self,
        task_id: str,
        thread_id: str,
        *,
        branch: str | None = None,
        worktree: str | None = None,
    ) -> TaskRecord:
        """The correlation ID led to a real thread: adopt it on the same attempt."""
        self._require(task_id, TaskStatus.AMBIGUOUS, "reconcile a found thread for")
        return self.record_dispatch(task_id, thread_id, branch=branch, worktree=worktree)

    def reconcile_absent(self, task_id: str, reason: str) -> TaskRecord:
        """Nothing was created, and here is how we know: return it on a new attempt."""
        stated = _text("reason", reason)
        self._require(task_id, TaskStatus.AMBIGUOUS, "reconcile an absent thread for")
        return self._apply(
            task_id,
            TaskStatus.PENDING,
            lambda record: self._reset(record),
            detail={"reason": stated, "superseded_correlation_id": self[task_id].correlation_id},
        )

    def record_verified(
        self,
        task_id: str,
        *,
        commit: str,
        evidence: Iterable[str],
        branch: str | None = None,
        worktree: str | None = None,
    ) -> TaskRecord:
        """A clean commit plus the checks that were actually run, kept verbatim."""
        sha = _text("commit", commit)
        checks = _evidence(evidence)
        return self._apply(
            task_id,
            TaskStatus.VERIFIED,
            lambda record: replace(
                record,
                commit=sha,
                evidence=checks,
                branch=branch or record.branch,
                worktree=worktree or record.worktree,
                reason=None,
            ),
            detail={"commit": sha, "evidence": list(checks)},
        )

    def record_integrated(self, task_id: str, integration_commit: str) -> TaskRecord:
        """The integration owner has this commit in the foundation; dependents may go."""
        sha = _text("integration commit", integration_commit)
        return self._apply(
            task_id,
            TaskStatus.INTEGRATED,
            lambda record: replace(record, integration_commit=sha, reason=None),
            detail={"integration_commit": sha},
        )

    def mark_archived(self, task_id: str) -> TaskRecord:
        """The final side effect of a successful integration, and idempotent by design."""
        record = self[task_id]
        if record.status is not TaskStatus.INTEGRATED:
            raise RunStateError(
                f"Task {task_id} is {record.status.value} and only an integrated task is archived"
            )
        if record.archived:
            return record
        return self._write(replace(record, archived=True, updated_at=_now()), "archived", {})

    def mark_blocked(self, task_id: str, reason: str) -> TaskRecord:
        """Needs a person. The evidence stays; only the path forward stops."""
        stated = _text("reason", reason)
        return self._apply(
            task_id,
            TaskStatus.BLOCKED,
            lambda record: replace(record, reason=stated),
            detail={"reason": stated},
        )

    def unblock(self, task_id: str) -> TaskRecord:
        """A person cleared it: start a fresh attempt, leaving the old one in the log."""
        return self._apply(
            task_id,
            TaskStatus.PENDING,
            lambda record: self._reset(record),
            detail={},
        )

    # ---- internals -----------------------------------------------------

    def _recover(self) -> None:
        """Turn every in-flight spawn into a reconciliation task, durably."""
        for record in self.records():
            if record.status is TaskStatus.SPAWNING:
                self.mark_ambiguous(
                    record.id,
                    "The process restarted while this spawn was in flight; "
                    "reconcile the correlation ID before dispatching again.",
                )

    def _reset(self, record: TaskRecord) -> TaskRecord:
        """A new attempt earns a new correlation identity and no inherited results.

        The discarded thread, commit, and evidence remain in the event log, which
        is what "preserve evidence" means once a task is deliberately rebuilt.
        """
        attempt = record.attempt + 1
        return replace(
            record,
            attempt=attempt,
            correlation_id=correlation_id(self.run_id, record.uid, attempt),
            thread_id=None,
            worktree=None,
            commit=None,
            evidence=(),
            reason=None,
        )

    def _require(self, task_id: str, status: TaskStatus, action: str) -> TaskRecord:
        record = self[task_id]
        if record.status is not status:
            raise RunStateError(
                f"Task {task_id} is {record.status.value}; "
                f"only a {status.value} task can {action} it"
            )
        return record

    def _apply(self, task_id: str, target: TaskStatus, change, *, detail: dict) -> TaskRecord:
        with self._locked():
            current = self[task_id]
            if current.status is target:
                # A retried call, not a new event: identical facts are fine, and
                # anything else means two sources believe different things.
                updated = change(current)
                if updated.to_json() == current.to_json():
                    return current
                mismatch = {
                    key: value
                    for key, value in updated.to_json().items()
                    if current.to_json()[key] != value and key != "updated_at"
                }
                raise RunStateError(
                    f"Task {task_id} is already {target.value} with different facts: {mismatch}"
                )
            if target not in ALLOWED[current.status]:
                raise RunStateError(
                    f"Task {task_id} cannot move from {current.status.value} to {target.value}"
                )
            updated = replace(change(current), status=target, updated_at=_now())
            return self._write(updated, target.value, detail, previous=current)

    def _write(
        self,
        record: TaskRecord,
        label: str,
        detail: dict,
        *,
        previous: TaskRecord | None = None,
    ) -> TaskRecord:
        document = self._document
        document["tasks"][record.id] = record.to_json()
        document["events"].append(
            {
                "at": record.updated_at or _now(),
                "task": record.id,
                "uid": record.uid,
                "attempt": record.attempt,
                "correlation_id": record.correlation_id,
                "from": (previous.status.value if previous else record.status.value),
                "to": label,
                "detail": detail,
            }
        )
        document["updated_at"] = _now()
        _atomic(self.path, document)
        return record

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize read-modify-write, and re-read so a stale object cannot win."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with (self.path.parent / f"{self.path.name}.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                self._document = _read(self.path, self.graph, self.approval_digest, self.run_id)
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _new_document(graph: TaskGraph, approval_digest: str, run_id: str) -> dict:
    now = _now()
    return {
        "version": RUN_STATE_VERSION,
        "run_id": run_id,
        "revision": graph.revision,
        "approval_digest": approval_digest,
        "plan_path": graph.plan_path,
        "created_at": now,
        "updated_at": now,
        "tasks": {
            task.id: TaskRecord(
                id=task.id,
                uid=task.uid,
                # A plan's checked box is history, not work this run must do.
                status=TaskStatus.INTEGRATED if task.done else TaskStatus.PENDING,
                attempt=1,
                correlation_id=correlation_id(run_id, task.uid, 1),
                updated_at=now,
            ).to_json()
            for task in graph
        },
        "events": [],
    }


def _read(path: Path, graph: TaskGraph, approval_digest: str, run_id: str | None) -> dict:
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RunStateError(f"The run state at {path} is not readable JSON") from exc
    if not isinstance(document, dict):
        raise RunStateError(f"The run state at {path} is not a run state document")
    if document.get("version") != RUN_STATE_VERSION:
        raise RunStateError(
            f"The run state at {path} is version {document.get('version')}, not {RUN_STATE_VERSION}"
        )
    if document.get("revision") != graph.revision:
        # The plan changed under a running build. Merging the two would silently
        # reassign work; naming it lets a person decide.
        raise RunStateError(
            f"The run state at {path} belongs to plan revision {document.get('revision')}, "
            f"not {graph.revision}"
        )
    if document.get("approval_digest") != approval_digest:
        raise RunStateError(f"The run state at {path} carries a different approval digest")
    if run_id is not None and document.get("run_id") != run_id:
        raise RunStateError(f"The run state at {path} belongs to run {document.get('run_id')}")
    missing = {task.id for task in graph} - set(document.get("tasks", {}))
    if missing:
        raise RunStateError(f"The run state at {path} has no record for {sorted(missing)}")
    document.setdefault("events", [])
    return document


def open_run_state(
    path: Path | str,
    *,
    graph: TaskGraph,
    approval_digest: str,
    run_id: str | None = None,
) -> RunState:
    """Create the ledger, or recover the one already on disk for this exact plan.

    Recovery is where the duplicate-worker bug would live, so it does exactly one
    thing beyond reading: any task still marked `spawning` becomes `ambiguous`,
    because a spawn that was in flight when the process died is precisely the
    case where nobody yet knows whether a worker exists. The scheduler treats
    `ambiguous` as active, so it stays out of the ready set until reconciliation
    resolves it either way.

    A ledger whose revision or approval digest does not match the given plan is
    refused rather than reused.
    """
    target = Path(path)
    digest = _text("approval digest", approval_digest)
    if target.exists():
        document = _read(target, graph, digest, run_id)
        state = RunState(target, graph, document)
        state._recover()
        return state
    resolved = run_id or graph.revision
    document = _new_document(graph, digest, resolved)
    _atomic(target, document)
    return RunState(target, graph, document)
