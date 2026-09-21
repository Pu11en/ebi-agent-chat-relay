"""The execution coordinator for accepted trusted handoffs.

A handoff is a job before it is a conversation, and this module is where the
job becomes work — under three rules that are easy to state and easy to break:

* **The ledger is written before anything is scheduled.** The attempt row is
  claimed first (a unique index no other process can also win), then the
  ``running`` transition is saved, and only then does an adapter start. A
  spawn that fails closes the attempt and requeues a bounded retry, so a job
  can never sit at ``running`` with nothing running.
* **Authority and location are decided before execution, not by it.** The
  effective scope is ``inherited ∩ policy`` (:mod:`handoff_authority`), the
  project is resolved inside the approved roots (:mod:`handoff_projects`),
  and either failing blocks the job visibly instead of starting a worker
  that would have to be trusted to notice.
* **Waiting is not failing.** No free execution slot leaves the job queued
  with a capacity note; the next pass tries again.

Three execution adapters exist. A *fresh backend turn* in the job thread is
the default — a bounded lookup does not need a durable chat session, and the
thread already shows what is happening. An *explicitly selected existing
session* resumes that thread instead, when a selector names one. A
*deterministic operation* needs no model at all and runs inline. All three
report through the same result sink, so the terminal result, the outbox, and
the origin delivery are identical whichever ran.

:func:`execute_ready_handoff_tasks` keeps the narrow project-lookup entry
point working on top of the coordinator.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from claude_code_core.handoffs.protocol import AuthorityScope, HandoffTask
from claude_code_core.handoffs.state import (
    CAPACITY_NOTE,
    HandoffJob,
    HandoffState,
    HandoffTrigger,
    Transition,
    apply,
    safe_retry,
)

from .database.handoff_repo import HandoffRepository
from .handoff_authority import RecipientPolicy, check_authority, describe_scope
from .handoff_projects import (
    DEFAULT_LOOKUP_LOCATOR,
    ProjectResolutionError,
    ProjectResolver,
    ResolvedProject,
    build_project_resolver,
)
from .handoff_return import record_and_deliver_handoff_result
from .project_lookup_worker import (
    build_project_lookup_prompt,
    project_lookup_harness,
    project_lookup_thread_name,
)

logger = logging.getLogger(__name__)

ResultSink = Callable[[str | None, str | None], Awaitable[None]]
TransitionHook = Callable[[HandoffTask, Transition], Awaitable[None]]
SessionSelector = Callable[[HandoffTask], int | None]

MAX_BLOCKER_NOTE_CHARS = 480


class ExecutionMode(Enum):
    """Which adapter ran the job."""

    FRESH_TURN = "fresh_turn"
    EXISTING_SESSION = "existing_session"
    DETERMINISTIC = "deterministic"


class DeterministicOperation(Protocol):
    """A non-model operation that can answer a handoff outright."""

    def matches(self, task: HandoffTask) -> bool: ...

    async def run(self, task: HandoffTask, project: ResolvedProject) -> str: ...


class _Chat(Protocol):
    """The slice of ``ClaudeChatCog`` the coordinator relies on (duck-typed)."""

    bot: Any


@dataclass(frozen=True)
class HandoffExecutionResult:
    """One handoff task that was handed to an adapter."""

    task_id: str
    thread_id: int | None
    attempt: int
    mode: ExecutionMode = ExecutionMode.FRESH_TURN


def build_handoff_prompt(task: HandoffTask, *, project_path: str, effective: AuthorityScope) -> str:
    """The instruction for a general handoff turn: scope first, then the goal.

    Only packet fields reach the prompt — never a transcript. The rules are
    stated bluntly because the effective scope is enforced here, not by the
    model's judgement.
    """
    if effective.is_read_only:
        scope_rules = (
            "- This task is read-only. Do not edit, create, delete, move, format, commit,\n"
            "  push, install, or restart anything."
        )
    else:
        where = ", ".join(effective.edit_paths) if effective.edit_paths else "the project folder"
        scope_rules = (
            f"- You may edit files within {where}. Use a safe worktree or branch for changes.\n"
            "- Do not delete data, do not deploy, do not push, do not publish, do not pay for\n"
            "  anything, do not message anyone outside this thread, and do not change\n"
            "  permissions. If the goal needs any of those, stop and say so."
        )
    findings = "\n".join(f"- {item}" for item in task.findings) or "- none provided"
    return (
        f"You are {task.recipient}, handling a handoff from {task.sender} "
        f"(task {task.task_id}).\n\n"
        f"Project: `{project_path}` ({task.project.owner}/{task.project.folder})\n"
        f"Effective authority: {describe_scope(effective)}\n\n"
        "Rules:\n"
        f"{scope_rules}\n"
        "- Stay inside the project folder.\n"
        "- Do not ask follow-up or multiple-choice questions; do the best reasonable job\n"
        "  from the packet and finish with the answer.\n"
        "- If you cannot finish safely, say exactly what is missing.\n\n"
        "Relevant findings from the origin:\n"
        f"{findings}\n\n"
        "Goal:\n"
        f"{task.goal}\n\n"
        "Expected result:\n"
        f"{task.expected_result}\n"
    )


def _clip_note(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:MAX_BLOCKER_NOTE_CHARS]


class HandoffExecutor:
    """Turns accepted/queued jobs into exactly one execution each."""

    def __init__(
        self,
        *,
        repo: HandoffRepository,
        local_agent_id: str,
        resolver: ProjectResolver,
        policy: RecipientPolicy,
        threads: Mapping[int, Any] | None = None,
        thread_lookup: Callable[[int], Awaitable[Any | None]] | None = None,
        session_selector: SessionSelector | None = None,
        deterministic: Sequence[DeterministicOperation] = (),
        on_transition: TransitionHook | None = None,
    ) -> None:
        self._repo = repo
        self._agent = local_agent_id.strip().lower()
        self._resolver = resolver
        self._policy = policy
        self._threads: dict[int, Any] = dict(threads or {})
        self._thread_lookup = thread_lookup
        self._session_selector = session_selector
        self._deterministic = tuple(deterministic)
        self._on_transition = on_transition

    @property
    def local_agent_id(self) -> str:
        return self._agent

    # -- restart -------------------------------------------------------------

    async def reconcile_after_restart(self, *, now: datetime | None = None) -> list[HandoffJob]:
        """Requeue jobs that say ``running`` but have no process behind them."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        requeued = await self._repo.requeue_running(self._agent, now=stamp)
        for job in requeued:
            task = await self._repo.get_task(job.task_id, self._agent)
            if task is not None:
                await self._notify(
                    task,
                    Transition(
                        job=job,
                        trigger=HandoffTrigger.RESTART_RECONCILE,
                        state=job.state,
                        at=stamp,
                        previous=HandoffState.RUNNING,
                        note=job.note,
                    ),
                )
        return requeued

    # -- the main pass -------------------------------------------------------

    async def run_ready(
        self,
        *,
        chat: Any,
        parent_channel: Any,
        now: datetime | None = None,
        limit: int = 5,
    ) -> list[HandoffExecutionResult]:
        """Start every accepted/queued job this agent owns, up to ``limit``."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC)
        started: list[HandoffExecutionResult] = []
        for job in await self._repo.list_nonterminal(self._agent):
            if len(started) >= limit:
                break
            if job.state not in (HandoffState.ACCEPTED, HandoffState.QUEUED):
                continue
            result = await self._execute(job, chat=chat, parent_channel=parent_channel, now=stamp)
            if result is not None:
                started.append(result)
        return started

    async def _execute(
        self, job: HandoffJob, *, chat: Any, parent_channel: Any, now: datetime
    ) -> HandoffExecutionResult | None:
        task = await self._repo.get_task(job.task_id, self._agent)
        if task is None:
            logger.warning("handoff job %s/%s has no stored task packet", job.task_id, self._agent)
            return None

        if task.is_expired(now):
            await self._move(task, job, HandoffTrigger.EXPIRE, now, note="task packet expired")
            return None

        decision = check_authority(task, self._policy)
        if not decision.allowed:
            await self._move(
                task, job, HandoffTrigger.BLOCK, now, note=_clip_note("; ".join(decision.blockers))
            )
            return None

        try:
            project = self._resolver.resolve(task.project)
        except ProjectResolutionError as exc:
            await self._move(task, job, HandoffTrigger.BLOCK, now, note=_clip_note(str(exc)))
            return None

        capacity = getattr(chat, "handoff_capacity_available", None)
        if callable(capacity) and not capacity():
            if job.state is not HandoffState.QUEUED or job.note != CAPACITY_NOTE:
                await self._move(task, job, HandoffTrigger.CAPACITY_WAIT, now)
            return None

        # Ledger first: claim the attempt, then record running, then start.
        claimed = await self._repo.claim_attempt(
            job.task_id, self._agent, attempt=job.attempt, now=now
        ) or await self._repo.reclaim_interrupted_attempt(
            job.task_id, self._agent, attempt=job.attempt, now=now
        )
        if not claimed:
            logger.info("handoff %s attempt %d is already claimed", job.task_id, job.attempt)
            return None
        transition = apply(job, HandoffTrigger.START, now=now)
        if not transition.schedules_execution or not await self._repo.save_transition(transition):
            await self._repo.finish_attempt(
                job.task_id, self._agent, attempt=job.attempt, outcome="superseded", now=now
            )
            return None
        running = transition.job
        await self._notify(task, transition)

        sink = self._result_sink(task, chat)
        try:
            mode, thread_id = await self._start(
                task,
                running,
                project=project,
                effective=decision.effective,
                chat=chat,
                parent_channel=parent_channel,
                sink=sink,
            )
        except Exception as exc:
            logger.warning("handoff %s could not start", task.task_id, exc_info=True)
            await self._fail_attempt(task, running, now, error=str(exc))
            return None
        return HandoffExecutionResult(
            task_id=task.task_id, thread_id=thread_id, attempt=running.attempt, mode=mode
        )

    # -- adapters ------------------------------------------------------------

    async def _start(
        self,
        task: HandoffTask,
        job: HandoffJob,
        *,
        project: ResolvedProject,
        effective: AuthorityScope,
        chat: Any,
        parent_channel: Any,
        sink: ResultSink,
    ) -> tuple[ExecutionMode, int | None]:
        for operation in self._deterministic:
            if operation.matches(task):
                text = await operation.run(task, project)
                await sink(text, None)
                return ExecutionMode.DETERMINISTIC, await self._repo.get_job_thread(
                    task.task_id, self._agent
                )

        path = str(project.path)
        lookup_shaped = task.project == DEFAULT_LOOKUP_LOCATOR and effective.is_read_only
        if lookup_shaped:
            prompt = build_project_lookup_prompt(
                query=task.goal,
                project_root=path,
                from_agent=task.sender,
                from_thread=task.origin.thread_id,
            )
        else:
            prompt = build_handoff_prompt(task, project_path=path, effective=effective)
        backend, model = project_lookup_harness() if effective.is_read_only else (None, None)

        selected = self._session_selector(task) if self._session_selector else None
        if selected is not None:
            thread = await self._thread(selected)
            if thread is None:
                raise RuntimeError(f"selected session thread {selected} is not reachable")
            await chat.run_handoff_turn(
                thread,
                prompt,
                working_dir=path,
                result_sink=sink,
                resume=True,
                backend=backend,
                model=model,
            )
            return ExecutionMode.EXISTING_SESSION, int(thread.id)

        job_thread_id = await self._repo.get_job_thread(task.task_id, self._agent)
        job_thread = await self._thread(job_thread_id) if job_thread_id is not None else None
        if job_thread is not None and hasattr(chat, "run_handoff_turn"):
            await chat.run_handoff_turn(
                job_thread,
                prompt,
                working_dir=path,
                result_sink=sink,
                resume=False,
                backend=backend,
                model=model,
            )
            return ExecutionMode.FRESH_TURN, int(job_thread.id)

        if parent_channel is None:
            raise RuntimeError("no job thread and no channel to open a worker thread in")
        worker = await chat.spawn_session(
            parent_channel,
            prompt,
            thread_name=project_lookup_thread_name(task.goal),
            auto_start=True,
            working_dir=path,
            result_sink=sink,
            backend=backend,
            model=model,
        )
        thread_id = int(worker.id)
        await self._repo.set_job_thread(task.task_id, self._agent, thread_id)
        return ExecutionMode.FRESH_TURN, thread_id

    async def _thread(self, thread_id: int) -> Any | None:
        thread = self._threads.get(thread_id)
        if thread is None and self._thread_lookup is not None:
            thread = await self._thread_lookup(thread_id)
        return thread

    # -- ledger helpers ------------------------------------------------------

    def _result_sink(self, task: HandoffTask, chat: Any) -> ResultSink:
        bot = getattr(chat, "bot", None)
        repo = self._repo
        agent = self._agent
        hook = self._on_transition

        async def sink(text: str | None, error: str | None) -> None:
            if bot is None:
                logger.warning("handoff %s finished but no bot is available", task.task_id)
                return
            await record_and_deliver_handoff_result(
                repo=repo,
                bot=bot,
                task_id=task.task_id,
                local_agent_id=agent,
                text=text,
                error=error,
                on_transition=hook,
            )

        return sink

    async def _move(
        self,
        task: HandoffTask,
        job: HandoffJob,
        trigger: HandoffTrigger,
        now: datetime,
        *,
        note: str | None = None,
        retryable: bool = False,
    ) -> Transition | None:
        transition = apply(job, trigger, now=now, note=note, retryable=retryable)
        if not await self._repo.save_transition(transition):
            return None
        await self._notify(task, transition)
        return transition

    async def _fail_attempt(
        self, task: HandoffTask, job: HandoffJob, now: datetime, *, error: str
    ) -> None:
        await self._repo.finish_attempt(
            job.task_id, self._agent, attempt=job.attempt, outcome="failed", detail=error, now=now
        )
        failed = await self._move(
            task,
            job,
            HandoffTrigger.FAIL,
            now,
            note=_clip_note(f"could not start: {error}"),
            retryable=True,
        )
        if failed is None or not failed.job.can_retry:
            return
        retry = safe_retry(failed.job, now=now, note=_clip_note(f"retrying after: {error}"))
        if await self._repo.save_transition(retry):
            await self._notify(task, retry)

    async def _notify(self, task: HandoffTask, transition: Transition) -> None:
        if self._on_transition is None:
            return
        try:
            await self._on_transition(task, transition)
        except Exception:
            logger.warning("handoff status hook failed for %s", task.task_id, exc_info=True)


def build_handoff_executor(
    *,
    repo: HandoffRepository,
    local_agent_id: str,
    fallback_lookup_root: str | None = None,
    thread_lookup: Callable[[int], Awaitable[Any | None]] | None = None,
    on_transition: TransitionHook | None = None,
    session_selector: SessionSelector | None = None,
    deterministic: Sequence[DeterministicOperation] = (),
) -> HandoffExecutor:
    """The executor an instance runs with, configured from its environment."""
    return HandoffExecutor(
        repo=repo,
        local_agent_id=local_agent_id,
        resolver=build_project_resolver(fallback_lookup_root=fallback_lookup_root),
        policy=RecipientPolicy.from_env(),
        thread_lookup=thread_lookup,
        on_transition=on_transition,
        session_selector=session_selector,
        deterministic=deterministic,
    )


async def execute_ready_handoff_tasks(
    *,
    repo: HandoffRepository,
    chat: Any,
    parent_channel: Any,
    local_agent_id: str,
    now: datetime | None = None,
    project_root: str | None = None,
    limit: int = 5,
) -> list[HandoffExecutionResult]:
    """Start accepted/queued handoff tasks for this agent (narrow-slice entry point)."""
    executor = build_handoff_executor(
        repo=repo,
        local_agent_id=local_agent_id,
        fallback_lookup_root=project_root,
        thread_lookup=_bot_thread_lookup(getattr(chat, "bot", None)),
    )
    return await executor.run_ready(chat=chat, parent_channel=parent_channel, now=now, limit=limit)


def _bot_thread_lookup(bot: Any) -> Callable[[int], Awaitable[Any | None]] | None:
    if bot is None:
        return None

    async def lookup(thread_id: int) -> Any | None:
        thread = bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await bot.fetch_channel(thread_id)
            except Exception:
                return None
        return thread

    return lookup


__all__ = [
    "DeterministicOperation",
    "ExecutionMode",
    "HandoffExecutionResult",
    "HandoffExecutor",
    "build_handoff_executor",
    "build_handoff_prompt",
    "execute_ready_handoff_tasks",
]
