"""Which tasks a Go Work build may start right now (T06).

Deterministic and pure: the same plan and ledger always give the same answer,
in plan order. A task is ready when it is still pending, every prerequisite
was *accepted* at the plan version that is current now, and nothing it owns
overlaps a task that is running or picked earlier in this same answer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from claude_code_core.gowork_state import BuildState, TaskAttempt, TaskStatus


@dataclass(frozen=True, slots=True)
class ReadyTask:
    task_id: str
    plan_id: str
    project_path: Path


def _releases(state: BuildState, dependency: TaskAttempt) -> bool:
    return dependency.accepted and dependency.plan_version == state.plan_version(dependency.plan_id)


def ready_tasks(
    state: BuildState, *, running: Iterable[str] = (), limit: int | None = None
) -> tuple[ReadyTask, ...]:
    """Tasks that may start now, in plan order, never more than *limit*."""
    known = {task.task_id for task in state.tree.tasks}
    busy = [task_id for task_id in running if task_id in known]  # an edit may drop one
    chosen: list[ReadyTask] = []
    for task in state.tree.tasks:
        if limit is not None and len(chosen) >= limit:
            break
        record = state[task.task_id]
        if record.status is not TaskStatus.PENDING or task.task_id in busy:
            continue
        if not all(_releases(state, state[dep]) for dep in task.dependencies):
            continue
        if any(
            not state.tree.can_run_together(task.task_id, other)
            for other in (*busy, *(c.task_id for c in chosen))
        ):
            continue
        plan = state.tree.get(task.plan_id)
        chosen.append(ReadyTask(task.task_id, plan.plan_id, plan.project_path))
    return tuple(chosen)
