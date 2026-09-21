"""T13 — workers are saved and released one at a time.

A fast worker's result is recorded (and its dependents released) while a
slow sibling is still running; a worker that raises blocks only its task;
cancelling the build leaves in-flight attempts as they were, with nothing
discarded; a saved result survives a crash that happens afterwards.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_schedule import ReadyTask
from claude_code_core.gowork_state import TaskStatus, open_build_state
from claude_code_core.task_loop import ManifestResult, Status, TaskLoop

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


class FakeWorker:
    def __init__(self, delays: dict[str, float], *, raise_for: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.delays = delays
        self.raise_for = set(raise_for)
        self.started: dict[str, float] = {}
        self.finished: dict[str, float] = {}
        self.cancelled: set[str] = set()

    async def __call__(self, task: ReadyTask) -> ManifestResult:
        loop = asyncio.get_running_loop()
        self.started[task.task_id] = loop.time()
        try:
            await asyncio.sleep(self.delays.get(task.task_id, 0.01))
        except asyncio.CancelledError:
            self.cancelled.add(task.task_id)
            raise
        self.finished[task.task_id] = loop.time()
        if task.task_id in self.raise_for:
            raise RuntimeError("the worker process died")
        return ManifestResult(task.task_id, True, "", commit=f"c-{task.task_id}", checks=("ok",))


def _loop(plan: Path, tmp_path: Path, worker: FakeWorker) -> TaskLoop:
    async def report(_text: str) -> None:
        pass

    async def run_round(_prompt: str) -> tuple[str | None, str | None]:
        raise AssertionError("no checkbox rounds")

    async def ask(_q: str) -> str | None:
        return None

    return TaskLoop(
        plan_path=plan,
        repo_dir=tmp_path,
        run_round=run_round,
        ask=ask,
        report=report,
        manifest_worker=worker,
        state_path=tmp_path / "state" / "build.json",
        build_id="thread-1",
    )


def _state(plan: Path, tmp_path: Path):  # noqa: ANN202
    return open_build_state(
        tmp_path / "state" / "build.json", load_plan_tree(plan), build_id="thread-1"
    )


async def test_a_fast_worker_releases_its_dependent_while_a_sibling_runs(
    plan: Path, tmp_path: Path
) -> None:
    worker = FakeWorker({API: 0.02, POST: 0.3, STYLES: 0.02, PAGE: 0.02})
    outcome = await _loop(plan, tmp_path, worker).run()

    assert outcome.status is Status.COMPLETE
    assert worker.started[PAGE] < worker.finished[POST]  # PAGE began before POST ended
    assert worker.started[PAGE] >= worker.finished[API]
    assert _state(plan, tmp_path).accepted_tasks() == (API, PAGE, STYLES, POST)


async def test_a_worker_that_raises_blocks_only_its_task(plan: Path, tmp_path: Path) -> None:
    worker = FakeWorker({}, raise_for={API})
    outcome = await _loop(plan, tmp_path, worker).run()

    assert outcome.status is Status.STUCK and API in outcome.detail
    state = _state(plan, tmp_path)
    assert state[API].status is TaskStatus.BLOCKED and "worker process died" in (
        state[API].reason or ""
    )
    assert state.accepted_tasks() == (STYLES, POST)
    assert PAGE not in worker.started


async def test_cancelling_the_build_keeps_in_flight_attempts_and_saved_results(
    plan: Path, tmp_path: Path
) -> None:
    worker = FakeWorker({API: 0.02, POST: 5.0, STYLES: 5.0, PAGE: 5.0})
    run = asyncio.create_task(_loop(plan, tmp_path, worker).run())
    for _ in range(200):
        if API in worker.finished and _state(plan, tmp_path)[API].accepted:
            break
        await asyncio.sleep(0.01)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert worker.cancelled == {POST, STYLES}  # PAGE waits on STYLES's files, never started
    state = _state(plan, tmp_path)
    assert state[API].status is TaskStatus.ACCEPTED  # saved before the cancel
    assert {state[t].status for t in (POST, STYLES)} == {TaskStatus.RUNNING}  # retained
    assert state[PAGE].status is TaskStatus.PENDING


async def test_a_saved_result_survives_a_crash_right_after_it(plan: Path, tmp_path: Path) -> None:
    class CrashAfterSave(FakeWorker):
        async def __call__(self, task: ReadyTask) -> ManifestResult:
            result = await super().__call__(task)
            if task.task_id == API:
                # the loop must persist the result even though the round then blows up
                self.crash = True
            return result

    worker = CrashAfterSave({})
    loop = _loop(plan, tmp_path, worker)
    original = loop._record_manifest_result

    async def record_then_crash(state, result):  # noqa: ANN001
        await original(state, result)
        if result.task_id == API:
            raise OSError("disk went away during cleanup")

    loop._record_manifest_result = record_then_crash  # type: ignore[method-assign]
    with pytest.raises(OSError):
        await loop.run()
    assert _state(plan, tmp_path)[API].status is TaskStatus.ACCEPTED
