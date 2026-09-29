"""Archive authority follows accepted results, never just a settled status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_state import BuildState, open_build_state

TASK = "product.catalog-api"


@pytest.fixture
def state(tmp_path: Path) -> BuildState:
    plan = Path(__file__).parent / "fixtures" / "validated-plan.md"
    return open_build_state(tmp_path / "build.json", load_plan_tree(plan), build_id="archive-test")


@pytest.mark.parametrize("status", ["blocked", "finished", "repairing"])
def test_unaccepted_attempt_is_never_an_archive_candidate(state: BuildState, status: str) -> None:
    attempt = state.begin(TASK)
    state.note_thread(TASK, attempt.attempt_id, thread_id=123)
    if status == "finished":
        state.submit_result(TASK, attempt.attempt_id, commit="abc", checks=["passed"])
    else:
        state.block(TASK, "not integrated")
        if status == "repairing":
            state.repair(TASK)
    assert state.unarchived_threads() == ()


def test_accepted_earlier_attempt_keeps_its_archive_authority_across_reopen(
    state: BuildState,
) -> None:
    attempt = state.begin(TASK)
    state.note_thread(TASK, attempt.attempt_id, thread_id=123)
    state.submit_result(TASK, attempt.attempt_id, commit="abc", checks=["combined check passed"])
    state.accept(TASK, attempt.attempt_id)
    state.rework(TASK, "new requirement")
    again = open_build_state(state.path, state.tree, build_id="archive-test")
    assert again.unarchived_threads() == ((TASK, 123),)
    again.mark_archived(TASK, thread_id=123)
    assert again.unarchived_threads() == ()


def test_unknown_legacy_earlier_thread_is_preserved_without_close_authority(
    state: BuildState,
) -> None:
    document = json.loads(state.path.read_text())
    document["tasks"][TASK]["earlier_threads"] = [123]
    state.path.write_text(json.dumps(document))
    assert state[TASK].earlier_threads == (123,)
    assert state.unarchived_threads() == ()


@pytest.mark.parametrize("missing", ["commit", "checks"])
def test_incomplete_acceptance_evidence_does_not_authorize_archive(
    state: BuildState, missing: str
) -> None:
    attempt = state.begin(TASK)
    state.note_thread(TASK, attempt.attempt_id, thread_id=123)
    state.submit_result(TASK, attempt.attempt_id, commit="abc", checks=["combined check passed"])
    state.accept(TASK, attempt.attempt_id)
    document = json.loads(state.path.read_text())
    key, value = ("result_commit", None) if missing == "commit" else ("checks", [])
    document["tasks"][TASK][key] = value
    state.path.write_text(json.dumps(document))
    assert state.unarchived_threads() == ()
