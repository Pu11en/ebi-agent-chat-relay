"""The ready set: every task that may safely start right now, and why the rest cannot.

The task graph proves what *could* ever run together; this module decides what
may run *now*, given the durable status of each task and whatever else is
already writing the repository. It answers with two lists and nothing in
between: tasks that are ready, and tasks that are pending with a stated reason.

Two properties matter more than anything else here.

*No product worker cap.* If four tasks are dependency-satisfied and own disjoint
paths, four tasks are ready. A fixed `/gowork` worker count would be a guess
about the machine dressed up as a safety rule, and every value of it invents a
bottleneck the graph never justified. Real capacity — the relay semaphore,
provider quotas, the operating system — queues submitted turns *after* this
decision, which is why `queued` is an active status here rather than a reason to
call a task unready. There is deliberately no limit parameter to pass.

*Every exclusion is explainable.* A task left out carries a kind a caller can
branch on (`dependency`, `blocked-dependency`, `blocked`, `ownership`) and a
sentence a person reading the parent thread can act on. "Waiting" with no
subject is how a stalled build looks healthy for an hour.

Readiness is computed fresh from the graph and the statuses given; this module
holds no state, performs no I/O, and dispatches nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from extensions.feature_workflow.task_graph import TaskGraph, TaskNode

SCHEDULER_VERSION = 1


class SchedulerError(Exception):
    """The inputs cannot describe a safe decision, so no ready set is returned."""


class TaskStatus(StrEnum):
    """Durable task status, as the run state records it.

    `QUEUED` means the task was submitted and infrastructure is holding it —
    not the same thing as a `/gowork` dependency, and reported separately so a
    busy relay never reads as a stuck plan.
    """

    PENDING = "pending"
    QUEUED = "queued"
    SPAWNING = "spawning"
    AMBIGUOUS = "ambiguous"
    DISPATCHED = "dispatched"
    VERIFIED = "verified"
    INTEGRATED = "integrated"
    BLOCKED = "blocked"


# Statuses that occupy a task: work exists for it somewhere, so re-dispatching
# would duplicate it. `VERIFIED` stays here because a verified commit still owns
# its paths until the integration owner records it.
ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.QUEUED,
        TaskStatus.SPAWNING,
        TaskStatus.AMBIGUOUS,
        TaskStatus.DISPATCHED,
        TaskStatus.VERIFIED,
    }
)

# Why a pending task is pending. Callers branch on these; the reason sentence is
# for humans.
KIND_DEPENDENCY = "dependency"
KIND_BLOCKED_DEPENDENCY = "blocked-dependency"
KIND_BLOCKED = "blocked"
KIND_OWNERSHIP = "ownership"


@dataclass(frozen=True, slots=True)
class ReadyTask:
    """A task that may start now, with the facts a worker brief is built from."""

    id: str
    uid: str
    owned_paths: tuple[str, ...]
    check: str | None
    integration_only: bool
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingTask:
    """A task that may not start yet, and the named thing standing in its way."""

    id: str
    uid: str
    kind: str
    reason: str
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadySet:
    """One scheduling decision for one plan revision."""

    version: int
    revision: str
    ready: tuple[ReadyTask, ...]
    pending: tuple[PendingTask, ...]
    active: tuple[str, ...]
    queued: tuple[str, ...]
    integrated: tuple[str, ...]

    @property
    def ready_ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.ready)

    @property
    def worker_ids(self) -> tuple[str, ...]:
        """Ready tasks a fresh worker session may be given."""
        return tuple(task.id for task in self.ready if not task.integration_only)

    @property
    def integration_ids(self) -> tuple[str, ...]:
        """Ready tasks reserved for the integration owner's own session."""
        return tuple(task.id for task in self.ready if task.integration_only)

    @property
    def pending_ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.pending)

    @property
    def active_ids(self) -> tuple[str, ...]:
        return self.active

    @property
    def queued_ids(self) -> tuple[str, ...]:
        return self.queued

    @property
    def counts(self) -> dict[str, int]:
        """A compact tally for the parent thread. `queued` is a subset of `active`."""
        return {
            "ready": len(self.ready),
            "pending": len(self.pending),
            "active": len(self.active),
            "queued": len(self.queued),
            "integrated": len(self.integrated),
        }

    def _pending(self, task_id: str) -> PendingTask:
        for entry in self.pending:
            if entry.id == task_id:
                return entry
        known = set(self.ready_ids) | set(self.active) | set(self.integrated)
        if task_id in known:
            raise SchedulerError(f"Task {task_id} is not pending")
        raise SchedulerError(f"Unknown task: {task_id}")

    def reason_for(self, task_id: str) -> str:
        return self._pending(task_id).reason

    def kind_for(self, task_id: str) -> str:
        return self._pending(task_id).kind

    def blockers_for(self, task_id: str) -> tuple[str, ...]:
        return self._pending(task_id).blockers


def _overlaps(first: str, second: str) -> bool:
    """True when two declared paths can name the same file.

    Mirrors the graph's containment rule: a trailing slash makes a directory
    scope explicit, but `pkg` and `pkg/a.py` collide either way.
    """
    left, right = first.rstrip("/"), second.rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _hold_path(holder: str, raw: str) -> str:
    value = raw.strip()
    if not value:
        raise SchedulerError(f"Hold {holder} declares an empty path")
    if "\\" in value or value.startswith("/") or PurePosixPath(value).is_absolute():
        raise SchedulerError(f"Hold {holder} declares a path outside the repository: {value}")
    if any(part in {"..", "."} for part in PurePosixPath(value).parts):
        raise SchedulerError(f"Hold {holder} declares a path that escapes the repository: {value}")
    return value


def _resolve_statuses(
    graph: TaskGraph, statuses: Mapping[str, str | TaskStatus] | None
) -> dict[str, TaskStatus]:
    """Durable state wins over the plan's checkbox; a checked box means integrated."""
    resolved = {
        task.id: (TaskStatus.INTEGRATED if task.done else TaskStatus.PENDING) for task in graph
    }
    for task_id, value in (statuses or {}).items():
        if task_id not in resolved:
            raise SchedulerError(f"Status given for unknown task: {task_id}")
        try:
            resolved[task_id] = TaskStatus(str(value))
        except ValueError as exc:
            raise SchedulerError(f"Task {task_id} has an unknown status: {value}") from exc
    return resolved


def _dependency_pending(
    graph: TaskGraph, task: TaskNode, statuses: Mapping[str, TaskStatus]
) -> PendingTask | None:
    """Ancestors, not just direct parents: a dependency's dependency is ours too."""
    unmet = sorted(
        dep for dep in graph.dependencies_of(task.id) if statuses[dep] is not TaskStatus.INTEGRATED
    )
    if not unmet:
        return None
    blocked = tuple(dep for dep in unmet if statuses[dep] is TaskStatus.BLOCKED)
    if blocked:
        named = ", ".join(blocked)
        return PendingTask(
            id=task.id,
            uid=task.uid,
            kind=KIND_BLOCKED_DEPENDENCY,
            reason=f"Dependency {named} is blocked and needs a person before this task can run.",
            blockers=blocked,
        )
    named = ", ".join(f"{dep} ({statuses[dep].value})" for dep in unmet)
    return PendingTask(
        id=task.id,
        uid=task.uid,
        kind=KIND_DEPENDENCY,
        reason=f"Waits for {named} to be verified and integrated.",
        blockers=tuple(unmet),
    )


def _ownership_pending(task: TaskNode, holds: Iterable[tuple[str, str]]) -> PendingTask | None:
    """Someone else is writing one of this task's files right now."""
    for holder, held in holds:
        for mine in task.owned_paths:
            if _overlaps(mine, held):
                return PendingTask(
                    id=task.id,
                    uid=task.uid,
                    kind=KIND_OWNERSHIP,
                    reason=f"Owned path {mine} is being written by {holder} ({held}).",
                    blockers=(holder,),
                )
    return None


def compute_ready_set(
    graph: TaskGraph,
    statuses: Mapping[str, str | TaskStatus] | None = None,
    *,
    holds: Mapping[str, Iterable[str]] | None = None,
) -> ReadySet:
    """Return every task that may start now, plus a stated reason for each that may not.

    `statuses` is the durable record keyed by task ID; anything absent falls back
    to the plan's checkbox. `holds` names paths owned outside this graph — a task
    from an earlier plan revision still finishing, or an operator's claim — keyed
    by the holder shown to the user.

    Raises `SchedulerError` rather than guessing when a status names an unknown
    task, a status value is not one the run state can produce, or a hold points
    outside the repository.
    """
    resolved = _resolve_statuses(graph, statuses)

    active = tuple(task.id for task in graph if resolved[task.id] in ACTIVE_STATUSES)
    queued = tuple(task_id for task_id in active if resolved[task_id] is TaskStatus.QUEUED)
    integrated = tuple(task.id for task in graph if resolved[task.id] is TaskStatus.INTEGRATED)

    # An active task keeps its declared paths. Within one graph that can never
    # collide with a concurrent task — the parser already proved it — but work
    # from another revision or another run is exactly what this guards.
    occupied: list[tuple[str, str]] = [
        (task_id, path) for task_id in active for path in graph[task_id].owned_paths
    ]
    for holder, paths in (holds or {}).items():
        label = holder.strip() or "another session"
        occupied.extend((label, _hold_path(label, path)) for path in paths)

    ready: list[ReadyTask] = []
    pending: list[PendingTask] = []
    for task in graph:
        status = resolved[task.id]
        if status is not TaskStatus.PENDING:
            if status is TaskStatus.BLOCKED:
                pending.append(
                    PendingTask(
                        id=task.id,
                        uid=task.uid,
                        kind=KIND_BLOCKED,
                        reason="This task is blocked and needs a person before it can run again.",
                        blockers=(),
                    )
                )
            continue
        # Dependencies first: an unmet dependency is the more useful answer even
        # when a path is also busy, because the path usually frees itself.
        reason = _dependency_pending(graph, task, resolved) or _ownership_pending(task, occupied)
        if reason is not None:
            pending.append(reason)
            continue
        ready.append(
            ReadyTask(
                id=task.id,
                uid=task.uid,
                owned_paths=task.owned_paths,
                check=graph.check_for(task.id),
                integration_only=task.integration_only,
                depends_on=task.depends_on,
            )
        )

    return ReadySet(
        version=SCHEDULER_VERSION,
        revision=graph.revision,
        ready=tuple(ready),
        pending=tuple(pending),
        active=active,
        queued=queued,
        integrated=integrated,
    )
