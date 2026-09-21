"""The durable ledger behind trusted cross-computer agent handoffs.

A handoff is a job before it is a conversation, and Discord is an at-least-once
transport: the same task packet can arrive twice, arrive out of order, or arrive
while the recipient is restarting. This repository is where that stops being a
problem, and it does so with database constraints rather than in-process locks —
a lock dies with the process, a `UNIQUE` index does not.

Three constraints carry the design:

* ``handoff_tasks(task_id, recipient_agent_id)`` — two concurrent copies of a
  packet insert one row, so one logical task is scheduled no matter how many
  times Discord delivers it. The same task id addressed to a *different*
  recipient is deliberately a different job.
* ``handoff_events(event_id)`` — a redelivered event is recognised and reported
  as already-seen instead of being applied a second time.
* ``handoff_results(task_id, recipient_agent_id)`` and ``handoff_outbox(event_id)``
  — a terminal result and its origin-delivery row are written in one
  transaction, so an origin that was unreachable is retried from the outbox
  without ever rerunning the recipient's work.

The module stores state; it never decides it. Legality lives in
:mod:`claude_code_core.handoffs.state`, and callers pass its
:class:`~claude_code_core.handoffs.state.Transition` here to be persisted.
:meth:`HandoffRepository.save_transition` is a compare-and-set against the state
the transition was computed from, so two writers racing on one job cannot both
win — the loser gets ``False`` and re-reads rather than silently clobbering.

Nothing in this module talks to Discord or starts execution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

import aiosqlite

from claude_code_core.handoffs.protocol import (
    ConversationCoordinate,
    HandoffEvent,
    HandoffEventKind,
    HandoffTask,
    HandoffValidationError,
)
from claude_code_core.handoffs.state import (
    DEFAULT_MAX_ATTEMPTS,
    RESTART_NOTE,
    HandoffJob,
    HandoffState,
    HandoffTrigger,
    Transition,
    apply,
)

logger = logging.getLogger(__name__)

# Delivery retry bounds. A result that cannot reach its origin waits rather than
# spinning: the spec asks for bounded retry, then a visible give-up.
DEFAULT_RETRY_BACKOFF_SECONDS = 60
MAX_RETRY_BACKOFF_SECONDS = 60 * 60
MAX_DELIVERY_ATTEMPTS = 6

MAX_ERROR_CHARS = 500
INTERRUPTED_OUTCOME = "interrupted"


class OutboxStatus(Enum):
    """Where an origin delivery stands."""

    PENDING = "pending"
    DELIVERED = "delivered"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class StoredEvent:
    """One protocol event as it was recorded, with its storage order."""

    id: int
    event_id: str
    task_id: str
    kind: HandoffEventKind
    sender: str
    recipient: str
    sequence: int
    payload: dict[str, str | int | bool]
    created_at: datetime
    task: HandoffTask | None = None

    def to_event(self) -> HandoffEvent:
        """Rebuild the validated protocol event this row came from."""
        return HandoffEvent(
            event_id=self.event_id,
            kind=self.kind,
            task_id=self.task_id,
            sender=self.sender,
            recipient=self.recipient,
            sequence=self.sequence,
            created_at=self.created_at,
            task=self.task,
            payload=dict(self.payload),
        )


@dataclass(frozen=True)
class AttemptRecord:
    """One execution attempt of a logical job."""

    id: int
    task_id: str
    recipient: str
    attempt: int
    execution_ref: str | None
    outcome: str | None
    detail: str | None
    started_at: datetime
    finished_at: datetime | None

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None


@dataclass(frozen=True)
class ResultRecord:
    """The terminal outcome of a logical job, written once."""

    task_id: str
    recipient: str
    event_id: str
    outcome: str
    summary: str | None
    retryable: bool
    created_at: datetime


@dataclass(frozen=True)
class OutboxEntry:
    """A terminal result waiting to reach the origin conversation."""

    id: int
    event_id: str
    task_id: str
    recipient: str
    destination: ConversationCoordinate
    payload: dict[str, object]
    status: OutboxStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None

    def to_event(self) -> HandoffEvent:
        """Rebuild the result event that should be sent to the origin."""
        return HandoffEvent.from_dict(self.payload)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise HandoffValidationError("timestamps stored in the ledger must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _require(value: str | None, field: str) -> datetime:
    parsed = _parse(value)
    if parsed is None:  # pragma: no cover — the columns are NOT NULL
        raise HandoffValidationError(f"stored handoff row is missing {field}")
    return parsed


def _backoff(attempts: int) -> int:
    """Exponential delay after ``attempts`` failed deliveries, bounded."""
    return min(MAX_RETRY_BACKOFF_SECONDS, DEFAULT_RETRY_BACKOFF_SECONDS * (2 ** max(0, attempts)))


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    return cleaned[:MAX_ERROR_CHARS] or None


def _job_from_row(row: aiosqlite.Row) -> HandoffJob:
    return HandoffJob(
        task_id=row["task_id"],
        recipient=row["recipient_agent_id"],
        updated_at=_require(row["updated_at"], "updated_at"),
        state=HandoffState.parse(row["state"]),
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        retryable=bool(row["retryable"]),
        note=row["note"],
    )


class HandoffRepository:
    """CRUD for the handoff ledger.

    Every method opens a short-lived connection, matching the other
    repositories in this package. Writes that must not interleave open with
    ``BEGIN IMMEDIATE`` so two racing writers serialise at the database rather
    than both reading "absent" and both inserting.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # -- tasks ---------------------------------------------------------------

    async def record_task(
        self,
        task: HandoffTask,
        *,
        now: datetime,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> tuple[bool, HandoffJob]:
        """Store a validated packet, or return the job that already exists.

        This is the idempotency boundary of the whole protocol: concurrent
        duplicate deliveries of one packet produce exactly one ``created=True``
        and one row, and a redelivery that arrives after the job has moved on
        returns the *current* job rather than rewinding it to accepted.

        Returns:
            ``(created, job)`` — ``created`` is True only for the insert that won.
        """
        if not isinstance(task, HandoffTask):
            raise TypeError("record_task needs a validated HandoffTask")
        stamp = _iso(now)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO handoff_tasks (
                    task_id, recipient_agent_id, sender_agent_id, packet_json,
                    state, attempt, max_attempts, retryable, note,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, 0, NULL, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.recipient,
                    task.sender,
                    json.dumps(task.to_dict(), ensure_ascii=False, sort_keys=True),
                    HandoffState.ACCEPTED.value,
                    max_attempts,
                    _iso(task.expires_at),
                    stamp,
                    stamp,
                ),
            )
            created = cursor.rowcount > 0
            row = await self._fetch_task_row(db, task.task_id, task.recipient)
            await db.commit()

        if row is None:  # pragma: no cover — the INSERT above guarantees a row
            raise RuntimeError(f"failed to read back handoff {task.task_id}")
        return created, _job_from_row(row)

    async def get_job(self, task_id: str, recipient: str) -> HandoffJob | None:
        """The current ledger state of one logical job."""
        row = await self._one(
            "SELECT * FROM handoff_tasks WHERE task_id = ? AND recipient_agent_id = ?",
            (task_id, recipient),
        )
        return None if row is None else _job_from_row(row)

    async def get_task(self, task_id: str, recipient: str) -> HandoffTask | None:
        """The packet exactly as it was authorized and stored."""
        row = await self._one(
            "SELECT packet_json FROM handoff_tasks WHERE task_id = ? AND recipient_agent_id = ?",
            (task_id, recipient),
        )
        return None if row is None else HandoffTask.from_dict(json.loads(row["packet_json"]))

    async def count_tasks(self) -> int:
        """How many logical jobs the ledger holds (used by tests and status)."""
        row = await self._one("SELECT COUNT(*) AS n FROM handoff_tasks", ())
        return 0 if row is None else int(row["n"])

    async def save_transition(self, transition: Transition) -> bool:
        """Persist a state-machine outcome, if the job has not moved since.

        The ``WHERE`` clause pins the state and attempt the transition was
        computed from. A second writer working from the same snapshot therefore
        updates nothing and gets ``False`` — the caller must re-read rather than
        assume its view is current. This is what keeps "only one writer may
        schedule execution" true across processes.
        """
        if not isinstance(transition, Transition):
            raise TypeError("save_transition needs a Transition")
        if transition.previous is None:
            raise ValueError("an accept transition is persisted by record_task")
        job = transition.job
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE handoff_tasks
                   SET state = ?, attempt = ?, retryable = ?, note = ?, updated_at = ?
                 WHERE task_id = ? AND recipient_agent_id = ? AND state = ? AND attempt = ?
                """,
                (
                    job.state.value,
                    job.attempt,
                    int(job.retryable),
                    job.note,
                    _iso(job.updated_at),
                    job.task_id,
                    job.recipient,
                    transition.previous.value,
                    job.attempt - 1 if transition.starts_new_attempt else job.attempt,
                ),
            )
            await db.commit()
            applied = cursor.rowcount > 0
        if not applied:
            logger.debug(
                "handoff %s/%s moved under a %s transition; not applied",
                job.task_id,
                job.recipient,
                transition.trigger.value,
            )
        return applied

    async def list_nonterminal(self, recipient: str) -> list[HandoffJob]:
        """Jobs this agent still owes an answer for — the reconciliation input."""
        rows = await self._all(
            """
            SELECT * FROM handoff_tasks
             WHERE recipient_agent_id = ? AND state NOT IN (?, ?)
             ORDER BY id
            """,
            (recipient, HandoffState.COMPLETED.value, HandoffState.FAILED.value),
        )
        return [_job_from_row(row) for row in rows]

    async def requeue_running(self, recipient: str, *, now: datetime) -> list[HandoffJob]:
        """Return jobs stuck in ``running`` to ``queued`` after a restart.

        A row that says ``running`` after a restart is a claim nobody can still
        honour: the process that held it is gone. Requeueing keeps the same
        attempt number on purpose — this resumes the job, it does not spend a
        retry on it — and never touches a terminal row.
        """
        requeued: list[HandoffJob] = []
        for job in await self.list_nonterminal(recipient):
            if job.state is not HandoffState.RUNNING:
                continue
            transition = apply(job, HandoffTrigger.RESTART_RECONCILE, now=now, note=RESTART_NOTE)
            if await self.save_transition(transition):
                # The claim that process held is closed as interrupted so the
                # same attempt number can be reclaimed — resumed, not retried.
                await self.finish_attempt(
                    job.task_id,
                    recipient,
                    attempt=job.attempt,
                    outcome=INTERRUPTED_OUTCOME,
                    detail=RESTART_NOTE,
                    now=now,
                )
                requeued.append(transition.job)
        return requeued

    # -- job thread ----------------------------------------------------------

    async def set_job_thread(self, task_id: str, recipient: str, thread_id: int) -> bool:
        """Remember the Discord thread that shows this job."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE handoff_tasks SET job_thread_id = ?
                 WHERE task_id = ? AND recipient_agent_id = ?
                """,
                (int(thread_id), task_id, recipient),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_job_thread(self, task_id: str, recipient: str) -> int | None:
        row = await self._one(
            "SELECT job_thread_id FROM handoff_tasks WHERE task_id = ? AND recipient_agent_id = ?",
            (task_id, recipient),
        )
        if row is None or row["job_thread_id"] is None:
            return None
        return int(row["job_thread_id"])

    async def find_by_job_thread(self, thread_id: int) -> HandoffJob | None:
        """Which job a Discord thread belongs to, for inbound event routing."""
        row = await self._one(
            "SELECT * FROM handoff_tasks WHERE job_thread_id = ?",
            (int(thread_id),),
        )
        return None if row is None else _job_from_row(row)

    # -- events --------------------------------------------------------------

    async def record_event(self, event: HandoffEvent) -> bool:
        """Store one protocol event.

        Returns:
            True when this event id was new, False when it was a redelivery.
            A False answer is the caller's signal to report the current state
            instead of applying the event again.
        """
        if not isinstance(event, HandoffEvent):
            raise TypeError("record_event needs a validated HandoffEvent")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await self._insert_event(db, event)
            await db.commit()
            return cursor.rowcount > 0

    async def has_event(self, event_id: str) -> bool:
        row = await self._one("SELECT 1 AS hit FROM handoff_events WHERE event_id = ?", (event_id,))
        return row is not None

    async def list_events(self, task_id: str) -> list[StoredEvent]:
        """Every stored event for a task, in the order the protocol declares.

        Ordering is by ``sequence`` first and insertion second, because Discord
        does not promise arrival order — the sequence is what the agents agreed
        on, the row id only breaks ties.
        """
        rows = await self._all(
            "SELECT * FROM handoff_events WHERE task_id = ? ORDER BY sequence, id",
            (task_id,),
        )
        return [_event_from_row(row) for row in rows]

    async def last_sequence(self, task_id: str) -> int | None:
        """The highest sequence recorded for a task, or None when it has none."""
        row = await self._one(
            "SELECT MAX(sequence) AS top FROM handoff_events WHERE task_id = ?",
            (task_id,),
        )
        if row is None or row["top"] is None:
            return None
        return int(row["top"])

    # -- attempts ------------------------------------------------------------

    async def claim_attempt(
        self,
        task_id: str,
        recipient: str,
        *,
        attempt: int,
        execution_ref: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Claim attempt number ``attempt`` for this job.

        The unique ``(task_id, recipient, attempt)`` index means only one caller
        can win, even across processes — so an executor that reads ``queued``
        twice still runs the work once.
        """
        stamp = _iso(now or datetime.now(UTC))
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO handoff_attempts (
                    task_id, recipient_agent_id, attempt, execution_ref, started_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, recipient, int(attempt), execution_ref, stamp),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def reclaim_interrupted_attempt(
        self,
        task_id: str,
        recipient: str,
        *,
        attempt: int,
        execution_ref: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Re-open an attempt a restart interrupted, so the job resumes under it.

        Only an attempt closed as ``interrupted`` qualifies — a failed or
        completed attempt stays closed — and the compare-and-set on that
        outcome means two resuming processes cannot both win.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE handoff_attempts
                   SET outcome = NULL, detail = NULL, finished_at = NULL,
                       execution_ref = ?, started_at = ?
                 WHERE task_id = ? AND recipient_agent_id = ? AND attempt = ?
                   AND finished_at IS NOT NULL AND outcome = ?
                """,
                (
                    execution_ref,
                    _iso(now or datetime.now(UTC)),
                    task_id,
                    recipient,
                    int(attempt),
                    INTERRUPTED_OUTCOME,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def finish_attempt(
        self,
        task_id: str,
        recipient: str,
        *,
        attempt: int,
        outcome: str,
        detail: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Close an attempt with its outcome (only an open attempt can be closed)."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE handoff_attempts
                   SET outcome = ?, detail = ?, finished_at = ?
                 WHERE task_id = ? AND recipient_agent_id = ? AND attempt = ?
                   AND finished_at IS NULL
                """,
                (
                    outcome,
                    _clip(detail),
                    _iso(now or datetime.now(UTC)),
                    task_id,
                    recipient,
                    int(attempt),
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_attempts(self, task_id: str, recipient: str) -> list[AttemptRecord]:
        rows = await self._all(
            """
            SELECT * FROM handoff_attempts
             WHERE task_id = ? AND recipient_agent_id = ?
             ORDER BY attempt
            """,
            (task_id, recipient),
        )
        return [_attempt_from_row(row) for row in rows]

    async def unfinished_attempt(self, task_id: str, recipient: str) -> AttemptRecord | None:
        """The attempt that was claimed and never closed, if there is one.

        After a restart this is the difference between work that may still be
        running and a row that merely says ``running``.
        """
        row = await self._one(
            """
            SELECT * FROM handoff_attempts
             WHERE task_id = ? AND recipient_agent_id = ? AND finished_at IS NULL
             ORDER BY attempt DESC LIMIT 1
            """,
            (task_id, recipient),
        )
        return None if row is None else _attempt_from_row(row)

    # -- results and origin delivery -----------------------------------------

    async def record_result(
        self,
        task_id: str,
        recipient: str,
        *,
        event: HandoffEvent,
        now: datetime,
        destination: ConversationCoordinate | None = None,
    ) -> bool:
        """Write the terminal result and its origin-delivery row together.

        One transaction, three inserts: the result, the event that carried it,
        and the outbox row that owes the origin an answer. Writing them apart
        would allow a completed job whose origin is never told, or a delivery
        for a result that was never recorded.

        ``destination`` defaults to the packet's recorded ``reply_to``; nothing
        in the event may redirect it.

        Returns:
            True when this result was new, False for a duplicate.
        """
        if not isinstance(event, HandoffEvent):
            raise TypeError("record_result needs a validated HandoffEvent")
        if event.kind is not HandoffEventKind.RESULT:
            raise ValueError(f"a {event.kind.value} event cannot record a result")
        if event.task_id != task_id:
            raise ValueError(f"event {event.event_id} belongs to task {event.task_id}")

        task = await self.get_task(task_id, recipient)
        if task is None:
            raise ValueError(f"no stored handoff {task_id} for recipient {recipient}")
        target = destination if destination is not None else task.reply_to
        outcome = str(event.payload.get("outcome", ""))
        if outcome not in ("completed", "failed"):
            raise ValueError(f"result outcome must be completed or failed, not {outcome!r}")
        summary = event.payload.get("summary")
        stamp = _iso(now)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO handoff_results (
                    task_id, recipient_agent_id, event_id, outcome, summary, retryable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    recipient,
                    event.event_id,
                    outcome,
                    summary if isinstance(summary, str) else None,
                    int(bool(event.payload.get("retryable", False))),
                    stamp,
                ),
            )
            if cursor.rowcount == 0:
                await db.commit()
                return False
            await db.execute(
                """
                INSERT OR IGNORE INTO handoff_outbox (
                    event_id, task_id, recipient_agent_id, destination_json, payload_json,
                    status, attempts, next_attempt_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    event.event_id,
                    task_id,
                    recipient,
                    json.dumps(target.to_dict(), sort_keys=True),
                    json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True),
                    OutboxStatus.PENDING.value,
                    stamp,
                    stamp,
                ),
            )
            await self._insert_event(db, event)
            await db.commit()
        return True

    async def get_result(self, task_id: str, recipient: str) -> ResultRecord | None:
        row = await self._one(
            "SELECT * FROM handoff_results WHERE task_id = ? AND recipient_agent_id = ?",
            (task_id, recipient),
        )
        if row is None:
            return None
        return ResultRecord(
            task_id=row["task_id"],
            recipient=row["recipient_agent_id"],
            event_id=row["event_id"],
            outcome=row["outcome"],
            summary=row["summary"],
            retryable=bool(row["retryable"]),
            created_at=_require(row["created_at"], "created_at"),
        )

    async def pending_deliveries(self, *, now: datetime, limit: int = 20) -> list[OutboxEntry]:
        """Origin deliveries that are due, oldest first."""
        rows = await self._all(
            """
            SELECT * FROM handoff_outbox
             WHERE status = ? AND next_attempt_at <= ?
             ORDER BY next_attempt_at, id
             LIMIT ?
            """,
            (OutboxStatus.PENDING.value, _iso(now), int(limit)),
        )
        return [_outbox_from_row(row) for row in rows]

    async def get_delivery(self, row_id: int) -> OutboxEntry | None:
        row = await self._one("SELECT * FROM handoff_outbox WHERE id = ?", (int(row_id),))
        return None if row is None else _outbox_from_row(row)

    async def mark_delivered(self, row_id: int, *, now: datetime) -> bool:
        """Record that the origin received the result. Idempotent."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE handoff_outbox
                   SET status = ?, delivered_at = ?, last_error = NULL
                 WHERE id = ? AND status = ?
                """,
                (
                    OutboxStatus.DELIVERED.value,
                    _iso(now),
                    int(row_id),
                    OutboxStatus.PENDING.value,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def record_delivery_failure(
        self, row_id: int, *, now: datetime, error: str | None = None
    ) -> bool:
        """Schedule the next delivery attempt, or give up at the bound.

        Retrying delivery is not retrying the task: the attempt ledger is
        untouched, and the recorded result is never recomputed. When the retry
        budget runs out the row becomes ``abandoned`` so the give-up is visible
        instead of being an unexplained silence.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT attempts, status FROM handoff_outbox WHERE id = ?",
                (int(row_id),),
            )
            row = await cursor.fetchone()
            if row is None or row["status"] != OutboxStatus.PENDING.value:
                await db.commit()
                return False
            attempts = int(row["attempts"]) + 1
            exhausted = attempts >= MAX_DELIVERY_ATTEMPTS
            await db.execute(
                """
                UPDATE handoff_outbox
                   SET attempts = ?, status = ?, last_error = ?, next_attempt_at = ?
                 WHERE id = ?
                """,
                (
                    attempts,
                    (OutboxStatus.ABANDONED if exhausted else OutboxStatus.PENDING).value,
                    _clip(error),
                    _iso(now + timedelta(seconds=_backoff(attempts - 1))),
                    int(row_id),
                ),
            )
            await db.commit()
        return True

    # -- shared plumbing -----------------------------------------------------

    @staticmethod
    async def _insert_event(db: aiosqlite.Connection, event: HandoffEvent) -> aiosqlite.Cursor:
        return await db.execute(
            """
            INSERT OR IGNORE INTO handoff_events (
                event_id, task_id, kind, sender_agent_id, recipient_agent_id,
                sequence, payload_json, packet_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.task_id,
                event.kind.value,
                event.sender,
                event.recipient,
                event.sequence,
                json.dumps(dict(event.payload), ensure_ascii=False, sort_keys=True),
                (
                    json.dumps(event.task.to_dict(), ensure_ascii=False, sort_keys=True)
                    if event.task is not None
                    else None
                ),
                _iso(event.created_at),
            ),
        )

    @staticmethod
    async def _fetch_task_row(
        db: aiosqlite.Connection, task_id: str, recipient: str
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM handoff_tasks WHERE task_id = ? AND recipient_agent_id = ?",
            (task_id, recipient),
        )
        return await cursor.fetchone()

    async def _one(self, sql: str, params: tuple[object, ...]) -> aiosqlite.Row | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            return await cursor.fetchone()

    async def _all(self, sql: str, params: tuple[object, ...]) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            return list(await cursor.fetchall())


def _event_from_row(row: aiosqlite.Row) -> StoredEvent:
    packet = row["packet_json"]
    return StoredEvent(
        id=int(row["id"]),
        event_id=row["event_id"],
        task_id=row["task_id"],
        kind=HandoffEventKind(row["kind"]),
        sender=row["sender_agent_id"],
        recipient=row["recipient_agent_id"],
        sequence=int(row["sequence"]),
        payload=json.loads(row["payload_json"]),
        created_at=_require(row["created_at"], "created_at"),
        task=HandoffTask.from_dict(json.loads(packet)) if packet else None,
    )


def _attempt_from_row(row: aiosqlite.Row) -> AttemptRecord:
    return AttemptRecord(
        id=int(row["id"]),
        task_id=row["task_id"],
        recipient=row["recipient_agent_id"],
        attempt=int(row["attempt"]),
        execution_ref=row["execution_ref"],
        outcome=row["outcome"],
        detail=row["detail"],
        started_at=_require(row["started_at"], "started_at"),
        finished_at=_parse(row["finished_at"]),
    )


def _outbox_from_row(row: aiosqlite.Row) -> OutboxEntry:
    return OutboxEntry(
        id=int(row["id"]),
        event_id=row["event_id"],
        task_id=row["task_id"],
        recipient=row["recipient_agent_id"],
        destination=ConversationCoordinate.from_dict(json.loads(row["destination_json"])),
        payload=json.loads(row["payload_json"]),
        status=OutboxStatus(row["status"]),
        attempts=int(row["attempts"]),
        next_attempt_at=_require(row["next_attempt_at"], "next_attempt_at"),
        last_error=row["last_error"],
        created_at=_require(row["created_at"], "created_at"),
        delivered_at=_parse(row["delivered_at"]),
    )


__all__ = [
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "MAX_DELIVERY_ATTEMPTS",
    "MAX_RETRY_BACKOFF_SECONDS",
    "AttemptRecord",
    "HandoffRepository",
    "OutboxEntry",
    "OutboxStatus",
    "ResultRecord",
    "StoredEvent",
]
