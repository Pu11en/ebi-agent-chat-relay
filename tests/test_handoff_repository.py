"""Tests for the durable handoff ledger (`claude_discord.database.handoff_repo`).

A handoff is a *job* before it is a conversation, and Discord is an at-least-once
transport that can redeliver, reorder, or drop a message while the recipient is
restarting. So the ledger has to answer three uncomfortable questions correctly:

* Two copies of the same task arrive at the same moment — is there one job or two?
* The bot died mid-execution — can it tell the difference between work that is
  still running and work that merely *says* it is?
* A terminal result was written but the origin conversation was unreachable —
  can it be delivered later without rerunning the task?

These tests drive all three from the outside: real SQLite files, real concurrent
writers, and the real state machine from `claude_code_core.handoffs.state`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from claude_code_core.handoffs import (
    AuthorityScope,
    ConversationCoordinate,
    HandoffCapability,
    HandoffEvent,
    HandoffEventKind,
    HandoffTask,
    ProjectLocator,
)
from claude_code_core.handoffs.state import (
    HandoffJob,
    HandoffState,
    HandoffTrigger,
    apply,
)
from claude_discord.database.handoff_repo import (
    DEFAULT_RETRY_BACKOFF_SECONDS,
    MAX_DELIVERY_ATTEMPTS,
    HandoffRepository,
    OutboxStatus,
)
from claude_discord.database.models import _CORE_SCHEMA, _MIGRATIONS, init_db

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
LOCAL_AGENT = "drewai"
REMOTE_AGENT = "david"


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path() -> AsyncIterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
async def repo(db_path: str) -> HandoffRepository:
    return HandoffRepository(db_path)


def make_task(
    *,
    task_id: str | None = None,
    sender: str = REMOTE_AGENT,
    recipient: str = LOCAL_AGENT,
    authority: AuthorityScope | None = None,
    created_at: datetime = NOW,
    ttl: timedelta = timedelta(hours=6),
) -> HandoffTask:
    origin = ConversationCoordinate(guild_id=111, channel_id=222, thread_id=333, message_id=444)
    return HandoffTask(
        task_id=task_id or str(uuid.uuid4()),
        sender=sender,
        recipient=recipient,
        origin=origin,
        origin_human_id="drew",
        project=ProjectLocator(owner="drew", folder="main-projects/ebi-agent-chat-relay"),
        goal="Report which config files mention the handoff channel.",
        authority=authority or AuthorityScope(read=True),
        expected_result="A short list of file paths.",
        reply_to=origin,
        created_at=created_at,
        expires_at=created_at + ttl,
    )


def make_event(
    task: HandoffTask,
    kind: HandoffEventKind,
    *,
    sequence: int,
    payload: dict[str, str | int | bool] | None = None,
    sender: str | None = None,
    recipient: str | None = None,
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> HandoffEvent:
    return HandoffEvent(
        event_id=event_id or str(uuid.uuid4()),
        kind=kind,
        task_id=task.task_id,
        sender=sender or (task.sender if kind is HandoffEventKind.TASK else task.recipient),
        recipient=recipient or (task.recipient if kind is HandoffEventKind.TASK else task.sender),
        sequence=sequence,
        created_at=created_at or task.created_at,
        task=task if kind is HandoffEventKind.TASK else None,
        payload=payload or ({} if kind is not HandoffEventKind.ACK else {"note": "accepted"}),
    )


@contextlib.asynccontextmanager
async def _pre_handoff_db() -> AsyncIterator[str]:
    """A database holding the schema exactly as it was before handoffs existed."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        async with aiosqlite.connect(path) as db:
            await db.executescript(_CORE_SCHEMA)
            await db.commit()
        yield path
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Schema is additive
# ---------------------------------------------------------------------------


class TestSchemaIsBackwardCompatible:
    async def test_handoff_tables_are_created_on_a_fresh_database(self, db_path: str) -> None:
        async with aiosqlite.connect(db_path) as db:
            rows = await db.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {row[0] for row in rows}
        assert {
            "handoff_tasks",
            "handoff_events",
            "handoff_attempts",
            "handoff_results",
            "handoff_outbox",
        } <= names

    async def test_existing_database_without_handoff_tables_keeps_its_rows(self) -> None:
        """A database written before handoffs existed keeps working and gains them."""
        async with _pre_handoff_db() as path:
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "INSERT INTO sessions (thread_id, session_id) VALUES (7, 'legacy-session')"
                )
                await db.commit()

            await init_db(path)

            async with aiosqlite.connect(path) as db:
                rows = await db.execute_fetchall("SELECT session_id FROM sessions")
                assert [row[0] for row in rows] == ["legacy-session"]
                tables = await db.execute_fetchall(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            assert "handoff_tasks" in {row[0] for row in tables}

    async def test_migration_list_alone_adds_the_handoff_tables(self) -> None:
        """The migration path, not the fresh-database script, is what upgrades.

        ``init_db`` runs both, so this replays only the migrations — proving an
        existing database does not depend on the CREATE script to catch up.
        """
        async with _pre_handoff_db() as path:
            async with aiosqlite.connect(path) as db:
                for statement in _MIGRATIONS:
                    with contextlib.suppress(Exception):
                        await db.execute(statement)
                await db.commit()
                tables = await db.execute_fetchall(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )

            assert {
                "handoff_tasks",
                "handoff_events",
                "handoff_attempts",
                "handoff_results",
                "handoff_outbox",
            } <= {row[0] for row in tables}


# ---------------------------------------------------------------------------
# One logical task per (task_id, recipient)
# ---------------------------------------------------------------------------


class TestTaskLedgerIsIdempotent:
    async def test_recording_a_task_returns_an_accepted_job(self, repo: HandoffRepository) -> None:
        task = make_task()
        created, job = await repo.record_task(task, now=NOW)
        assert created is True
        assert job.task_id == task.task_id
        assert job.recipient == LOCAL_AGENT
        assert job.state is HandoffState.ACCEPTED
        assert job.attempt == 1

    async def test_redelivered_task_returns_the_stored_job_without_a_second_row(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.save_transition(
            apply(
                (await repo.get_job(task.task_id, LOCAL_AGENT)),  # type: ignore[arg-type]
                HandoffTrigger.START,
                now=NOW,
            )
        )

        created, job = await repo.record_task(task, now=NOW + timedelta(minutes=5))

        assert created is False
        assert job.state is HandoffState.RUNNING, "redelivery must not rewind a running job"
        assert await repo.count_tasks() == 1

    async def test_concurrent_duplicate_inserts_schedule_one_logical_task(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()

        results = await asyncio.gather(
            *(repo.record_task(task, now=NOW) for _ in range(8)),
        )

        assert sum(1 for created, _ in results if created) == 1
        assert await repo.count_tasks() == 1
        assert {job.task_id for _, job in results} == {task.task_id}

    async def test_same_task_id_for_a_different_recipient_is_a_separate_job(
        self, repo: HandoffRepository
    ) -> None:
        task_id = str(uuid.uuid4())
        mine = make_task(task_id=task_id, recipient=LOCAL_AGENT)
        theirs = make_task(task_id=task_id, sender=LOCAL_AGENT, recipient="imac")

        assert (await repo.record_task(mine, now=NOW))[0] is True
        assert (await repo.record_task(theirs, now=NOW))[0] is True
        assert await repo.count_tasks() == 2

    async def test_stored_packet_round_trips(self, repo: HandoffRepository) -> None:
        task = make_task(
            authority=AuthorityScope(
                read=True,
                edit=True,
                edit_paths=("claude_discord/database",),
                capabilities=frozenset({HandoffCapability.DESTRUCTIVE}),
            )
        )
        await repo.record_task(task, now=NOW)

        stored = await repo.get_task(task.task_id, LOCAL_AGENT)

        assert stored == task

    async def test_unknown_job_is_none(self, repo: HandoffRepository) -> None:
        assert await repo.get_job(str(uuid.uuid4()), LOCAL_AGENT) is None
        assert await repo.get_task(str(uuid.uuid4()), LOCAL_AGENT) is None


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


class TestTransitionsArePersisted:
    async def _job(self, repo: HandoffRepository, task: HandoffTask) -> HandoffJob:
        await repo.record_task(task, now=NOW)
        job = await repo.get_job(task.task_id, LOCAL_AGENT)
        assert job is not None
        return job

    async def test_state_and_note_survive_a_reload(self, repo: HandoffRepository) -> None:
        task = make_task()
        job = await self._job(repo, task)

        applied = await repo.save_transition(
            apply(job, HandoffTrigger.CAPACITY_WAIT, now=NOW + timedelta(seconds=30))
        )
        assert applied is True

        reloaded = await repo.get_job(task.task_id, LOCAL_AGENT)
        assert reloaded is not None
        assert reloaded.state is HandoffState.QUEUED
        assert reloaded.note == "waiting for local execution capacity"

    async def test_a_stale_transition_is_refused(self, repo: HandoffRepository) -> None:
        """Two writers holding the same job: the second one loses, visibly."""
        task = make_task()
        job = await self._job(repo, task)

        first = apply(job, HandoffTrigger.START, now=NOW)
        second = apply(job, HandoffTrigger.BLOCK, now=NOW, note="needs edit authority")

        assert await repo.save_transition(first) is True
        assert await repo.save_transition(second) is False

        current = await repo.get_job(task.task_id, LOCAL_AGENT)
        assert current is not None
        assert current.state is HandoffState.RUNNING

    async def test_retry_keeps_one_logical_task_and_raises_the_attempt(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        job = await self._job(repo, task)
        running = apply(job, HandoffTrigger.START, now=NOW)
        await repo.save_transition(running)
        failed = apply(
            running.job, HandoffTrigger.FAIL, now=NOW, note="tool crashed", retryable=True
        )
        await repo.save_transition(failed)

        retried = apply(failed.job, HandoffTrigger.RETRY, now=NOW + timedelta(minutes=1))
        assert await repo.save_transition(retried) is True

        current = await repo.get_job(task.task_id, LOCAL_AGENT)
        assert current is not None
        assert current.state is HandoffState.QUEUED
        assert current.attempt == 2
        assert current.retryable is False
        assert await repo.count_tasks() == 1


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestEventLedger:
    async def test_events_are_stored_in_protocol_order(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        task_event = make_event(task, HandoffEventKind.TASK, sequence=0)
        ack = make_event(task, HandoffEventKind.ACK, sequence=1)
        state = make_event(
            task, HandoffEventKind.STATE, sequence=2, payload={"state": "running", "attempt": 1}
        )

        # Deliberately inserted out of order — Discord does not promise order.
        for event in (state, task_event, ack):
            assert await repo.record_event(event) is True

        stored = await repo.list_events(task.task_id)
        assert [item.sequence for item in stored] == [0, 1, 2]
        assert [item.kind for item in stored] == [
            HandoffEventKind.TASK,
            HandoffEventKind.ACK,
            HandoffEventKind.STATE,
        ]
        assert stored[2].payload == {"state": "running", "attempt": 1}

    async def test_duplicate_event_id_is_stored_once(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        ack = make_event(task, HandoffEventKind.ACK, sequence=1)

        assert await repo.record_event(ack) is True
        assert await repo.record_event(ack) is False
        assert await repo.has_event(ack.event_id) is True
        assert len(await repo.list_events(task.task_id)) == 1

    async def test_concurrent_duplicate_events_insert_one_row(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        ack = make_event(task, HandoffEventKind.ACK, sequence=1)

        results = await asyncio.gather(*(repo.record_event(ack) for _ in range(8)))

        assert sum(1 for stored in results if stored) == 1
        assert len(await repo.list_events(task.task_id)) == 1

    async def test_last_sequence_tracks_the_highest_recorded_event(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        assert await repo.last_sequence(task.task_id) is None

        await repo.record_event(make_event(task, HandoffEventKind.TASK, sequence=0))
        await repo.record_event(make_event(task, HandoffEventKind.ACK, sequence=3))

        assert await repo.last_sequence(task.task_id) == 3

    async def test_event_round_trips_through_the_protocol(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        original = make_event(task, HandoffEventKind.TASK, sequence=0)

        await repo.record_event(original)
        stored = (await repo.list_events(task.task_id))[0]

        assert stored.to_event() == original


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


class TestAttemptLedger:
    async def test_an_attempt_is_claimed_once(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)

        claimed = await asyncio.gather(
            *(
                repo.claim_attempt(task.task_id, LOCAL_AGENT, attempt=1, execution_ref="turn-a")
                for _ in range(5)
            )
        )

        assert sum(1 for was_claimed in claimed if was_claimed) == 1

    async def test_finishing_an_attempt_records_its_outcome(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.claim_attempt(task.task_id, LOCAL_AGENT, attempt=1, execution_ref="turn-a")

        await repo.finish_attempt(
            task.task_id, LOCAL_AGENT, attempt=1, outcome="failed", detail="backend timed out"
        )

        attempts = await repo.list_attempts(task.task_id, LOCAL_AGENT)
        assert len(attempts) == 1
        assert attempts[0].execution_ref == "turn-a"
        assert attempts[0].outcome == "failed"
        assert attempts[0].detail == "backend timed out"
        assert attempts[0].finished_at is not None

    async def test_a_retry_claims_a_second_attempt(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.claim_attempt(task.task_id, LOCAL_AGENT, attempt=1, execution_ref="turn-a")

        assert (
            await repo.claim_attempt(task.task_id, LOCAL_AGENT, attempt=2, execution_ref="turn-b")
            is True
        )
        assert [item.attempt for item in await repo.list_attempts(task.task_id, LOCAL_AGENT)] == [
            1,
            2,
        ]

    async def test_unfinished_attempts_are_visible_after_a_restart(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.claim_attempt(task.task_id, LOCAL_AGENT, attempt=1, execution_ref="turn-a")

        assert await repo.unfinished_attempt(task.task_id, LOCAL_AGENT) is not None

        await repo.finish_attempt(task.task_id, LOCAL_AGENT, attempt=1, outcome="completed")
        assert await repo.unfinished_attempt(task.task_id, LOCAL_AGENT) is None


# ---------------------------------------------------------------------------
# Restart safety
# ---------------------------------------------------------------------------


class TestRestartSafety:
    async def test_nonterminal_jobs_are_listed_for_reconciliation(
        self, repo: HandoffRepository
    ) -> None:
        queued = make_task()
        running = make_task()
        done = make_task()
        for task in (queued, running, done):
            await repo.record_task(task, now=NOW)
        await repo.save_transition(
            apply(await _job(repo, queued), HandoffTrigger.CAPACITY_WAIT, now=NOW)
        )
        started = apply(await _job(repo, running), HandoffTrigger.START, now=NOW)
        await repo.save_transition(started)
        finished = apply(
            apply(await _job(repo, done), HandoffTrigger.START, now=NOW).job,
            HandoffTrigger.COMPLETE,
            now=NOW,
        )
        await repo.save_transition(apply(await _job(repo, done), HandoffTrigger.START, now=NOW))
        await repo.save_transition(finished)

        pending = await repo.list_nonterminal(LOCAL_AGENT)

        assert {job.task_id for job in pending} == {queued.task_id, running.task_id}

    async def test_running_jobs_are_requeued_with_a_restart_note(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.save_transition(apply(await _job(repo, task), HandoffTrigger.START, now=NOW))

        requeued = await repo.requeue_running(LOCAL_AGENT, now=NOW + timedelta(minutes=2))

        assert [job.task_id for job in requeued] == [task.task_id]
        current = await repo.get_job(task.task_id, LOCAL_AGENT)
        assert current is not None
        assert current.state is HandoffState.QUEUED
        assert current.attempt == 1, "reconciliation resumes the job, it does not retry it"
        assert current.note == "requeued after restart: no verifiable active execution"

    async def test_requeue_never_touches_a_terminal_job(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        started = apply(await _job(repo, task), HandoffTrigger.START, now=NOW)
        await repo.save_transition(started)
        await repo.save_transition(apply(started.job, HandoffTrigger.COMPLETE, now=NOW))

        assert await repo.requeue_running(LOCAL_AGENT, now=NOW) == []
        current = await repo.get_job(task.task_id, LOCAL_AGENT)
        assert current is not None
        assert current.state is HandoffState.COMPLETED

    async def test_another_agents_jobs_are_not_reconciled(self, repo: HandoffRepository) -> None:
        mine = make_task()
        theirs = make_task(sender=LOCAL_AGENT, recipient="imac")
        await repo.record_task(mine, now=NOW)
        await repo.record_task(theirs, now=NOW)

        assert {job.task_id for job in await repo.list_nonterminal("imac")} == {theirs.task_id}


# ---------------------------------------------------------------------------
# Result + outbox
# ---------------------------------------------------------------------------


class TestResultOutbox:
    async def test_result_and_outbox_row_are_written_together(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        result_event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "completed", "summary": "three files mention it"},
        )

        written = await repo.record_result(task.task_id, LOCAL_AGENT, event=result_event, now=NOW)

        assert written is True
        result = await repo.get_result(task.task_id, LOCAL_AGENT)
        assert result is not None
        assert result.outcome == "completed"
        assert result.summary == "three files mention it"
        pending = await repo.pending_deliveries(now=NOW)
        assert [item.task_id for item in pending] == [task.task_id]
        assert pending[0].destination == task.reply_to
        assert pending[0].status is OutboxStatus.PENDING

    async def test_a_duplicate_result_does_not_queue_a_second_delivery(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "completed", "summary": "done"},
        )

        assert await repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW) is True
        assert await repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW) is False

        assert len(await repo.pending_deliveries(now=NOW)) == 1

    async def test_concurrent_result_writes_queue_one_delivery(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "failed", "summary": "folder missing", "retryable": True},
        )

        written = await asyncio.gather(
            *(repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW) for _ in range(6))
        )

        assert sum(1 for ok in written if ok) == 1
        assert len(await repo.pending_deliveries(now=NOW)) == 1
        result = await repo.get_result(task.task_id, LOCAL_AGENT)
        assert result is not None
        assert result.retryable is True

    async def test_delivery_is_retried_later_without_rerunning_the_task(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "completed", "summary": "done"},
        )
        await repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW)
        pending = (await repo.pending_deliveries(now=NOW))[0]

        await repo.record_delivery_failure(pending.id, now=NOW, error="origin thread unavailable")

        assert await repo.pending_deliveries(now=NOW) == []
        later = NOW + timedelta(seconds=DEFAULT_RETRY_BACKOFF_SECONDS + 1)
        retried = await repo.pending_deliveries(now=later)
        assert [item.id for item in retried] == [pending.id]
        assert retried[0].attempts == 1
        assert retried[0].last_error == "origin thread unavailable"
        # The attempt ledger is untouched: a delivery retry is not an execution retry.
        assert await repo.list_attempts(task.task_id, LOCAL_AGENT) == []

    async def test_backoff_grows_with_each_failure(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.record_result(
            task.task_id,
            LOCAL_AGENT,
            event=make_event(
                task,
                HandoffEventKind.RESULT,
                sequence=4,
                payload={"outcome": "completed", "summary": "done"},
            ),
            now=NOW,
        )
        row_id = (await repo.pending_deliveries(now=NOW))[0].id

        await repo.record_delivery_failure(row_id, now=NOW, error="first")
        await repo.record_delivery_failure(row_id, now=NOW, error="second")

        assert (
            await repo.pending_deliveries(
                now=NOW + timedelta(seconds=DEFAULT_RETRY_BACKOFF_SECONDS + 1)
            )
            == []
        )
        due = NOW + timedelta(seconds=DEFAULT_RETRY_BACKOFF_SECONDS * 2 + 1)
        assert [item.id for item in await repo.pending_deliveries(now=due)] == [row_id]

    async def test_delivery_stops_at_the_retry_bound(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.record_result(
            task.task_id,
            LOCAL_AGENT,
            event=make_event(
                task,
                HandoffEventKind.RESULT,
                sequence=4,
                payload={"outcome": "completed", "summary": "done"},
            ),
            now=NOW,
        )
        row_id = (await repo.pending_deliveries(now=NOW))[0].id

        for _ in range(MAX_DELIVERY_ATTEMPTS):
            await repo.record_delivery_failure(row_id, now=NOW, error="origin unavailable")

        far_future = NOW + timedelta(days=30)
        assert await repo.pending_deliveries(now=far_future) == []
        abandoned = await repo.get_delivery(row_id)
        assert abandoned is not None
        assert abandoned.status is OutboxStatus.ABANDONED

    async def test_delivered_rows_are_not_returned_again(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.record_result(
            task.task_id,
            LOCAL_AGENT,
            event=make_event(
                task,
                HandoffEventKind.RESULT,
                sequence=4,
                payload={"outcome": "completed", "summary": "done"},
            ),
            now=NOW,
        )
        row_id = (await repo.pending_deliveries(now=NOW))[0].id

        assert await repo.mark_delivered(row_id, now=NOW) is True
        assert await repo.mark_delivered(row_id, now=NOW) is False
        assert await repo.pending_deliveries(now=NOW + timedelta(days=1)) == []
        delivered = await repo.get_delivery(row_id)
        assert delivered is not None
        assert delivered.status is OutboxStatus.DELIVERED
        assert delivered.delivered_at is not None

    async def test_outbox_payload_round_trips_to_the_result_event(
        self, repo: HandoffRepository
    ) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "completed", "summary": "done"},
        )
        await repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW)

        pending = (await repo.pending_deliveries(now=NOW))[0]

        assert pending.to_event() == event

    async def test_recording_a_result_also_stores_its_event(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "completed", "summary": "done"},
        )

        await repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW)

        assert await repo.has_event(event.event_id) is True

    async def test_a_nonresult_event_cannot_write_a_result(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)
        ack = make_event(task, HandoffEventKind.ACK, sequence=1)

        with pytest.raises(ValueError, match="result"):
            await repo.record_result(task.task_id, LOCAL_AGENT, event=ack, now=NOW)

    async def test_a_result_for_an_unknown_job_is_refused(self, repo: HandoffRepository) -> None:
        task = make_task()
        event = make_event(
            task,
            HandoffEventKind.RESULT,
            sequence=4,
            payload={"outcome": "completed", "summary": "done"},
        )

        with pytest.raises(ValueError, match="no stored handoff"):
            await repo.record_result(task.task_id, LOCAL_AGENT, event=event, now=NOW)


# ---------------------------------------------------------------------------
# Job thread bookkeeping
# ---------------------------------------------------------------------------


class TestJobThread:
    async def test_job_thread_is_remembered(self, repo: HandoffRepository) -> None:
        task = make_task()
        await repo.record_task(task, now=NOW)

        assert await repo.set_job_thread(task.task_id, LOCAL_AGENT, 987654321) is True

        assert await repo.get_job_thread(task.task_id, LOCAL_AGENT) == 987654321
        assert await repo.find_by_job_thread(987654321) is not None

    async def test_job_thread_for_an_unknown_job_is_none(self, repo: HandoffRepository) -> None:
        assert await repo.get_job_thread(str(uuid.uuid4()), LOCAL_AGENT) is None
        assert await repo.find_by_job_thread(12345) is None


async def _job(repo: HandoffRepository, task: HandoffTask) -> HandoffJob:
    job = await repo.get_job(task.task_id, task.recipient)
    assert job is not None
    return job
