"""T22 — what the build says is drawn from saved evidence, in plain words.

Every message names the project and goal, says what changed or failed and why
it matters, and reads on its own without the chat. Nothing is invented: a build
with blocked tasks lists issues, never success.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_report import (
    render_blocker_question,
    render_completion,
    render_progress,
)
from claude_code_core.gowork_state import BuildState, open_build_state

FIXTURES = Path(__file__).parent / "fixtures"
API, PAGE, STYLES, POST = (
    "product.catalog-api",
    "website.catalog-page",
    "website.page-styles",
    "marketing.launch-post",
)


@pytest.fixture
def state(tmp_path: Path) -> BuildState:
    source = tmp_path / "master-plan.md"
    text = (FIXTURES / "validated-plan.md").read_text(encoding="utf-8")
    source.write_text("Goal: launch the business with a live catalog\n\n" + text, encoding="utf-8")
    return open_build_state(tmp_path / "build.json", load_plan_tree(source), build_id="thread-1")


def _accept(state: BuildState, task: str, *checks: str) -> None:
    attempt = state.begin(task)
    state.submit_result(task, attempt.attempt_id, commit=f"c-{task}", checks=list(checks) or ["ok"])
    state.accept(task, attempt.attempt_id)


def test_progress_names_project_goal_and_where_things_stand(state: BuildState) -> None:
    _accept(state, API, "acceptance check passed: pytest")
    state.begin(POST)
    text = render_progress(
        state, project="launch-co", goal="launch the business with a live catalog"
    )

    assert text.startswith("**launch-co** — launch the business with a live catalog")
    assert "1 of 4 done" in text
    assert "✅ Publish the checked product catalog contract" in text
    assert "⏳ Write the launch announcement" in text  # running
    assert "Show the checked product catalog on the website" in text  # waiting, named by outcome
    assert "product.catalog-api" not in text.replace("`", "")  # outcomes, not bare ids
    assert "\n" in text and len(text) < 900


def test_completion_bullets_say_what_and_why_with_checks_and_limitations(state: BuildState) -> None:
    for task in (API, STYLES, POST):
        _accept(state, task, "acceptance check passed: pytest -q")
    _accept(state, PAGE, "acceptance check passed: npm test", "review (approve): approved")
    state_page = state[PAGE]
    # a repair and a rework in the history, so the summary must mention the limitations
    doc = state._document  # noqa: SLF001
    doc["tasks"][POST]["lineage_repairs"] = 1
    doc["tasks"][POST]["previous_failure"] = "the tests failed once"
    doc["tasks"][API]["rework_reason"] = "the plan 'product' changed to version 3"
    state._write()  # noqa: SLF001

    text = render_completion(
        state, project="launch-co", goal="launch the business with a live catalog"
    )

    assert text.startswith("**launch-co** — launch the business with a live catalog")
    assert "All 4 tasks are done and checked" in text
    assert (
        "• Publish the checked product catalog contract — delivers The product catalog "
        "contract is published"
    ) in text
    assert "checked by: acceptance check passed: pytest -q" in text
    assert "second AI approved" in text  # the review, in plain words
    assert "Worth knowing" in text
    assert "Write the launch announcement needed one repair (the tests failed once)" in text
    assert "was reworked after the plan changed" in text
    assert state_page.result_commit not in text  # no commit hashes for a non-technical reader


def test_all_blocked_lists_issues_not_success(state: BuildState) -> None:
    state.begin(API)
    state.block(
        API, "combined check failed after merging: acceptance check FAILED: pytest — 2 failed"
    )
    state.begin(POST)
    state.block(POST, "the review sent it back: no date on the post")
    text = render_completion(state, project="launch-co", goal="launch")

    assert "done and checked" not in text and "🏁" not in text
    assert text.count("🛑") >= 1 and "2 tasks are stuck" in text
    assert "Publish the checked product catalog contract — combined check failed" in text
    assert "Write the launch announcement — the review sent it back: no date on the post" in text
    assert "Show the checked product catalog on the website" in text and "waiting" in text


def test_blocker_question_is_one_actionable_decision(state: BuildState) -> None:
    state.begin(API)
    state.block(API, "the review sent it back: the schema lacks a version field")
    text = render_blocker_question(state, API, project="launch-co", goal="launch the business")

    assert text.startswith("❓ **launch-co** — launch the business")
    assert "Publish the checked product catalog contract" in text
    assert "the schema lacks a version field" in text
    assert "Reply to this message" in text and "retry" in text and "skip" in text
    assert text.count("?") >= 1 and len(text) < 700
