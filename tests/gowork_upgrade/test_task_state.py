"""T05 — durable task attempts and acceptance evidence.

A build's memory of each task lives on disk so a restart, a duplicate result
or a stale worker can never turn "finished" into "accepted" by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_state import (
    BuildState,
    StaleAttemptError,
    TaskStatus,
    open_build_state,
)

FIXTURES = Path(__file__).parent / "fixtures"
API = "product.catalog-api"
PAGE = "website.catalog-page"


@pytest.fixture
def state(tmp_path: Path) -> BuildState:
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    return open_build_state(tmp_path / "build.json", load_plan_tree(source), build_id="thread-11")


def test_every_task_starts_pending_with_its_ownership_and_version(state: BuildState) -> None:
    record = state[API]
    assert record.status is TaskStatus.PENDING
    assert record.attempt == 1
    assert record.attempt_id == "thread-11:product.catalog-api:1"
    assert record.plan_id == "product" and record.plan_version == 2
    assert record.owned_files == ("src/catalog.py", "tests/test_catalog.py")
    assert record.owned_resources == ("catalog-schema",)
    assert not record.accepted


def test_reopen_preserves_state(state: BuildState) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["pytest: 3 passed"])
    state.record_review(API, attempt.attempt_id, verdict="no objection", notes="looks fine")

    again = open_build_state(state.path, state.tree, build_id="thread-11")

    assert again[API].status is TaskStatus.FINISHED
    assert again[API].result_commit == "abc123"
    assert again[API].checks == ("pytest: 3 passed",)
    assert again[API].review == ("no objection: looks fine",)
    assert again.events[-1]["task"] == API


def test_finished_is_not_accepted_until_the_build_says_so(state: BuildState) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["ok"])
    assert state[API].status is TaskStatus.FINISHED and not state[API].accepted
    assert state.accepted_tasks() == ()

    state.accept(API, attempt.attempt_id)

    assert state[API].status is TaskStatus.ACCEPTED and state[API].accepted
    assert state.accepted_tasks() == (API,)


def test_duplicate_result_submission_is_idempotent(state: BuildState) -> None:
    attempt = state.begin(API)
    first = state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["ok"])
    second = state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["ok"])

    assert first == second
    assert sum(1 for e in state.events if e["change"] == "finished") == 1


def test_a_different_result_for_the_same_attempt_is_refused(state: BuildState) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["ok"])
    with pytest.raises(StaleAttemptError, match="already finished"):
        state.submit_result(API, attempt.attempt_id, commit="def456", checks=["ok"])


def test_a_stale_attempt_cannot_overwrite_an_accepted_current_result(state: BuildState) -> None:
    first = state.begin(API)
    state.submit_result(API, first.attempt_id, commit="abc123", checks=["ok"])
    state.block(API, "reviewer sent it back")
    second = state.retry(API)
    assert second.attempt == 2 and second.attempt_id.endswith(":2")
    state.begin(API)
    state.submit_result(API, second.attempt_id, commit="def456", checks=["ok"])
    state.accept(API, second.attempt_id)

    with pytest.raises(StaleAttemptError, match="attempt 1"):
        state.submit_result(API, first.attempt_id, commit="zzz999", checks=["ok"])
    with pytest.raises(StaleAttemptError):
        state.accept(API, first.attempt_id)
    assert state[API].result_commit == "def456" and state[API].accepted


def test_an_attempt_from_an_older_plan_version_cannot_be_accepted(state: BuildState) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["ok"])
    state.note_plan_version("product", 3)  # requirements changed mid-build

    with pytest.raises(StaleAttemptError, match="version 2"):
        state.accept(API, attempt.attempt_id)
    assert state[API].status is TaskStatus.FINISHED
    assert state.retry(API).plan_version == 3


def test_repair_usage_is_counted_per_attempt(state: BuildState) -> None:
    attempt = state.begin(API)
    assert state.record_repair(API, attempt.attempt_id, "tests failed once").repairs == 1
    assert state[API].repairs == 1
    assert state.retry_after_block(API, "gave up").repairs == 0


def test_transitions_are_explicit(state: BuildState) -> None:
    with pytest.raises(StaleAttemptError, match="pending"):
        state.submit_result(API, state[API].attempt_id, commit="x", checks=["ok"])
    state.begin(API)
    with pytest.raises(StaleAttemptError, match="running"):
        state.accept(API, state[API].attempt_id)
    with pytest.raises(KeyError):
        state.begin("no.such.task")


def test_interrupted_writes_leave_recoverable_state(state: BuildState, tmp_path: Path) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["ok"])
    stray = state.path.parent / ".write-abandoned.tmp"
    stray.write_text("{ not finished", encoding="utf-8")

    again = open_build_state(state.path, state.tree, build_id="thread-11")

    assert again[API].result_commit == "abc123"
    assert json.loads(state.path.read_text(encoding="utf-8"))["build_id"] == "thread-11"


def test_opening_with_another_build_id_is_refused(state: BuildState) -> None:
    with pytest.raises(StaleAttemptError, match="thread-11"):
        open_build_state(state.path, state.tree, build_id="thread-12")


def test_worker_threads_are_remembered_until_archived(state: BuildState) -> None:
    """T14: the thread a task ran in outlives the worker so its archive can be retried."""
    attempt = state.begin(API)
    state.note_thread(API, attempt.attempt_id, thread_id=4242)
    assert state[API].thread_id == 4242 and not state[API].archived
    assert state.unarchived_threads() == ()  # still running: nothing to archive yet

    state.submit_result(API, attempt.attempt_id, commit="abc", checks=["ok"])
    assert state.unarchived_threads() == ((API, 4242),)
    state.mark_archived(API)
    state.mark_archived(API)  # idempotent
    assert state[API].archived and state.unarchived_threads() == ()

    again = open_build_state(state.path, state.tree, build_id="thread-11")
    assert again[API].thread_id == 4242 and again[API].archived

    with pytest.raises(StaleAttemptError):
        state.note_thread(API, "thread-11:product.catalog-api:9", thread_id=1)


def test_two_handles_on_one_ledger_never_lose_each_others_writes(state: BuildState) -> None:
    """T14: the cog and the loop each open the ledger; both must see the latest file."""
    other = open_build_state(state.path, state.tree, build_id="thread-11")
    attempt = state.begin(API)
    other.note_thread(API, attempt.attempt_id, thread_id=77)  # written through the other handle
    state.submit_result(API, attempt.attempt_id, commit="abc", checks=["ok"])  # must not drop it
    assert state[API].thread_id == 77 and other[API].status is TaskStatus.FINISHED
    other.mark_archived(API)
    state.accept(API, attempt.attempt_id)
    assert state[API].archived and other[API].accepted
