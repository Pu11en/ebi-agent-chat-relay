"""T06 — select ready tasks across child plans, deterministically.

Only an *accepted, current-version* prerequisite releases dependent work, and
shared ownership keeps overlapping tasks from running at the same time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_schedule import ReadyTask, ready_tasks
from claude_code_core.gowork_state import BuildState, open_build_state

FIXTURES = Path(__file__).parent / "fixtures"
API = "product.catalog-api"
PAGE = "website.catalog-page"
STYLES = "website.page-styles"
POST = "marketing.launch-post"


@pytest.fixture
def state(tmp_path: Path) -> BuildState:
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    return open_build_state(tmp_path / "build.json", load_plan_tree(source), build_id="b")


def _ids(state: BuildState, **kwargs: object) -> list[str]:
    return [task.task_id for task in ready_tasks(state, **kwargs)]  # type: ignore[arg-type]


def _finish(state: BuildState, task_id: str, commit: str = "abc") -> None:
    attempt = state[task_id].attempt_id
    if state[task_id].status.value == "pending":
        state.begin(task_id)
    state.submit_result(task_id, attempt, commit=commit, checks=["ok"])


def test_website_waits_for_product_while_marketing_proceeds(state: BuildState) -> None:
    ready = ready_tasks(state)

    assert [t.task_id for t in ready] == [API, STYLES, POST]
    assert ready[0] == ReadyTask(
        task_id=API, plan_id="product", project_path=state.tree.get("product").project_path
    )
    assert PAGE not in _ids(state)


def test_a_finished_but_unaccepted_dependency_releases_nothing(state: BuildState) -> None:
    _finish(state, API)
    assert PAGE not in _ids(state)
    assert API not in _ids(state)  # finished work is not offered again


def test_an_accepted_dependency_releases_its_dependent(state: BuildState) -> None:
    _finish(state, API)
    state.accept(API, state[API].attempt_id)
    assert _ids(state) == [PAGE, POST]  # page now first in plan order; styles overlaps it


def test_a_blocked_dependency_never_releases_children(state: BuildState) -> None:
    state.begin(API)
    state.block(API, "the worker failed twice")
    assert _ids(state) == [STYLES, POST]


def test_a_stale_dependency_never_releases_children(state: BuildState) -> None:
    _finish(state, API)
    state.accept(API, state[API].attempt_id)
    state.note_plan_version("product", 3)  # the product plan changed after acceptance

    assert PAGE not in _ids(state)
    assert API not in _ids(state)  # it is accepted; re-planning is T19's job, not the scheduler's


def test_overlapping_ownership_excludes_the_later_task(state: BuildState) -> None:
    _finish(state, API)
    state.accept(API, state[API].attempt_id)
    assert _ids(state) == [PAGE, POST]
    assert _ids(state, running=[PAGE]) == [POST]
    assert _ids(state, running=[STYLES]) == [POST]  # a running task blocks its overlaps too


def test_running_and_selected_tasks_are_never_offered(state: BuildState) -> None:
    state.begin(POST)
    assert _ids(state) == [API, STYLES]
    assert _ids(state, running=[API]) == [STYLES]


def test_selection_is_deterministic_and_bounded(state: BuildState) -> None:
    assert _ids(state) == _ids(state) == [API, STYLES, POST]
    assert _ids(state, limit=2) == [API, STYLES]
    assert _ids(state, limit=0) == []


def test_cross_project_dependency_is_checked_the_same_way(state: BuildState) -> None:
    # PAGE (website project) depends on API (product project); accepted → released.
    assert state.tree.get("website").project_path != state.tree.get("product").project_path
    _finish(state, API)
    state.accept(API, state[API].attempt_id)
    assert PAGE in _ids(state)
