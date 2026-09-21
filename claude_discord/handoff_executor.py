"""Start local workers for accepted trusted handoff tasks."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from claude_code_core.handoffs.state import HandoffState, HandoffTrigger, apply

from .database.handoff_repo import HandoffRepository
from .project_lookup_worker import (
    build_project_lookup_prompt,
    project_lookup_thread_name,
    resolve_project_lookup_root,
)

logger = logging.getLogger(__name__)


class _ChatSpawner(Protocol):
    def spawn_session(
        self,
        channel: Any,
        prompt: str,
        *,
        thread_name: str | None = None,
        auto_start: bool = True,
        working_dir: str | None = None,
    ) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class HandoffExecutionResult:
    """One handoff task that was handed to a local worker thread."""

    task_id: str
    thread_id: int
    attempt: int


async def execute_ready_handoff_tasks(
    *,
    repo: HandoffRepository,
    chat: _ChatSpawner,
    parent_channel: Any,
    local_agent_id: str,
    now: datetime | None = None,
    project_root: str | None = None,
    limit: int = 5,
) -> list[HandoffExecutionResult]:
    """Start accepted/queued handoff tasks for this agent.

    The durable state transition is saved before any worker starts. That keeps
    two bot processes from starting the same handoff when Discord redelivers the
    same packet or two inbox turns race.
    """
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    started: list[HandoffExecutionResult] = []
    lookup_root = resolve_project_lookup_root(configured=project_root)
    agent_id = local_agent_id.strip().lower()

    for job in await repo.list_nonterminal(agent_id):
        if len(started) >= limit:
            break
        if job.state not in {HandoffState.ACCEPTED, HandoffState.QUEUED}:
            continue

        task = await repo.get_task(job.task_id, agent_id)
        if task is None:
            logger.warning("handoff job %s/%s has no stored task packet", job.task_id, agent_id)
            continue

        transition = apply(job, HandoffTrigger.START, now=stamp)
        if not transition.schedules_execution:
            continue
        if not await repo.save_transition(transition):
            continue

        if not await repo.claim_attempt(
            job.task_id,
            agent_id,
            attempt=transition.job.attempt,
            execution_ref=None,
            now=stamp,
        ):
            continue

        prompt = build_project_lookup_prompt(
            query=task.goal,
            project_root=lookup_root,
            from_agent=task.sender,
            from_thread=task.origin.thread_id,
        )
        worker_thread = await chat.spawn_session(
            parent_channel,
            prompt,
            thread_name=project_lookup_thread_name(task.goal),
            auto_start=True,
            working_dir=lookup_root,
        )
        thread_id = int(worker_thread.id)
        await repo.set_job_thread(task.task_id, agent_id, thread_id)
        started.append(
            HandoffExecutionResult(
                task_id=task.task_id,
                thread_id=thread_id,
                attempt=transition.job.attempt,
            )
        )

    return started


__all__ = ["HandoffExecutionResult", "execute_ready_handoff_tasks"]
