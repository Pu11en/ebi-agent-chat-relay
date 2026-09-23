"""Tests for the durable handoff job state machine (OpenSpec task 1.2).

The state machine is the part of a handoff that decides whether work may start.
Everything here therefore pushes on three promises from the design:

* a terminal event can neither create work nor restart it,
* waiting for local capacity keeps a job queued instead of failing it,
* an illegal transition fails the same way every time, with no side effect.

Like the protocol module this layer is surface-neutral: no Discord, no database.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_code_core.handoffs import protocol as p
from claude_code_core.handoffs import state as s

Payload = dict[str, str | int | bool]

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


def _coordinate() -> p.ConversationCoordinate:
    return p.ConversationCoordinate(guild_id=111, channel_id=222, thread_id=333, message_id=444)


def _task(**overrides: object) -> p.HandoffTask:
    fields: dict[str, object] = {
        "task_id": TASK_ID,
        "sender": "drewai",
        "recipient": "david",
        "origin": _coordinate(),
        "origin_human_id": "drew",
        "project": p.ProjectLocator(owner="drew", folder="main-projects/realpage"),
        "goal": "Report how many rows the leads table has.",
        "authority": p.AuthorityScope(read=True),
        "expected_result": "One number plus the file it came from.",
        "reply_to": _coordinate(),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=2),
    }
    fields.update(overrides)
    return p.HandoffTask(**fields)  # type: ignore[arg-type]


def _event(
    kind: p.HandoffEventKind,
    *,
    payload: Payload | None = None,
    sequence: int = 1,
    sender: str = "david",
    recipient: str = "drewai",
) -> p.HandoffEvent:
    return p.HandoffEvent(
        event_id=EVENT_ID,
        kind=kind,
        task_id=TASK_ID,
        sender=sender,
        recipient=recipient,
        sequence=sequence,
        created_at=LATER,
        payload=payload or {},
    )


def _task_event() -> p.HandoffEvent:
    return p.HandoffEvent(
        event_id=EVENT_ID,
        kind=p.HandoffEventKind.TASK,
        task_id=TASK_ID,
        sender="drewai",
        recipient="david",
        sequence=0,
        created_at=NOW,
        task=_task(),
    )


def _any_job(state: s.HandoffState) -> s.HandoffJob:
    """A job in ``state``, retryable whenever that state allows it to be."""
    return _job(state, retryable=state is s.HandoffState.FAILED)


def _job(state: s.HandoffState = s.HandoffState.ACCEPTED, **overrides: object) -> s.HandoffJob:
    fields: dict[str, object] = {
        "task_id": TASK_ID,
        "recipient": "david",
        "state": state,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return s.HandoffJob(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Surface neutrality and protocol agreement
# --------------------------------------------------------------------------


def test_state_module_stays_surface_neutral() -> None:
    source = Path(s.__file__).read_text(encoding="utf-8")
    for forbidden in ("import discord", "aiosqlite", "claude_discord"):
        assert forbidden not in source


def test_the_six_named_states_are_exactly_the_spec_states() -> None:
    assert {member.value for member in s.HandoffState} == {
        "accepted",
        "queued",
        "running",
        "blocked",
        "completed",
        "failed",
    }
    assert {st.value for st in s.TERMINAL_STATES} == {"completed", "failed"}
    assert frozenset(s.HandoffState) == s.TERMINAL_STATES | s.NONTERMINAL_STATES
    assert not s.TERMINAL_STATES & s.NONTERMINAL_STATES


def test_terminal_state_values_match_the_protocol_result_outcomes() -> None:
    assert {st.value for st in s.TERMINAL_STATES} == set(p.RESULT_OUTCOMES)


def test_transition_payload_is_a_valid_protocol_state_payload() -> None:
    transition = s.apply(_job(s.HandoffState.QUEUED), s.HandoffTrigger.START, now=LATER)
    event = p.HandoffEvent(
        event_id=EVENT_ID,
        kind=p.HandoffEventKind.STATE,
        task_id=TASK_ID,
        sender="david",
        recipient="drewai",
        sequence=2,
        created_at=LATER,
        payload=transition.to_payload(),
    )
    assert event.payload["state"] == "running"
    assert event.payload["attempt"] == 1


# --------------------------------------------------------------------------
# Creating a job
# --------------------------------------------------------------------------


def test_accepting_a_task_stores_an_accepted_job_without_scheduling_it() -> None:
    transition = s.accept_task(_task(), now=NOW)
    assert transition.previous is None
    assert transition.trigger is s.HandoffTrigger.ACCEPT
    assert transition.state is s.HandoffState.ACCEPTED
    assert transition.schedules_execution is False
    job = transition.job
    assert (job.task_id, job.recipient) == (TASK_ID, "david")
    assert job.attempt == 1
    assert job.is_terminal is False


def test_accepting_an_expired_task_is_refused() -> None:
    task = _task()
    with pytest.raises(s.HandoffExpiredError):
        s.accept_task(task, now=task.expires_at)


def test_only_a_task_event_can_create_a_job() -> None:
    accepted = s.accept_event(_task_event(), now=NOW)
    assert accepted.state is s.HandoffState.ACCEPTED

    for kind in (
        p.HandoffEventKind.ACK,
        p.HandoffEventKind.STATE,
        p.HandoffEventKind.QUESTION,
        p.HandoffEventKind.ANSWER,
        p.HandoffEventKind.RESULT,
    ):
        payloads: dict[p.HandoffEventKind, Payload] = {
            p.HandoffEventKind.STATE: {"state": "running"},
            p.HandoffEventKind.QUESTION: {"question": "which folder?"},
            p.HandoffEventKind.ANSWER: {"answer": "the leads one"},
            p.HandoffEventKind.RESULT: {"outcome": "completed", "summary": "1,876 rows"},
        }
        payload: Payload = payloads.get(kind, {})
        with pytest.raises(s.IllegalTransitionError):
            s.accept_event(_event(kind, payload=payload), now=LATER)


def test_an_existing_job_cannot_be_accepted_again() -> None:
    with pytest.raises(s.IllegalTransitionError):
        s.apply(_job(), s.HandoffTrigger.ACCEPT, now=LATER)


def test_a_job_is_frozen_and_validates_its_identity() -> None:
    job = _job()
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.state = s.HandoffState.RUNNING  # type: ignore[misc]
    with pytest.raises(p.HandoffValidationError):
        _job(task_id="not-a-uuid")
    with pytest.raises(p.HandoffValidationError):
        _job(recipient="Not An Agent")
    with pytest.raises(p.HandoffValidationError):
        _job(updated_at=datetime(2026, 9, 20, 12, 0, 0))


# --------------------------------------------------------------------------
# The happy path, and who may schedule execution
# --------------------------------------------------------------------------


def test_accepted_queued_running_completed_is_the_happy_path() -> None:
    job = s.accept_task(_task(), now=NOW).job
    queued = s.apply(job, s.HandoffTrigger.ENQUEUE, now=LATER)
    assert queued.state is s.HandoffState.QUEUED
    assert queued.schedules_execution is False

    running = s.apply(queued.job, s.HandoffTrigger.START, now=LATER)
    assert running.state is s.HandoffState.RUNNING
    assert running.schedules_execution is True

    done = s.apply(running.job, s.HandoffTrigger.COMPLETE, now=LATER)
    assert done.state is s.HandoffState.COMPLETED
    assert done.job.is_terminal is True
    assert done.schedules_execution is False


def test_start_is_the_only_trigger_that_schedules_execution() -> None:
    for trigger in s.HandoffTrigger:
        if trigger is s.HandoffTrigger.START:
            continue
        for state in s.HandoffState:
            job = _any_job(state)
            try:
                transition = s.apply(job, trigger, now=LATER)
            except s.HandoffStateError:
                continue
            assert transition.schedules_execution is False, trigger


def test_a_running_job_cannot_be_started_twice() -> None:
    running = _job(s.HandoffState.RUNNING)
    with pytest.raises(s.IllegalTransitionError):
        s.apply(running, s.HandoffTrigger.START, now=LATER)


def test_completing_is_only_legal_from_running() -> None:
    for state in (s.HandoffState.ACCEPTED, s.HandoffState.QUEUED, s.HandoffState.BLOCKED):
        with pytest.raises(s.IllegalTransitionError):
            s.apply(_job(state), s.HandoffTrigger.COMPLETE, now=LATER)


# --------------------------------------------------------------------------
# Capacity waits and restart reconciliation
# --------------------------------------------------------------------------


def test_waiting_for_capacity_keeps_the_job_queued_and_never_fails_it() -> None:
    first = s.apply(_job(), s.HandoffTrigger.CAPACITY_WAIT, now=LATER)
    assert first.state is s.HandoffState.QUEUED
    assert first.note == s.CAPACITY_NOTE
    assert first.schedules_execution is False

    again = s.apply(first.job, s.HandoffTrigger.CAPACITY_WAIT, now=LATER)
    assert again.state is s.HandoffState.QUEUED
    assert again.job.attempt == 1


def test_restart_reconciliation_requeues_a_running_job_with_a_note() -> None:
    transition = s.apply(
        _job(s.HandoffState.RUNNING), s.HandoffTrigger.RESTART_RECONCILE, now=LATER
    )
    assert transition.state is s.HandoffState.QUEUED
    assert transition.note == s.RESTART_NOTE
    assert transition.schedules_execution is False
    assert transition.job.attempt == 1


def test_restart_reconciliation_does_not_touch_a_terminal_job() -> None:
    for state in s.TERMINAL_STATES:
        with pytest.raises(s.IllegalTransitionError):
            s.apply(_job(state), s.HandoffTrigger.RESTART_RECONCILE, now=LATER)


# --------------------------------------------------------------------------
# Blocking
# --------------------------------------------------------------------------


def test_a_job_can_block_from_any_active_state_and_unblock_into_the_queue() -> None:
    for state in (s.HandoffState.ACCEPTED, s.HandoffState.QUEUED, s.HandoffState.RUNNING):
        blocked = s.apply(_job(state), s.HandoffTrigger.BLOCK, now=LATER, note="needs edit rights")
        assert blocked.state is s.HandoffState.BLOCKED
        assert blocked.note == "needs edit rights"

        unblocked = s.apply(blocked.job, s.HandoffTrigger.UNBLOCK, now=LATER)
        assert unblocked.state is s.HandoffState.QUEUED
        assert unblocked.schedules_execution is False


def test_unblocking_something_that_is_not_blocked_is_illegal() -> None:
    with pytest.raises(s.IllegalTransitionError):
        s.apply(_job(s.HandoffState.QUEUED), s.HandoffTrigger.UNBLOCK, now=LATER)


# --------------------------------------------------------------------------
# Terminal immutability and safe retry
# --------------------------------------------------------------------------


def test_a_completed_job_is_immutable() -> None:
    done = _job(s.HandoffState.COMPLETED)
    for trigger in s.HandoffTrigger:
        if trigger is s.HandoffTrigger.OBSERVE:
            assert s.apply(done, trigger, now=LATER).job == done
            continue
        with pytest.raises(s.HandoffStateError):
            s.apply(done, trigger, now=LATER)


def test_a_completed_job_can_never_be_retried() -> None:
    done = _job(s.HandoffState.COMPLETED)
    assert done.attempts_remaining > 0
    assert done.can_retry is False
    with pytest.raises(s.RetryNotPermittedError):
        s.apply(done, s.HandoffTrigger.RETRY, now=LATER)


def test_only_a_failed_job_may_be_recorded_as_retryable() -> None:
    for state in s.HandoffState:
        if state is s.HandoffState.FAILED:
            continue
        with pytest.raises(p.HandoffValidationError):
            _job(state, retryable=True)


def test_an_unretryable_failure_stays_failed() -> None:
    failed = s.apply(
        _job(s.HandoffState.RUNNING), s.HandoffTrigger.FAIL, now=LATER, note="no folder"
    )
    assert failed.state is s.HandoffState.FAILED
    assert failed.job.retryable is False
    with pytest.raises(s.RetryNotPermittedError):
        s.safe_retry(failed.job, now=LATER)


def test_a_retryable_failure_requeues_a_new_attempt_without_scheduling_it() -> None:
    failed = s.apply(_job(s.HandoffState.RUNNING), s.HandoffTrigger.FAIL, now=LATER, retryable=True)
    assert failed.job.can_retry is True

    retried = s.safe_retry(failed.job, now=LATER)
    assert retried.state is s.HandoffState.QUEUED
    assert retried.starts_new_attempt is True
    assert retried.schedules_execution is False
    assert retried.job.attempt == 2
    assert retried.job.retryable is False
    assert retried.job.task_id == failed.job.task_id


def test_retry_stops_at_the_attempt_bound() -> None:
    job = _job(s.HandoffState.FAILED, attempt=3, max_attempts=3, retryable=True)
    assert job.attempts_remaining == 0
    assert job.can_retry is False
    with pytest.raises(s.RetryNotPermittedError):
        s.safe_retry(job, now=LATER)


def test_expiry_fails_a_nonterminal_job_without_making_it_retryable() -> None:
    for state in s.NONTERMINAL_STATES:
        transition = s.apply(_job(state), s.HandoffTrigger.EXPIRE, now=LATER)
        assert transition.state is s.HandoffState.FAILED
        assert transition.job.retryable is False
        assert transition.job.can_retry is False


def test_marking_retryable_only_makes_sense_for_a_failure() -> None:
    with pytest.raises(s.HandoffStateError):
        s.apply(_job(), s.HandoffTrigger.BLOCK, now=LATER, retryable=True)


# --------------------------------------------------------------------------
# Determinism of rejection
# --------------------------------------------------------------------------


def test_an_illegal_transition_is_rejected_identically_every_time() -> None:
    job = _job(s.HandoffState.COMPLETED)
    messages = set()
    for _ in range(3):
        with pytest.raises(s.IllegalTransitionError) as caught:
            s.apply(job, s.HandoffTrigger.START, now=LATER)
        messages.add(str(caught.value))
    assert len(messages) == 1
    assert job == _job(s.HandoffState.COMPLETED)


def test_the_same_legal_transition_always_produces_the_same_result() -> None:
    job = _job(s.HandoffState.QUEUED)
    first = s.apply(job, s.HandoffTrigger.START, now=LATER)
    second = s.apply(job, s.HandoffTrigger.START, now=LATER)
    assert first == second


def test_unknown_triggers_and_naive_timestamps_are_refused() -> None:
    with pytest.raises(s.HandoffStateError):
        s.apply(_job(), "start", now=LATER)  # type: ignore[arg-type]
    with pytest.raises(p.HandoffValidationError):
        s.apply(_job(), s.HandoffTrigger.START, now=datetime(2026, 9, 20, 12, 5, 0))


def test_legal_triggers_agree_with_apply() -> None:
    for state in s.HandoffState:
        legal = s.legal_triggers(state)
        for trigger in s.HandoffTrigger:
            assert s.is_legal(state, trigger) == (trigger in legal)
            if trigger in legal:
                s.apply(_any_job(state), trigger, now=LATER)
            else:
                with pytest.raises(s.HandoffStateError):
                    s.apply(_any_job(state), trigger, now=LATER)


# --------------------------------------------------------------------------
# Mirroring received events: never creates or restarts work
# --------------------------------------------------------------------------


def test_a_duplicate_task_event_reuses_the_stored_job_and_schedules_nothing() -> None:
    job = s.accept_event(_task_event(), now=NOW).job
    transition = s.apply_event(job, _task_event())
    assert transition.trigger is s.HandoffTrigger.OBSERVE
    assert transition.job == job
    assert transition.schedules_execution is False


def test_conversational_events_do_not_change_state() -> None:
    job = _job(s.HandoffState.RUNNING)
    conversational: list[tuple[p.HandoffEventKind, Payload]] = [
        (p.HandoffEventKind.ACK, {}),
        (p.HandoffEventKind.QUESTION, {"question": "which folder?"}),
        (p.HandoffEventKind.ANSWER, {"answer": "the leads one"}),
    ]
    for kind, payload in conversational:
        transition = s.apply_event(job, _event(kind, payload=payload))
        assert transition.job == job
        assert transition.trigger is s.HandoffTrigger.OBSERVE


def test_a_mirrored_running_state_never_schedules_local_execution() -> None:
    job = _job(s.HandoffState.ACCEPTED)
    transition = s.apply_event(job, _event(p.HandoffEventKind.STATE, payload={"state": "running"}))
    assert transition.state is s.HandoffState.RUNNING
    assert transition.schedules_execution is False


def test_a_mirrored_state_that_matches_is_an_exact_no_op() -> None:
    job = _job(s.HandoffState.RUNNING)
    transition = s.apply_event(job, _event(p.HandoffEventKind.STATE, payload={"state": "running"}))
    assert transition.job == job
    assert transition.trigger is s.HandoffTrigger.OBSERVE


def test_a_mirrored_state_may_requeue_but_never_revives_a_terminal_job() -> None:
    requeued = s.apply_event(
        _job(s.HandoffState.RUNNING), _event(p.HandoffEventKind.STATE, payload={"state": "queued"})
    )
    assert requeued.state is s.HandoffState.QUEUED

    with pytest.raises(s.IllegalTransitionError):
        s.apply_event(
            _job(s.HandoffState.COMPLETED),
            _event(p.HandoffEventKind.STATE, payload={"state": "running"}),
        )


def test_an_unknown_mirrored_state_name_is_rejected() -> None:
    with pytest.raises(p.HandoffValidationError):
        s.apply_event(_job(), _event(p.HandoffEventKind.STATE, payload={"state": "paused"}))


def test_a_result_event_closes_the_job_from_any_live_state() -> None:
    for state in s.NONTERMINAL_STATES:
        transition = s.apply_event(
            _job(state),
            _event(
                p.HandoffEventKind.RESULT,
                payload={"outcome": "completed", "summary": "1,876 rows in leads"},
            ),
        )
        assert transition.state is s.HandoffState.COMPLETED
        assert transition.schedules_execution is False


def test_a_failed_result_carries_its_retry_guidance() -> None:
    transition = s.apply_event(
        _job(s.HandoffState.RUNNING),
        _event(
            p.HandoffEventKind.RESULT,
            payload={"outcome": "failed", "summary": "the folder was locked", "retryable": True},
        ),
    )
    assert transition.state is s.HandoffState.FAILED
    assert transition.job.retryable is True
    assert transition.job.can_retry is True


def test_a_redelivered_result_is_idempotent_but_a_contradicting_one_is_refused() -> None:
    done = _job(s.HandoffState.COMPLETED)
    result = _event(
        p.HandoffEventKind.RESULT, payload={"outcome": "completed", "summary": "1,876 rows"}
    )
    assert s.apply_event(done, result).job == done

    contradiction = _event(
        p.HandoffEventKind.RESULT, payload={"outcome": "failed", "summary": "it broke"}
    )
    with pytest.raises(s.IllegalTransitionError):
        s.apply_event(done, contradiction)


def test_an_event_for_another_task_or_agent_is_refused() -> None:
    other_task = "11111111-2222-4333-8444-555555555555"
    with pytest.raises(s.IllegalTransitionError):
        s.apply_event(
            _job(),
            p.HandoffEvent(
                event_id=EVENT_ID,
                kind=p.HandoffEventKind.ACK,
                task_id=other_task,
                sender="david",
                recipient="drewai",
                sequence=1,
                created_at=LATER,
            ),
        )
    with pytest.raises(s.IllegalTransitionError):
        s.apply_event(_job(), _event(p.HandoffEventKind.ACK, sender="imac", recipient="drewai"))
