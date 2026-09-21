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
STYLES = "website.page-styles"
POST = "marketing.launch-post"


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


def test_an_earlier_attempts_thread_is_still_archived_after_a_repair_rework_or_retry(
    state: BuildState,
) -> None:
    """T14 across T18/T19/T21 (found by the T31 demo): a fresh attempt must not make the
    previous attempt's still-open worker thread disappear from the archive list."""
    attempt = state.begin(API)
    state.note_thread(API, attempt.attempt_id, thread_id=4242)
    state.block(API, "the tests failed")
    state.repair(API)  # a new attempt, before anyone archived thread 4242
    assert state.unarchived_threads() == ((API, 4242),)  # pending task, old thread listed

    second = state.begin(API)
    state.note_thread(API, second.attempt_id, thread_id=4343)
    state.submit_result(API, second.attempt_id, commit="abc", checks=["ok"])
    state.accept(API, second.attempt_id)
    state.note_plan_version("product", 3)
    state.rework(API, "the plan changed")  # thread 4343 was never archived either
    assert set(state.unarchived_threads()) == {(API, 4242), (API, 4343)}

    state.mark_archived(API, thread_id=4242)
    assert state.unarchived_threads() == ((API, 4343),)
    again = open_build_state(state.path, state.tree, build_id="thread-11")
    assert again.unarchived_threads() == ((API, 4343),)  # survives reopen
    again.mark_archived(API, thread_id=4343)
    assert again.unarchived_threads() == ()

    third = again.begin(API)
    again.note_thread(API, third.attempt_id, thread_id=4444)
    again.block(API, "stuck")
    again.retry(API)  # a person's retry: the same rule
    assert again.unarchived_threads() == ((API, 4444),)
    again.mark_archived(API, thread_id=4444)
    assert again.unarchived_threads() == ()


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


def test_the_repair_budget_follows_the_task_across_attempts(state: BuildState) -> None:
    """T18: one automatic repair per task, whatever the attempt number or restart."""
    first = state.begin(API)
    state.block(API, "the tests failed")
    assert state.repairs_left(API) == 1
    repaired = state.repair(API)  # the one automatic repair
    assert repaired.attempt == 2 and repaired.status is TaskStatus.PENDING
    assert repaired.previous_failure == "the tests failed"
    assert repaired.lineage_repairs == 1 and state.repairs_left(API) == 0
    again = open_build_state(state.path, state.tree, build_id="thread-11")
    assert again.repairs_left(API) == 0  # survives reopen

    state.begin(API)
    state.block(API, "failed again")
    with pytest.raises(StaleAttemptError, match="repair"):
        state.repair(API)
    manual = state.retry(API)  # a person may always ask for another try
    assert manual.attempt == 3 and manual.lineage_repairs == 1
    assert manual.previous_failure == "failed again"
    with pytest.raises(StaleAttemptError):
        state.repair(PAGE)  # nothing failed there
    assert first.attempt_id != repaired.attempt_id != manual.attempt_id


def _bump(plan: Path, plan_id: str, old: int, new: int) -> None:
    text = plan.read_text(encoding="utf-8")
    text = text.replace(
        f'"id": "{plan_id}", "version": {old}', f'"id": "{plan_id}", "version": {new}'
    )
    text = text.replace(
        f'"plan_id": "{plan_id}",\n      "plan_version": {old}',
        f'"plan_id": "{plan_id}",\n      "plan_version": {new}',
    )
    plan.write_text(text, encoding="utf-8")


def test_a_plan_edit_is_saved_at_once_and_stale_acceptances_become_rework(
    state: BuildState, tmp_path: Path
) -> None:
    """T19: the new version is recorded immediately; work accepted at the old version is
    reworked (not repaired), its result kept; unrelated plans are untouched."""
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc", checks=["ok"])
    state.accept(API, attempt.attempt_id)
    state.begin(POST)
    state.block(POST, "failed")
    state.repair(POST)  # POST has used its repair; a rework must not give it back

    plan = tmp_path / "master-plan.md"
    _bump(plan, "product", 2, 3)
    _bump(plan, "marketing", 1, 2)
    report = state.sync_tree(load_plan_tree(plan))

    assert report.changed_plans == {"product": 3, "marketing": 2}
    assert set(report.reworked_tasks) == {API}  # accepted at v2, now stale
    assert state.plan_version("product") == 3
    api = state[API]
    assert api.status is TaskStatus.PENDING and api.attempt == 2 and api.plan_version == 3
    assert api.rework_reason and "version 3" in api.rework_reason
    assert api.previous_commit == "abc" and api.previous_failure is None
    assert api.lineage_repairs == 0 and state.repairs_left(API) == 1  # rework is not repair
    assert state[POST].lineage_repairs == 1 and state[POST].plan_version == 2  # pending, moved on
    assert state[PAGE].status is TaskStatus.PENDING and state[STYLES].plan_version == 4
    again = open_build_state(state.path, load_plan_tree(plan), build_id="thread-11")
    assert again.plan_version("product") == 3 and again[API].attempt == 2


def test_a_finished_result_at_an_old_version_cannot_be_accepted_but_is_kept(
    state: BuildState, tmp_path: Path
) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc", checks=["ok"])
    plan = tmp_path / "master-plan.md"
    _bump(plan, "product", 2, 3)
    state.sync_tree(load_plan_tree(plan))

    with pytest.raises(StaleAttemptError):  # the finished attempt is stale: never accepted
        state.accept(API, attempt.attempt_id)
    reworked = state[API]  # sync already opened the rework attempt
    assert reworked.attempt == 2 and reworked.previous_commit == "abc"
    assert reworked.rework_reason and "version 3" in reworked.rework_reason
    assert any(e.get("change") == "finished" and e.get("commit") == "abc" for e in state.events)


def test_new_tasks_in_an_edited_plan_appear_pending(state: BuildState, tmp_path: Path) -> None:
    plan = tmp_path / "master-plan.md"
    text = plan.read_text(encoding="utf-8").replace(
        '"tasks": [',
        '"tasks": [\n    {"id": "marketing.press-kit", "plan_id": "marketing", "plan_version": 1, '
        '"outcome": "Assemble the press kit", "dependencies": [], "owned_files": ["kit/"], '
        '"owned_resources": [], "required_inputs": ["REQ-LAUNCH-POST"], "output": "a kit", '
        '"acceptance_check": "true", "source_requirement": "REQ-LAUNCH-POST"},',
        1,
    )
    plan.write_text(text, encoding="utf-8")
    report = state.sync_tree(load_plan_tree(plan))
    assert report.added_tasks == ("marketing.press-kit",)
    assert state["marketing.press-kit"].status is TaskStatus.PENDING
    assert "marketing.press-kit" in [r.task_id for r in state.records]
