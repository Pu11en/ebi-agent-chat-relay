"""The run state is the ledger that makes a restart boring.

Everything else in a parallel build is recoverable guesswork unless one record
answers, exactly once, "did this task already start?". These tests pin the three
claims the rest of the build leans on.

*Intent is written before the side effect.* A spawn is recorded as `spawning`
with a correlation identity before Discord or the relay is touched, so a process
that dies mid-request leaves a trace rather than a silence.

*Uncertainty never becomes a duplicate.* Reopening the ledger turns every
in-flight spawn into `ambiguous`, which the scheduler counts as active. Only a
person or a reconciliation that proves absence can return that task to the
dispatchable set, and when it does, the correlation identity changes so the
retry is distinguishable from the original.

*Evidence outlives the worktree.* A verified commit and its check output survive
a restart unchanged, and the task is not rebuilt.

Invalid transitions raise instead of repairing themselves: a ledger that quietly
accepts a nonsense move is worse than no ledger at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extensions.feature_workflow.run_state import (
    RUN_STATE_VERSION,
    RunState,
    RunStateError,
    correlation_id,
    open_run_state,
)
from extensions.feature_workflow.scheduler import ACTIVE_STATUSES, TaskStatus, compute_ready_set
from extensions.feature_workflow.task_graph import TaskGraph, parse_task_graph

HEADER = "Check: `uv run pytest -q`\n\n## 1. Wave\n\n"

PLAN = HEADER + (
    "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** Contracts.\n"
    "- [ ] 1.2 **Depends on: none; owns: `pkg/b.py`.** Sibling.\n"
    "- [ ] 1.3 **Depends on: 1.1; owns: `pkg/c.py`.** Dependent.\n"
)

OTHER_PLAN = PLAN + "- [ ] 1.4 **Depends on: none; owns: `pkg/d.py`.** Late addition.\n"

DIGEST = "a" * 64
FOUNDATION = "0" * 40
COMMIT = "1" * 40
OTHER_COMMIT = "2" * 40
MERGE = "3" * 40
EVIDENCE = ("uv run pytest tests/test_a.py -q → 12 passed",)


def graph(source: str = PLAN) -> TaskGraph:
    return parse_task_graph(source)


def state_path(tmp_path: Path) -> Path:
    return tmp_path / "run" / "state.json"


def opened(tmp_path: Path, source: str = PLAN, digest: str = DIGEST) -> RunState:
    """Open the ledger the way a coordinator tick would: create or recover."""
    return open_run_state(state_path(tmp_path), graph=graph(source), approval_digest=digest)


def dispatch(state: RunState, task_id: str = "1.1", thread_id: str = "99001") -> None:
    state.begin_spawn(task_id, foundation=FOUNDATION)
    state.record_dispatch(task_id, thread_id, branch=f"session/{thread_id}")


def verify(state: RunState, task_id: str = "1.1", thread_id: str = "99001") -> None:
    dispatch(state, task_id, thread_id)
    state.record_verified(task_id, commit=COMMIT, evidence=EVIDENCE, worktree=f"/w/wt-{thread_id}")


class TestCorrelationIdentity:
    def test_identity_is_stable_for_the_same_run_task_and_attempt(self) -> None:
        first = correlation_id("run-a", "rev123:1.1", 1)

        assert first == correlation_id("run-a", "rev123:1.1", 1)
        assert first != correlation_id("run-a", "rev123:1.2", 1)
        assert first != correlation_id("run-a", "rev123:1.1", 2)
        assert first != correlation_id("run-b", "rev123:1.1", 1)

    def test_intent_records_the_identity_before_any_side_effect(self, tmp_path: Path) -> None:
        state = opened(tmp_path)

        record = state.begin_spawn("1.1", foundation=FOUNDATION)

        assert record.status is TaskStatus.SPAWNING
        assert record.attempt == 1
        assert record.correlation_id == correlation_id(state.run_id, graph()["1.1"].uid, 1)
        assert record.thread_id is None
        assert record.foundation == FOUNDATION

    def test_identity_survives_every_later_transition(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        intended = state.begin_spawn("1.1", foundation=FOUNDATION).correlation_id

        state.record_dispatch("1.1", "99001")
        state.record_verified("1.1", commit=COMMIT, evidence=EVIDENCE)
        record = state.record_integrated("1.1", MERGE)

        assert record.correlation_id == intended
        assert record.attempt == 1

    def test_two_tasks_never_share_an_identity(self, tmp_path: Path) -> None:
        state = opened(tmp_path)

        first = state.begin_spawn("1.1", foundation=FOUNDATION)
        second = state.begin_spawn("1.2", foundation=FOUNDATION)

        assert first.correlation_id != second.correlation_id


class TestTransitions:
    def test_full_path_from_pending_to_integrated(self, tmp_path: Path) -> None:
        state = opened(tmp_path)

        assert state["1.1"].status is TaskStatus.PENDING
        state.begin_spawn("1.1", foundation=FOUNDATION)
        state.mark_queued("1.1", "relay is at capacity")
        assert state["1.1"].status is TaskStatus.QUEUED
        state.record_dispatch("1.1", "99001", branch="session/99001")
        assert state["1.1"].status is TaskStatus.DISPATCHED
        state.record_verified("1.1", commit=COMMIT, evidence=EVIDENCE)
        assert state["1.1"].status is TaskStatus.VERIFIED
        record = state.record_integrated("1.1", MERGE)

        assert record.status is TaskStatus.INTEGRATED
        assert record.integration_commit == MERGE

    @pytest.mark.parametrize(
        "action",
        [
            lambda s: s.record_dispatch("1.1", "99001"),
            lambda s: s.record_verified("1.1", commit=COMMIT, evidence=EVIDENCE),
            lambda s: s.record_integrated("1.1", MERGE),
            lambda s: s.mark_queued("1.1", "relay busy"),
            lambda s: s.mark_ambiguous("1.1", "no response"),
        ],
    )
    def test_a_pending_task_cannot_skip_ahead(self, tmp_path: Path, action) -> None:
        state = opened(tmp_path)

        with pytest.raises(RunStateError, match="1.1"):
            action(state)

        assert state["1.1"].status is TaskStatus.PENDING

    def test_dispatched_cannot_skip_verification(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        dispatch(state)

        with pytest.raises(RunStateError, match="dispatched"):
            state.record_integrated("1.1", MERGE)

    def test_integrated_is_terminal(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        verify(state)
        state.record_integrated("1.1", MERGE)

        with pytest.raises(RunStateError, match="integrated"):
            state.begin_spawn("1.1", foundation=FOUNDATION)
        with pytest.raises(RunStateError, match="integrated"):
            state.mark_blocked("1.1", "changed my mind")

    def test_unknown_task_is_refused(self, tmp_path: Path) -> None:
        state = opened(tmp_path)

        with pytest.raises(RunStateError, match="Unknown task"):
            state.begin_spawn("9.9", foundation=FOUNDATION)

    def test_blocking_and_unblocking_starts_a_distinguishable_attempt(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        dispatch(state)
        first = state["1.1"].correlation_id

        blocked = state.mark_blocked("1.1", "worker changed an unowned path")
        assert blocked.status is TaskStatus.BLOCKED
        assert blocked.reason == "worker changed an unowned path"

        retried = state.unblock("1.1")
        assert retried.status is TaskStatus.PENDING
        assert retried.attempt == 2
        assert retried.thread_id is None

        respawned = state.begin_spawn("1.1", foundation=FOUNDATION)
        assert respawned.correlation_id != first

    def test_repeating_a_transition_with_the_same_facts_is_accepted(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        state.begin_spawn("1.1", foundation=FOUNDATION)

        first = state.record_dispatch("1.1", "99001", branch="session/99001")
        again = state.record_dispatch("1.1", "99001", branch="session/99001")

        assert again == first

    def test_repeating_a_transition_with_different_facts_is_refused(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        dispatch(state)

        with pytest.raises(RunStateError, match="99002"):
            state.record_dispatch("1.1", "99002")

        assert state["1.1"].thread_id == "99001"

    def test_verification_requires_a_commit_and_check_evidence(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        dispatch(state)

        with pytest.raises(RunStateError, match="evidence"):
            state.record_verified("1.1", commit=COMMIT, evidence=())
        with pytest.raises(RunStateError, match="commit"):
            state.record_verified("1.1", commit="   ", evidence=EVIDENCE)

        assert state["1.1"].status is TaskStatus.DISPATCHED

    def test_dispatch_requires_a_thread_identity(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        state.begin_spawn("1.1", foundation=FOUNDATION)

        with pytest.raises(RunStateError, match="thread"):
            state.record_dispatch("1.1", "  ")

    def test_archival_follows_integration_only(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        verify(state)

        with pytest.raises(RunStateError, match="integrated"):
            state.mark_archived("1.1")

        state.record_integrated("1.1", MERGE)
        assert state.mark_archived("1.1").archived is True
        assert state.mark_archived("1.1").archived is True


class TestDurability:
    def test_every_transition_is_visible_to_a_fresh_reader(self, tmp_path: Path) -> None:
        verify(opened(tmp_path))

        reopened = opened(tmp_path)

        assert reopened["1.1"].status is TaskStatus.VERIFIED
        assert reopened["1.1"].commit == COMMIT
        assert reopened["1.1"].evidence == EVIDENCE
        assert reopened["1.1"].thread_id == "99001"
        assert reopened["1.1"].foundation == FOUNDATION

    def test_the_file_is_replaced_atomically_and_leaves_nothing_behind(
        self, tmp_path: Path
    ) -> None:
        state = opened(tmp_path)
        dispatch(state)

        path = state_path(tmp_path)
        document = json.loads(path.read_text())

        assert document["version"] == RUN_STATE_VERSION
        assert document["approval_digest"] == DIGEST
        assert [
            entry.name for entry in path.parent.iterdir() if entry.name.startswith(".write-")
        ] == []

    def test_a_refused_transition_writes_nothing(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        dispatch(state)
        before = state_path(tmp_path).read_text()

        with pytest.raises(RunStateError):
            state.record_integrated("1.1", MERGE)

        assert state_path(tmp_path).read_text() == before

    def test_the_event_log_keeps_superseded_evidence(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        verify(state)
        state.mark_blocked("1.1", "combined check failed")
        state.unblock("1.1")

        assert state["1.1"].commit is None
        recorded = [event for event in opened(tmp_path).events if event["to"] == "verified"]
        assert recorded and recorded[0]["detail"]["commit"] == COMMIT

    def test_a_changed_plan_or_approval_is_refused_rather_than_merged(self, tmp_path: Path) -> None:
        dispatch(opened(tmp_path))

        with pytest.raises(RunStateError, match="revision"):
            opened(tmp_path, source=OTHER_PLAN)
        with pytest.raises(RunStateError, match="approval"):
            opened(tmp_path, digest="b" * 64)

    def test_statuses_feed_the_scheduler_directly(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        verify(state)
        state.record_integrated("1.1", MERGE)
        dispatch(state, "1.2", "99002")

        ready = compute_ready_set(graph(), state.statuses())

        assert ready.ready_ids == ("1.3",)
        assert ready.active_ids == ("1.2",)
        assert ready.integrated == ("1.1",)


class TestRestartSafety:
    def test_reopening_turns_an_in_flight_spawn_into_ambiguous(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        intended = state.begin_spawn("1.1", foundation=FOUNDATION).correlation_id

        recovered = opened(tmp_path)

        assert recovered["1.1"].status is TaskStatus.AMBIGUOUS
        assert recovered["1.1"].correlation_id == intended
        assert recovered["1.1"].attempt == 1
        assert TaskStatus.AMBIGUOUS in ACTIVE_STATUSES

    def test_an_ambiguous_task_is_never_dispatched_again_automatically(
        self, tmp_path: Path
    ) -> None:
        state = opened(tmp_path)
        state.begin_spawn("1.1", foundation=FOUNDATION)
        recovered = opened(tmp_path)

        assert "1.1" not in compute_ready_set(graph(), recovered.statuses()).ready_ids
        with pytest.raises(RunStateError, match="ambiguous"):
            recovered.begin_spawn("1.1", foundation=FOUNDATION)

    def test_reconciliation_adopts_the_thread_the_spawn_actually_created(
        self, tmp_path: Path
    ) -> None:
        state = opened(tmp_path)
        state.begin_spawn("1.1", foundation=FOUNDATION)
        recovered = opened(tmp_path)

        adopted = recovered.reconcile_found("1.1", "99001", branch="session/99001")

        assert adopted.status is TaskStatus.DISPATCHED
        assert adopted.attempt == 1
        assert adopted.thread_id == "99001"
        assert adopted.correlation_id == state["1.1"].correlation_id

    def test_proven_absence_returns_the_task_with_a_new_identity(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        first = state.begin_spawn("1.1", foundation=FOUNDATION).correlation_id
        recovered = opened(tmp_path)

        returned = recovered.reconcile_absent("1.1", "no thread carries this correlation ID")

        assert returned.status is TaskStatus.PENDING
        assert returned.attempt == 2
        assert recovered.begin_spawn("1.1", foundation=FOUNDATION).correlation_id != first

    def test_reconciling_absence_requires_a_stated_reason(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        state.begin_spawn("1.1", foundation=FOUNDATION)
        recovered = opened(tmp_path)

        with pytest.raises(RunStateError, match="reason"):
            recovered.reconcile_absent("1.1", "   ")

        assert recovered["1.1"].status is TaskStatus.AMBIGUOUS

    def test_a_verified_result_is_never_rebuilt_after_a_restart(self, tmp_path: Path) -> None:
        verify(opened(tmp_path))

        recovered = opened(tmp_path)

        assert recovered["1.1"].status is TaskStatus.VERIFIED
        assert "1.1" not in compute_ready_set(graph(), recovered.statuses()).ready_ids
        with pytest.raises(RunStateError, match="verified"):
            recovered.begin_spawn("1.1", foundation=FOUNDATION)

    def test_a_missing_worktree_does_not_lose_the_durable_git_evidence(
        self, tmp_path: Path
    ) -> None:
        state = opened(tmp_path)
        verify(state)

        recovered = opened(tmp_path)

        assert recovered["1.1"].branch == "session/99001"
        assert recovered["1.1"].commit == COMMIT
        assert not Path(recovered["1.1"].worktree or "/w/wt-99001").exists()

    def test_recovery_leaves_settled_tasks_untouched(self, tmp_path: Path) -> None:
        state = opened(tmp_path)
        verify(state)
        state.record_integrated("1.1", MERGE)
        dispatch(state, "1.2", "99002")

        recovered = opened(tmp_path)

        assert recovered["1.1"].status is TaskStatus.INTEGRATED
        assert recovered["1.2"].status is TaskStatus.DISPATCHED
        assert recovered["1.3"].status is TaskStatus.PENDING
