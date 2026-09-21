"""The shared recovery coordinator: one logical turn, at most one accepted answer.

Backends are stubs that return canned attempt results; time and sleep are
injected. The SQLite repository is real so the durability claims are tested
against the same conditional updates production uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from claude_code_core.capacity_policy import (
    FallbackTarget,
    RecoveryPhase,
    RecoveryPolicy,
    RecoveryStatus,
)
from claude_discord.capacity_recovery import (
    AttemptResult,
    AttemptTarget,
    CapacityRecoveryCoordinator,
    CapacityRestartLoader,
    TurnSubmission,
)
from claude_discord.database.capacity_recovery_repo import (
    CapacityPendingTurn,
    CapacityRecoveryRepository,
    PendingTurnState,
)
from claude_discord.database.models import init_db

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
POLICY = RecoveryPolicy(
    min_delay_seconds=1, base_delay_seconds=10, max_delay_seconds=100, jitter_ratio=0.0
)


@pytest.fixture
async def repo() -> AsyncIterator[CapacityRecoveryRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield CapacityRecoveryRepository(path)
    finally:
        os.unlink(path)


class Clock:
    def __init__(self, *, hold: bool = False) -> None:
        self.now = NOW
        self.slept: list[float] = []
        #: When set, sleeps never return — the "bot restarted mid-wait" shape.
        self.hold = hold

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if self.hold:
            await asyncio.Event().wait()
        self.now = self.now + timedelta(seconds=seconds)


async def interrupted_mid_wait(repo: CapacityRecoveryRepository, clock: Clock, *keys: str) -> None:
    """Run each turn until its first scheduled retry, then kill it like a restart."""
    for key in keys:
        attempt, _calls = scripted(SATURATED)
        task = asyncio.create_task(coordinator(repo, clock).run_turn(submission(key), attempt))
        while not clock.slept:
            await asyncio.sleep(0)
        clock.slept.clear()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def submission(turn_key: str = "turn-1", **overrides: object) -> TurnSubmission:
    fields: dict[str, object] = dict(
        turn_key=turn_key,
        frontend="discord",
        thread_id=42,
        session_id="abc",
        prompt="Write the release notes",
        backend="claude",
        model="opus",
    )
    fields.update(overrides)
    return TurnSubmission(**fields)  # type: ignore[arg-type]


def coordinator(
    repo: CapacityRecoveryRepository | None, clock: Clock, policy: RecoveryPolicy = POLICY
) -> CapacityRecoveryCoordinator:
    return CapacityRecoveryCoordinator(
        policy=policy, store=repo, sleep=clock.sleep, now=clock, rng=lambda: 0.0
    )


def scripted(*results: AttemptResult):
    calls: list[AttemptTarget] = []
    queue = list(results)

    async def attempt(target: AttemptTarget) -> AttemptResult:
        calls.append(target)
        return queue.pop(0)

    return attempt, calls


SATURATED = AttemptResult(error="API Error: 529 overloaded_error: model is at capacity")
ANSWER = AttemptResult(text="Here are the notes.")


async def test_a_saturated_attempt_waits_then_the_next_answer_is_accepted(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    attempt, calls = scripted(SATURATED, ANSWER)
    seen: list[RecoveryStatus] = []

    async def on_status(status: RecoveryStatus) -> None:
        seen.append(status)

    result = await coordinator(repo, clock).run_turn(submission(), attempt, on_status=on_status)

    assert result.kind == "accepted"
    assert result.attempts == 2
    assert result.result is ANSWER
    assert [target.attempt for target in calls] == [1, 2]
    assert clock.slept == [10.0]
    assert [status.phase for status in seen] == [RecoveryPhase.RETRYING, RecoveryPhase.ACCEPTED]
    assert "attempt 1 of" in seen[0].line
    stored = await repo.get("turn-1")
    assert stored is not None
    assert stored.state is PendingTurnState.ACCEPTED
    assert stored.attempt == 2


async def test_a_second_worker_on_the_same_turn_makes_no_model_call(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    release = asyncio.Event()
    calls: list[str] = []

    async def slow(target: AttemptTarget) -> AttemptResult:
        calls.append("slow")
        await release.wait()
        return ANSWER

    async def other(target: AttemptTarget) -> AttemptResult:
        calls.append("other")
        return ANSWER

    first = asyncio.create_task(coordinator(repo, clock).run_turn(submission(), slow))
    while not calls:
        await asyncio.sleep(0)
    second = await coordinator(repo, clock).run_turn(submission(), other)
    release.set()
    first_result = await first

    assert second.kind == "duplicate"
    assert first_result.kind == "accepted"
    assert calls == ["slow"]


async def test_a_late_result_after_another_acceptance_is_not_delivered(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    release = asyncio.Event()

    async def slow(target: AttemptTarget) -> AttemptResult:
        await release.wait()
        return AttemptResult(text="late original answer")

    task = asyncio.create_task(coordinator(repo, clock).run_turn(submission(), slow))
    while (row := await repo.get("turn-1")) is None or row.claim_token is None:
        await asyncio.sleep(0)
    accepted = await repo.accept_once("turn-1", accepted_result_ref="retry:answer", now=NOW)
    assert accepted is not None
    release.set()
    result = await task

    assert result.kind == "duplicate"
    stored = await repo.get("turn-1")
    assert stored is not None and stored.accepted_result_ref == "retry:answer"


@pytest.mark.parametrize(
    ("error", "phase"),
    [
        ("You've hit your usage limit. Resets at 3pm", RecoveryPhase.QUOTA),
        ("Not logged in. Please run /login", RecoveryPhase.AUTHENTICATION),
        ("Error: ENOENT no such file", RecoveryPhase.PERMANENT),
    ],
)
async def test_non_recoverable_outcomes_stop_with_a_next_action(
    repo: CapacityRecoveryRepository, error: str, phase: RecoveryPhase
) -> None:
    clock = Clock()
    attempt, calls = scripted(AttemptResult(error=error))
    seen: list[RecoveryStatus] = []

    async def on_status(status: RecoveryStatus) -> None:
        seen.append(status)

    result = await coordinator(repo, clock).run_turn(submission(), attempt, on_status=on_status)

    assert result.kind == "needs_user"
    assert len(calls) == 1
    assert clock.slept == []
    assert seen[-1].phase is phase
    assert "Next:" in seen[-1].line


async def test_an_exhausted_budget_keeps_the_record_but_stops_trying(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    policy = RecoveryPolicy(
        min_delay_seconds=1, base_delay_seconds=10, jitter_ratio=0.0, max_attempts=3
    )
    attempt, calls = scripted(SATURATED, SATURATED, SATURATED, ANSWER)

    result = await coordinator(repo, clock, policy).run_turn(submission(), attempt)

    assert result.kind == "exhausted"
    assert result.attempts == 3
    assert len(calls) == 3
    assert result.status is not None and result.status.phase is RecoveryPhase.EXHAUSTED
    stored = await repo.get("turn-1")
    assert stored is not None
    assert stored.state is PendingTurnState.SCHEDULED
    assert stored.accepted_at is None
    assert stored.prompt_ref == "Write the release notes"
    assert await repo.reload_due(now=clock.now) == []


async def test_the_authorized_fallback_is_announced_before_the_next_attempt(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    policy = RecoveryPolicy(min_delay_seconds=1, jitter_ratio=0.0, fallback_after_attempts=1)
    chain = (FallbackTarget("codex", "gpt-5.5", authority="task"),)
    attempt, calls = scripted(SATURATED, ANSWER)
    order: list[str] = []

    async def on_status(status: RecoveryStatus) -> None:
        order.append(f"status:{status.phase.value}")

    async def on_switch(target: FallbackTarget) -> None:
        order.append(f"switch:{target.label}")

    result = await coordinator(repo, clock, policy).run_turn(
        submission(fallback_chain=chain), attempt, on_status=on_status, on_switch=on_switch
    )

    assert result.kind == "accepted"
    assert (calls[0].backend, calls[0].model) == ("claude", "opus")
    assert (calls[1].backend, calls[1].model) == ("codex", "gpt-5.5")
    assert order[:2] == ["status:fallback", "switch:codex · gpt-5.5"]
    assert clock.slept == []


async def test_no_chain_means_the_turn_never_leaves_its_backend(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    policy = RecoveryPolicy(min_delay_seconds=1, jitter_ratio=0.0, fallback_after_attempts=1)
    attempt, calls = scripted(SATURATED, SATURATED, ANSWER)

    result = await coordinator(repo, clock, policy).run_turn(submission(), attempt)

    assert result.kind == "accepted"
    assert {(c.backend, c.model) for c in calls} == {("claude", "opus")}


async def test_partial_output_before_a_capacity_error_pauses_for_the_user(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    attempt, calls = scripted(AttemptResult(error="model is at capacity", delivered=True), ANSWER)

    result = await coordinator(repo, clock).run_turn(submission(), attempt)

    assert result.kind == "ambiguous"
    assert len(calls) == 1
    assert result.status is not None and "already" in result.status.line


async def test_disabled_scheduling_stops_after_the_first_attempt(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    attempt, calls = scripted(SATURATED, ANSWER)
    policy = RecoveryPolicy(enabled=False)

    result = await coordinator(repo, clock, policy).run_turn(submission(), attempt)

    assert result.kind == "exhausted"
    assert len(calls) == 1


async def test_without_a_store_the_duplicate_guard_is_in_memory() -> None:
    clock = Clock()
    coord = coordinator(None, clock)
    attempt, calls = scripted(SATURATED, ANSWER, ANSWER)

    first = await coord.run_turn(submission(), attempt)
    again = await coord.run_turn(submission(), attempt)

    assert first.kind == "accepted"
    assert again.kind == "duplicate"
    assert len(calls) == 2


async def test_live_recovery_state_is_exposed_without_the_prompt(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock()
    coord = coordinator(repo, clock)
    snapshots: list[dict[str, object]] = []

    async def attempt(target: AttemptTarget) -> AttemptResult:
        snapshots.append(dict(coord.snapshot()))
        return SATURATED if target.attempt == 1 else ANSWER

    await coord.run_turn(submission(), attempt)

    assert snapshots[0] == {}
    view = snapshots[1]["turn-1"]
    assert isinstance(view, dict)
    assert view["phase"] == "retrying"
    assert view["thread_id"] == 42
    assert "prompt" not in view and "release notes" not in repr(view)
    assert coord.snapshot() == {}


# ---------------------------------------------------------------------------
# Restart loader (task 2.3)
# ---------------------------------------------------------------------------


async def test_two_restart_loaders_resume_each_due_turn_exactly_once(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock(hold=True)
    await interrupted_mid_wait(repo, clock, "a", "b")
    clock.hold = False
    clock.now = NOW + timedelta(minutes=5)
    assert [t.turn_key for t in await repo.reload_due(now=clock.now)] == ["a", "b"]
    resumed: list[tuple[str, CapacityPendingTurn]] = []

    def loader(name: str) -> CapacityRestartLoader:
        async def resume(turn: CapacityPendingTurn) -> None:
            resumed.append((name, turn))

        return CapacityRestartLoader(repo, resume, now=clock)

    await asyncio.gather(loader("one").load_due(), loader("two").load_due())

    assert sorted(turn.turn_key for _, turn in resumed) == ["a", "b"]
    assert all(turn.state is PendingTurnState.RUNNING for _, turn in resumed)
    assert all(turn.claim_token for _, turn in resumed)


async def test_a_resumed_turn_continues_under_the_loader_claim(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock(hold=True)
    await interrupted_mid_wait(repo, clock, "turn-1")
    clock.hold = False
    clock.now = NOW + timedelta(minutes=5)
    claimed: list[CapacityPendingTurn] = []

    async def resume(turn: CapacityPendingTurn) -> None:
        claimed.append(turn)

    await CapacityRestartLoader(repo, resume, now=clock).load_due()
    assert len(claimed) == 1
    turn = claimed[0]
    attempt2, calls2 = scripted(SATURATED, ANSWER)

    result = await coordinator(repo, clock).run_turn(
        TurnSubmission.from_pending(turn), attempt2, claim_token=turn.claim_token
    )

    assert result.kind == "accepted"
    assert [c.attempt for c in calls2] == [2, 3]
    stored = await repo.get("turn-1")
    assert stored is not None and stored.state is PendingTurnState.ACCEPTED


async def test_the_loader_does_not_resume_expired_or_future_turns(
    repo: CapacityRecoveryRepository,
) -> None:
    clock = Clock(hold=True)
    await interrupted_mid_wait(repo, clock, "future")
    clock.hold = False
    resumed: list[str] = []

    async def resume(turn: CapacityPendingTurn) -> None:
        resumed.append(turn.turn_key)

    await CapacityRestartLoader(repo, resume, now=clock).load_due()
    assert resumed == []
    clock.now = NOW + timedelta(days=2)
    await CapacityRestartLoader(repo, resume, now=clock).load_due()
    assert resumed == []
    stored = await repo.get("future")
    assert stored is not None and stored.state is PendingTurnState.EXPIRED
