"""T11b — a manifest plan is built from its ledger and ready set.

The loop asks `ready_tasks()` what may start, dispatches those together,
records what came back (accepted or blocked) and stops when everything is
accepted or nothing can move. Two projects progress side by side; a
dependent task waits for its prerequisite's acceptance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_schedule import ReadyTask
from claude_code_core.gowork_state import TaskStatus, open_build_state
from claude_code_core.task_loop import LoopOutcome, ManifestResult, Status, TaskLoop

FIXTURES = Path(__file__).parent / "fixtures"
API, PAGE, STYLES, POST = (
    "product.catalog-api",
    "website.catalog-page",
    "website.page-styles",
    "marketing.launch-post",
)


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    return source


class FakeDispatch:
    def __init__(self, fail: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.rounds: list[list[str]] = []
        self.fail = set(fail)

    async def __call__(self, tasks: list[ReadyTask]) -> list[ManifestResult]:
        self.rounds.append([t.task_id for t in tasks])
        return [
            ManifestResult(
                task_id=t.task_id,
                ok=t.task_id not in self.fail,
                detail="" if t.task_id not in self.fail else "the tests failed",
                commit=f"c-{t.task_id}" if t.task_id not in self.fail else None,
                checks=("pytest: passed",),
            )
            for t in tasks
        ]


def _loop(plan: Path, tmp_path: Path, dispatch: FakeDispatch, **kwargs: object) -> TaskLoop:
    reports: list[str] = []

    async def report(text: str) -> None:
        reports.append(text)

    async def run_round(prompt: str) -> tuple[str | None, str | None]:
        raise AssertionError("a manifest build never runs a checkbox round")

    async def ask(question: str) -> str | None:
        return None

    loop = TaskLoop(
        plan_path=plan,
        repo_dir=tmp_path,
        run_round=run_round,
        ask=ask,
        report=report,
        manifest_dispatch=dispatch,
        state_path=tmp_path / "state" / "build.json",
        build_id="thread-1",
        **kwargs,  # type: ignore[arg-type]
    )
    loop.reports = reports  # type: ignore[attr-defined]
    return loop


async def test_two_projects_progress_while_a_dependent_waits(plan: Path, tmp_path: Path) -> None:
    dispatch = FakeDispatch()
    loop = _loop(plan, tmp_path, dispatch)

    outcome = await loop.run()

    assert outcome == LoopOutcome(Status.COMPLETE, rounds=2)
    assert dispatch.rounds[0] == [API, STYLES, POST]  # product + website + marketing together
    assert dispatch.rounds[1] == [PAGE]  # released only by API's acceptance
    state = open_build_state(
        tmp_path / "state" / "build.json", load_plan_tree(plan), build_id="thread-1"
    )
    assert state.accepted_tasks() == (API, PAGE, STYLES, POST)
    assert state[API].result_commit == f"c-{API}" and state[API].checks == ("pytest: passed",)
    assert any("4 tasks" in line and "accepted" in line for line in loop.reports)  # type: ignore[attr-defined]


async def test_a_failed_task_blocks_its_dependents_not_the_others(
    plan: Path, tmp_path: Path
) -> None:
    dispatch = FakeDispatch(fail={API})
    loop = _loop(plan, tmp_path, dispatch)

    outcome = await loop.run()

    assert outcome.status is Status.STUCK and API in outcome.detail
    assert dispatch.rounds == [[API, STYLES, POST]]  # PAGE never dispatched
    state = open_build_state(
        tmp_path / "state" / "build.json", load_plan_tree(plan), build_id="thread-1"
    )
    assert state[API].status is TaskStatus.BLOCKED and "tests failed" in (state[API].reason or "")
    assert state.accepted_tasks() == (STYLES, POST)
    assert state[PAGE].status is TaskStatus.PENDING


async def test_reopening_carries_on_from_the_ledger(plan: Path, tmp_path: Path) -> None:
    first = FakeDispatch(fail={STYLES})
    await _loop(plan, tmp_path, first).run()  # API, POST accepted; STYLES blocked; PAGE pending

    state = open_build_state(
        tmp_path / "state" / "build.json", load_plan_tree(plan), build_id="thread-1"
    )
    state.retry(STYLES)  # the person had the failure fixed; try again
    second = FakeDispatch()
    outcome = await _loop(plan, tmp_path, second).run()

    assert outcome.status is Status.COMPLETE
    assert first.rounds == [[API, STYLES, POST], [PAGE]]  # PAGE ran once API was accepted
    assert second.rounds == [[STYLES]]  # only the retried task was left
    assert sum(len(r) for r in first.rounds) + sum(len(r) for r in second.rounds) == 5


async def test_stop_between_rounds_is_honoured(plan: Path, tmp_path: Path) -> None:
    dispatch = FakeDispatch()
    loop = _loop(plan, tmp_path, dispatch)
    loop.request_stop()
    outcome = await loop.run()
    assert outcome.status is Status.NONE and dispatch.rounds == []


async def test_the_parallel_limit_bounds_a_round(plan: Path, tmp_path: Path) -> None:
    dispatch = FakeDispatch()
    loop = _loop(plan, tmp_path, dispatch, max_parallel=lambda: 2)
    outcome = await loop.run()
    assert outcome.status is Status.COMPLETE
    assert all(len(r) <= 2 for r in dispatch.rounds)
    assert dispatch.rounds[0] == [API, STYLES]


async def test_an_invalid_manifest_stops_with_the_reason(plan: Path, tmp_path: Path) -> None:
    text = plan.read_text(encoding="utf-8").replace(
        '"dependencies": ["product.catalog-api"]', '"dependencies": ["nope"]'
    )
    plan.write_text(text, encoding="utf-8")
    outcome = await _loop(plan, tmp_path, FakeDispatch()).run()
    assert outcome.status is Status.STUCK and "unknown task 'nope'" in outcome.detail
