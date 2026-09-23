"""The durable job state machine for trusted cross-computer agent handoffs.

A handoff is a job before it is a conversation. This module owns the part that
decides whether work may start, and it is deliberately pure: no Discord, no
database, no clock of its own. A caller passes the stored job, says what just
happened, and gets back the next job plus one explicit answer to the only
dangerous question in the whole protocol — *may this schedule execution?*

Three rules from the `trusted-agent-handoffs` design are load-bearing here:

* **Nothing a remote agent says can start or restart local work.** Only a local
  :data:`HandoffTrigger.START` sets ``schedules_execution``; everything that
  arrives as a protocol event is mirrored onto the ledger with that flag off.
  A terminal result is therefore recorded, never re-run, and a redelivered task
  event returns the stored job unchanged instead of scheduling a second one.
* **Waiting for capacity is not a failure.** A recipient with no free slot stays
  ``queued`` and keeps its attempt number; only real execution failure, expiry,
  or an exhausted retry budget reaches ``failed``.
* **Terminal states are immutable except for an explicit safe retry**, which
  needs a failure the recipient marked retryable and a remaining attempt, and
  which produces a *queued* new attempt under the same logical task — the retry
  itself never starts anything.

Every rejection is a deterministic exception raised before any value changes;
the job objects are frozen, so a refused transition cannot leave a half-applied
ledger behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import NoReturn

from claude_code_core.handoffs.protocol import (
    HandoffEvent,
    HandoffEventKind,
    HandoffProtocolError,
    HandoffTask,
    HandoffValidationError,
    validate_agent_id,
    validate_uuid,
)

DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS_LIMIT = 10
MAX_NOTE_CHARS = 500

CAPACITY_NOTE = "waiting for local execution capacity"
RESTART_NOTE = "requeued after restart: no verifiable active execution"


class HandoffStateError(HandoffProtocolError):
    """Base class for every refusal of a handoff state change."""


class IllegalTransitionError(HandoffStateError):
    """The requested transition is not part of the state machine."""


class RetryNotPermittedError(HandoffStateError):
    """A retry was asked for where the safe-retry rules do not allow one."""


class HandoffExpiredError(HandoffStateError):
    """The task packet expired before the recipient could accept it."""


class HandoffState(Enum):
    """The six job states a recipient exposes for a handoff."""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Completed and failed jobs are immutable apart from a safe retry."""
        return self in (HandoffState.COMPLETED, HandoffState.FAILED)

    @property
    def is_active(self) -> bool:
        """Only a running job may be holding an execution slot."""
        return self is HandoffState.RUNNING

    @classmethod
    def parse(cls, value: object) -> HandoffState:
        """Parse a state name received from another agent."""
        if isinstance(value, HandoffState):
            return value
        if not isinstance(value, str):
            raise HandoffValidationError("a handoff state must be a string")
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise HandoffValidationError(f"unknown handoff state: {value!r}") from None


TERMINAL_STATES = frozenset({HandoffState.COMPLETED, HandoffState.FAILED})
NONTERMINAL_STATES = frozenset(HandoffState) - TERMINAL_STATES
_ALL_STATES = frozenset(HandoffState)


class HandoffTrigger(Enum):
    """What just happened to a job, named from the recipient's point of view."""

    ACCEPT = "accept"
    ENQUEUE = "enqueue"
    CAPACITY_WAIT = "capacity_wait"
    START = "start"
    BLOCK = "block"
    UNBLOCK = "unblock"
    COMPLETE = "complete"
    FAIL = "fail"
    EXPIRE = "expire"
    RESTART_RECONCILE = "restart_reconcile"
    RETRY = "retry"
    OBSERVE = "observe"

    @property
    def schedules_execution(self) -> bool:
        """Starting is the single trigger that may hand work to an executor."""
        return self is HandoffTrigger.START


# Which states a trigger may fire from, and where it lands. ``ACCEPT`` has an
# empty source set on purpose: an existing job can never be accepted twice, so
# a redelivered task cannot rewind a job that is already running or finished.
_ALLOWED_FROM: dict[HandoffTrigger, frozenset[HandoffState]] = {
    HandoffTrigger.ACCEPT: frozenset(),
    HandoffTrigger.ENQUEUE: frozenset({HandoffState.ACCEPTED}),
    HandoffTrigger.CAPACITY_WAIT: frozenset({HandoffState.ACCEPTED, HandoffState.QUEUED}),
    HandoffTrigger.START: frozenset({HandoffState.ACCEPTED, HandoffState.QUEUED}),
    HandoffTrigger.BLOCK: frozenset(
        {HandoffState.ACCEPTED, HandoffState.QUEUED, HandoffState.RUNNING}
    ),
    HandoffTrigger.UNBLOCK: frozenset({HandoffState.BLOCKED}),
    HandoffTrigger.COMPLETE: frozenset({HandoffState.RUNNING}),
    HandoffTrigger.FAIL: NONTERMINAL_STATES,
    HandoffTrigger.EXPIRE: NONTERMINAL_STATES,
    HandoffTrigger.RESTART_RECONCILE: frozenset({HandoffState.RUNNING}),
    HandoffTrigger.RETRY: frozenset({HandoffState.FAILED}),
    HandoffTrigger.OBSERVE: _ALL_STATES,
}

_TARGET: dict[HandoffTrigger, HandoffState | None] = {
    HandoffTrigger.ACCEPT: HandoffState.ACCEPTED,
    HandoffTrigger.ENQUEUE: HandoffState.QUEUED,
    HandoffTrigger.CAPACITY_WAIT: HandoffState.QUEUED,
    HandoffTrigger.START: HandoffState.RUNNING,
    HandoffTrigger.BLOCK: HandoffState.BLOCKED,
    HandoffTrigger.UNBLOCK: HandoffState.QUEUED,
    HandoffTrigger.COMPLETE: HandoffState.COMPLETED,
    HandoffTrigger.FAIL: HandoffState.FAILED,
    HandoffTrigger.EXPIRE: HandoffState.FAILED,
    HandoffTrigger.RESTART_RECONCILE: HandoffState.QUEUED,
    HandoffTrigger.RETRY: HandoffState.QUEUED,
    HandoffTrigger.OBSERVE: None,
}

# Mirroring a remote agent's own state is more forgiving about where it starts:
# the observer may have missed the intermediate events (it was offline, or the
# status post was dropped). It is never more forgiving about what it lands on —
# terminal jobs stay terminal, and nothing mirrored can schedule execution.
_MIRROR_FROM: dict[HandoffTrigger, frozenset[HandoffState]] = {
    HandoffTrigger.COMPLETE: NONTERMINAL_STATES,
    HandoffTrigger.CAPACITY_WAIT: NONTERMINAL_STATES,
}

_MIRROR_TRIGGERS: dict[HandoffState, HandoffTrigger] = {
    HandoffState.ACCEPTED: HandoffTrigger.ACCEPT,
    HandoffState.QUEUED: HandoffTrigger.CAPACITY_WAIT,
    HandoffState.RUNNING: HandoffTrigger.START,
    HandoffState.BLOCKED: HandoffTrigger.BLOCK,
    HandoffState.COMPLETED: HandoffTrigger.COMPLETE,
    HandoffState.FAILED: HandoffTrigger.FAIL,
}

_DEFAULT_NOTES: dict[HandoffTrigger, str] = {
    HandoffTrigger.CAPACITY_WAIT: CAPACITY_NOTE,
    HandoffTrigger.RESTART_RECONCILE: RESTART_NOTE,
}


def _fail(message: str) -> NoReturn:
    raise HandoffValidationError(message)


def _require_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        _fail(f"{name} must be a timestamp")
    if value.tzinfo is None:
        _fail(f"{name} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _clean_note(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail("note must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_NOTE_CHARS:
        _fail(f"note exceeds {MAX_NOTE_CHARS} characters")
    if any(ch == "\x00" or (ord(ch) < 0x20 and ch not in "\n\t") for ch in text):
        _fail("note contains control characters")
    return text


def is_legal(state: HandoffState, trigger: HandoffTrigger) -> bool:
    """Say whether ``trigger`` is part of the state machine at ``state``.

    Retry legality also depends on the job's recorded retryability and attempt
    budget; this answers the structural question only.
    """
    if not isinstance(state, HandoffState):
        raise HandoffStateError(f"unknown handoff state: {state!r}")
    if not isinstance(trigger, HandoffTrigger):
        raise HandoffStateError(f"unknown handoff trigger: {trigger!r}")
    return state in _ALLOWED_FROM[trigger]


def legal_triggers(state: HandoffState) -> frozenset[HandoffTrigger]:
    """Every trigger that may fire from ``state``."""
    return frozenset(trigger for trigger in HandoffTrigger if is_legal(state, trigger))


@dataclass(frozen=True)
class HandoffJob:
    """One logical handoff on one recipient, keyed by ``(task_id, recipient)``."""

    task_id: str
    recipient: str
    updated_at: datetime
    state: HandoffState = HandoffState.ACCEPTED
    attempt: int = 1
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retryable: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "task_id", validate_uuid(self.task_id))
        set_(self, "recipient", validate_agent_id(self.recipient))
        if not isinstance(self.state, HandoffState):
            _fail(f"unknown handoff state: {self.state!r}")
        for name in ("attempt", "max_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                _fail(f"{name} must be an integer")
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_LIMIT:
            _fail(f"max_attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}")
        if not 1 <= self.attempt <= self.max_attempts:
            _fail(f"attempt must be between 1 and max_attempts ({self.max_attempts})")
        if not isinstance(self.retryable, bool):
            _fail("retryable must be a boolean")
        if self.retryable and self.state is not HandoffState.FAILED:
            _fail("only a failed job can be marked retryable")
        set_(self, "note", _clean_note(self.note))
        set_(self, "updated_at", _require_utc(self.updated_at, "updated_at"))

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def is_active(self) -> bool:
        return self.state.is_active

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempt)

    @property
    def can_retry(self) -> bool:
        """A safe retry needs a retryable failure and a remaining attempt."""
        return self.state is HandoffState.FAILED and self.retryable and self.attempts_remaining > 0


@dataclass(frozen=True)
class Transition:
    """The outcome of one state change: the new job plus what it authorises."""

    job: HandoffJob
    trigger: HandoffTrigger
    state: HandoffState
    at: datetime
    previous: HandoffState | None = None
    schedules_execution: bool = False
    starts_new_attempt: bool = False
    note: str | None = None

    @property
    def changed(self) -> bool:
        """False for an observation that left the ledger exactly as it was."""
        return self.previous != self.state or self.trigger is not HandoffTrigger.OBSERVE

    def to_payload(self) -> dict[str, str | int | bool]:
        """Render this transition as a protocol ``state`` event payload."""
        payload: dict[str, str | int | bool] = {
            "state": self.state.value,
            "attempt": self.job.attempt,
        }
        if self.note:
            payload["note"] = self.note
        return payload


def _observation(job: HandoffJob, now: datetime) -> Transition:
    """Record that something was seen without touching the stored job."""
    return Transition(
        job=job,
        trigger=HandoffTrigger.OBSERVE,
        state=job.state,
        at=now,
        previous=job.state,
        note=job.note,
    )


def _apply(
    job: HandoffJob,
    trigger: HandoffTrigger,
    *,
    now: datetime,
    note: str | None,
    retryable: bool,
    mirror: bool,
) -> Transition:
    if not isinstance(job, HandoffJob):
        raise HandoffStateError(f"not a handoff job: {job!r}")
    if not isinstance(trigger, HandoffTrigger):
        raise HandoffStateError(f"unknown handoff trigger: {trigger!r}")
    if not isinstance(retryable, bool):
        raise HandoffStateError("retryable must be a boolean")
    moment = _require_utc(now, "now")
    if retryable and trigger is not HandoffTrigger.FAIL:
        raise HandoffStateError(f"retryable describes a failure; {trigger.value} cannot set it")
    clean_note = _clean_note(note)

    if trigger is HandoffTrigger.OBSERVE:
        return _observation(job, moment)

    if trigger is HandoffTrigger.ACCEPT:
        raise IllegalTransitionError(
            f"task {job.task_id} is already stored as {job.state.value}; "
            "a redelivered task never creates or restarts work"
        )

    if trigger is HandoffTrigger.RETRY:
        if job.state is not HandoffState.FAILED:
            raise RetryNotPermittedError(
                f"only a failed handoff can be retried, not {job.state.value}"
            )
        if not job.retryable:
            raise RetryNotPermittedError(
                f"handoff {job.task_id} failed in a way that is not safe to retry"
            )
        if job.attempts_remaining <= 0:
            raise RetryNotPermittedError(
                f"handoff {job.task_id} used all {job.max_attempts} attempts"
            )

    allowed = (
        _MIRROR_FROM[trigger] if mirror and trigger in _MIRROR_FROM else _ALLOWED_FROM[trigger]
    )
    if job.state not in allowed:
        detail = "terminal states are immutable" if job.state.is_terminal else "not a legal move"
        raise IllegalTransitionError(
            f"cannot {trigger.value} a handoff that is {job.state.value}: {detail}"
        )

    target = _TARGET[trigger] or job.state
    starts_new_attempt = trigger is HandoffTrigger.RETRY
    resolved_note = clean_note if clean_note is not None else _DEFAULT_NOTES.get(trigger)
    next_job = HandoffJob(
        task_id=job.task_id,
        recipient=job.recipient,
        updated_at=moment,
        state=target,
        attempt=job.attempt + 1 if starts_new_attempt else job.attempt,
        max_attempts=job.max_attempts,
        retryable=retryable and trigger is HandoffTrigger.FAIL,
        note=resolved_note,
    )
    return Transition(
        job=next_job,
        trigger=trigger,
        state=target,
        at=moment,
        previous=job.state,
        schedules_execution=trigger.schedules_execution and not mirror,
        starts_new_attempt=starts_new_attempt,
        note=resolved_note,
    )


def apply(
    job: HandoffJob,
    trigger: HandoffTrigger,
    *,
    now: datetime,
    note: str | None = None,
    retryable: bool = False,
) -> Transition:
    """Apply a locally decided ``trigger`` to ``job``.

    This is the only entry point that can return ``schedules_execution=True``,
    and only for :data:`HandoffTrigger.START`. Callers must persist the returned
    job before acting on that flag.
    """
    return _apply(job, trigger, now=now, note=note, retryable=retryable, mirror=False)


def accept_task(
    task: HandoffTask, *, now: datetime, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> Transition:
    """Create the first ledger entry for a validated task packet."""
    if not isinstance(task, HandoffTask):
        raise HandoffStateError(f"not a handoff task: {task!r}")
    moment = _require_utc(now, "now")
    if task.is_expired(moment):
        raise HandoffExpiredError(f"task {task.task_id} expired at {task.expires_at.isoformat()}")
    job = HandoffJob(
        task_id=task.task_id,
        recipient=task.recipient,
        updated_at=moment,
        state=HandoffState.ACCEPTED,
        max_attempts=max_attempts,
    )
    return Transition(
        job=job,
        trigger=HandoffTrigger.ACCEPT,
        state=HandoffState.ACCEPTED,
        at=moment,
        previous=None,
    )


def accept_event(
    event: HandoffEvent, *, now: datetime, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> Transition:
    """Create a job from a received event — refused unless it is a task event."""
    if not isinstance(event, HandoffEvent):
        raise HandoffStateError(f"not a handoff event: {event!r}")
    if not event.kind.creates_work or event.task is None:
        raise IllegalTransitionError(
            f"a {event.kind.value} event cannot create work; only a task event can"
        )
    return accept_task(event.task, now=now, max_attempts=max_attempts)


def apply_event(job: HandoffJob, event: HandoffEvent, *, now: datetime | None = None) -> Transition:
    """Mirror a received protocol event onto a stored job.

    Nothing that arrives this way schedules execution: a duplicate task, an ack,
    a question, a mirrored ``running`` status, and a terminal result all leave
    ``schedules_execution`` false. Redelivery of an event the job already
    reflects is an exact no-op, and a result that contradicts a terminal job is
    refused rather than silently rewritten.
    """
    if not isinstance(job, HandoffJob):
        raise HandoffStateError(f"not a handoff job: {job!r}")
    if not isinstance(event, HandoffEvent):
        raise HandoffStateError(f"not a handoff event: {event!r}")
    if event.task_id != job.task_id:
        raise IllegalTransitionError(
            f"event {event.event_id} belongs to task {event.task_id}, not {job.task_id}"
        )
    if job.recipient not in (event.sender, event.recipient):
        raise IllegalTransitionError(
            f"event {event.event_id} does not involve recipient {job.recipient}"
        )
    moment = _require_utc(now if now is not None else event.created_at, "now")

    if event.kind is HandoffEventKind.RESULT:
        target = HandoffState.parse(event.payload.get("outcome"))
        note = event.payload.get("summary")
        retryable = bool(event.payload.get("retryable", False))
    elif event.kind is HandoffEventKind.STATE:
        target = HandoffState.parse(event.payload.get("state"))
        note = event.payload.get("note")
        retryable = False
    else:
        # Task redelivery, acks, questions, and answers never move a job.
        return _observation(job, moment)

    if target is job.state:
        return _observation(job, moment)
    trigger = _MIRROR_TRIGGERS[target]
    return _apply(
        job,
        trigger,
        now=moment,
        note=note if isinstance(note, str) else None,
        retryable=retryable and trigger is HandoffTrigger.FAIL,
        mirror=True,
    )


def safe_retry(job: HandoffJob, *, now: datetime, note: str | None = None) -> Transition:
    """Start a new attempt for a retryable failure, queued rather than running."""
    return apply(job, HandoffTrigger.RETRY, now=now, note=note)


def describe(job: HandoffJob) -> str:
    """A short human-readable status line for a job thread post."""
    parts: list[str] = [job.state.value]
    if job.max_attempts > 1:
        parts.append(f"attempt {job.attempt}/{job.max_attempts}")
    if job.note:
        parts.append(job.note)
    return " · ".join(str(part) for part in parts)
