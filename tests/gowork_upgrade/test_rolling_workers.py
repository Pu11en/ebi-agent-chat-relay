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
        after_manifest_result=getattr(worker, "after", None),
        reconcile=None,
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


async def test_the_after_save_hook_runs_only_once_the_result_is_on_disk(
    plan: Path, tmp_path: Path
) -> None:
    """T14: whatever cleans up a worker (archiving its thread) sees the saved result."""
    seen: list[tuple[str, str]] = []

    async def after(result: ManifestResult) -> None:
        seen.append((result.task_id, _state(plan, tmp_path)[result.task_id].status.value))
        if result.task_id == POST:
            raise RuntimeError("Discord is down")  # must not break the build

    worker = FakeWorker({}, raise_for={STYLES})
    loop = _loop(plan, tmp_path, worker)
    loop._after_manifest_result = after
    outcome = await loop.run()

    assert outcome.status is Status.STUCK
    assert dict(seen) == {API: "accepted", POST: "accepted", STYLES: "blocked", PAGE: "accepted"}


async def test_a_result_whose_combined_check_failed_is_saved_but_not_accepted(
    plan: Path, tmp_path: Path
) -> None:
    """T15: the commit is kept for repair, the task is blocked, dependents wait."""

    class CombinedCheckFails(FakeWorker):
        async def __call__(self, task: ReadyTask) -> ManifestResult:
            result = await super().__call__(task)
            if task.task_id == API:
                return ManifestResult(
                    API,
                    False,
                    "combined check failed: 2 tests broke",
                    commit="c-api",
                    checks=("pytest: 2 failed",),
                )
            return result

    outcome = await _loop(plan, tmp_path, CombinedCheckFails({})).run()

    assert outcome.status is Status.STUCK and "combined check failed" in outcome.detail
    state = _state(plan, tmp_path)
    assert state[API].status is TaskStatus.BLOCKED
    assert state[API].result_commit == "c-api" and state[API].checks == ("pytest: 2 failed",)
    assert state[PAGE].status is TaskStatus.PENDING
    assert state.accepted_tasks() == (STYLES, POST)


async def test_attempts_left_running_by_a_crash_are_reconciled_not_reassigned(
    plan: Path, tmp_path: Path
) -> None:
    """T17: a running attempt found at startup is salvaged by the adapter or blocked — never
    silently given to a new worker."""
    state = _state(plan, tmp_path)
    state.begin(API)  # the bot died while this worker ran
    state.begin(POST)

    salvaged: list[str] = []

    async def reconcile(ledger) -> None:  # noqa: ANN001
        # the adapter found API's side copy merged: save it
        attempt = ledger[API].attempt_id
        ledger.submit_result(API, attempt, commit="c-salvaged", checks=("found merged",))
        ledger.accept(API, attempt)
        salvaged.append(API)

    worker = FakeWorker({})
    loop = _loop(plan, tmp_path, worker)
    loop._reconcile = reconcile
    outcome = await loop.run()

    assert salvaged == [API]
    assert API not in worker.started  # never rerun
    state = _state(plan, tmp_path)
    assert state[API].accepted and state[API].result_commit == "c-salvaged"
    assert state[POST].status is TaskStatus.BLOCKED and "restart" in (state[POST].reason or "")
    assert POST not in worker.started  # uncertain ownership is not reassigned
    assert state[PAGE].accepted and state[STYLES].accepted  # the rest carried on
    assert outcome.status is Status.STUCK


async def test_a_failure_gets_exactly_one_automatic_repair(plan: Path, tmp_path: Path) -> None:
    """T18: fail → one repair attempt with the reason → fail again → blocker. No hidden loops."""

    class FailsTwice(FakeWorker):
        def __init__(self) -> None:
            super().__init__({})
            self.attempts: list[str] = []

        async def __call__(self, task: ReadyTask) -> ManifestResult:
            result = await super().__call__(task)
            if task.task_id == API:
                self.attempts.append(task.task_id)
                return ManifestResult(API, False, f"the tests failed (try {len(self.attempts)})")
            return result

    worker = FailsTwice()
    loop = _loop(plan, tmp_path, worker)
    outcome = await loop.run()

    assert worker.attempts == [API, API]  # the original and its one repair, never a third
    state = _state(plan, tmp_path)
    assert state[API].status is TaskStatus.BLOCKED and state[API].attempt == 2
    assert state[API].lineage_repairs == 1 and "try 2" in (state[API].reason or "")
    assert outcome.status is Status.STUCK and API in outcome.detail
    assert state.accepted_tasks() == (STYLES, POST)  # independent work continued


async def test_a_repair_that_succeeds_is_accepted_and_the_reason_reached_the_worker(
    plan: Path, tmp_path: Path
) -> None:
    class FailsOnce(FakeWorker):
        def __init__(self) -> None:
            super().__init__({})
            self.seen_failures: list[str | None] = []

        async def __call__(self, task: ReadyTask) -> ManifestResult:
            result = await super().__call__(task)
            if task.task_id == API:
                self.seen_failures.append(_state(plan, tmp_path)[API].previous_failure)
                if len(self.seen_failures) == 1:
                    return ManifestResult(API, False, "forgot the version field")
            return result

    worker = FailsOnce()
    outcome = await _loop(plan, tmp_path, worker).run()
    assert outcome.status is Status.COMPLETE
    assert worker.seen_failures == [None, "forgot the version field"]
    assert _state(plan, tmp_path)[API].accepted and _state(plan, tmp_path)[API].attempt == 2


async def test_the_repair_budget_survives_a_restart(plan: Path, tmp_path: Path) -> None:
    """T18: an interrupted repair attempt does not earn the task another automatic repair."""
    state = _state(plan, tmp_path)
    state.begin(API)
    state.block(API, "the tests failed")
    state.repair(API)  # the repair attempt was running when the bot died
    state.begin(API)

    worker = FakeWorker({})
    outcome = await _loop(plan, tmp_path, worker).run()
    assert API not in worker.started  # blocked by the restart rule, and no third attempt
    state = _state(plan, tmp_path)
    assert state[API].status is TaskStatus.BLOCKED and state[API].attempt == 2
    assert outcome.status is Status.STUCK


async def test_interruptions_and_cancellations_do_not_spend_the_repair(
    plan: Path, tmp_path: Path
) -> None:
    class Cancelled(FakeWorker):
        async def __call__(self, task: ReadyTask) -> ManifestResult:
            if task.task_id == API:
                return ManifestResult(API, False, "the worker was cancelled")
            return await super().__call__(task)

    await _loop(plan, tmp_path, Cancelled({})).run()
    state = _state(plan, tmp_path)
    assert state[API].status is TaskStatus.BLOCKED and state[API].lineage_repairs == 0


def _bump_product(plan: Path) -> None:
    text = plan.read_text(encoding="utf-8")
    text = text.replace('"id": "product", "version": 2', '"id": "product", "version": 3')
    text = text.replace(
        '"plan_id": "product",\n      "plan_version": 2',
        '"plan_id": "product",\n      "plan_version": 3',
    )
    plan.write_text(text, encoding="utf-8")


async def test_a_mid_build_plan_change_reworks_only_the_affected_tasks(
    plan: Path, tmp_path: Path
) -> None:
    """T19: the person edits the product plan while its task runs. The current attempt
    finishes and is kept, a rework attempt (not a repair) follows at the new version, the
    dependent page waits for it, and the other plans' tasks keep moving."""

    class EditsPlanOnce(FakeWorker):
        def __init__(self) -> None:
            super().__init__({API: 0.05, STYLES: 0.05, POST: 0.05, PAGE: 0.02})
            self.api_attempts: list[tuple[int, str | None, str | None]] = []

        async def __call__(self, task: ReadyTask) -> ManifestResult:
            if task.task_id == API:
                record = _state(plan, tmp_path)[API]
                self.api_attempts.append(
                    (record.attempt, record.rework_reason, record.previous_commit)
                )
                if record.attempt == 1:
                    _bump_product(plan)  # the plan changes while the first attempt works
            return await super().__call__(task)

    worker = EditsPlanOnce()
    outcome = await _loop(plan, tmp_path, worker).run()

    assert outcome.status is Status.COMPLETE
    assert [a[0] for a in worker.api_attempts] == [1, 2]
    assert worker.api_attempts[1][1] and "version 3" in worker.api_attempts[1][1]  # a rework
    assert worker.api_attempts[1][2] == f"c-{API}"  # the kept result of attempt 1
    state = _state(plan, tmp_path)
    assert state[API].accepted and state[API].plan_version == 3 and state[API].lineage_repairs == 0
    assert worker.started[PAGE] >= worker.finished[API]  # PAGE waited for the reworked API
    assert list(worker.started).count(STYLES) == 1 and list(worker.started).count(POST) == 1
