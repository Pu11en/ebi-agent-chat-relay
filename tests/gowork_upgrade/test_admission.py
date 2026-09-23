"""T08 — one admission controller shared by every build.

Slots are reserved atomically, cancellation never leaks one, reviews keep a
reserve so they cannot deadlock behind the workers they judge, chat is
counted but never queued, and a build that keeps losing to "more useful"
work ages until it wins — a memory that survives reopening the controller.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_code_core.gowork_admission import AGE_LIMIT, AdmissionController


async def _hold(
    controller: AdmissionController, build: str, log: list, name: str, **kw: object
) -> None:
    async with controller.reserve("task", build, **kw):  # type: ignore[arg-type]
        log.append(("in", name, controller.snapshot().held))
        await asyncio.sleep(0.01)
        log.append(("out", name))


async def test_racing_requests_never_double_book(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=2)
    log: list = []
    await asyncio.gather(*(_hold(controller, "a", log, f"w{i}") for i in range(5)))

    assert max(held for kind, _, *rest in log if kind == "in" for held in rest) <= 2
    assert sum(1 for entry in log if entry[0] == "in") == 5
    assert controller.snapshot().held == 0


async def test_cancellation_releases_a_held_and_a_queued_reservation(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=1)
    holder_started = asyncio.Event()

    async def holder() -> None:
        async with controller.reserve("task", "a"):
            holder_started.set()
            await asyncio.sleep(10)

    holding = asyncio.create_task(holder())
    await holder_started.wait()
    queued = asyncio.create_task(controller.reserve("task", "b").__aenter__())
    await asyncio.sleep(0.01)
    assert controller.snapshot().waiting == 1

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert controller.snapshot().waiting == 0

    holding.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holding
    assert controller.snapshot().held == 0
    async with controller.reserve("task", "c"):  # the slot came back
        assert controller.snapshot().held == 1


async def test_reviews_cannot_deadlock_behind_workers(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=3, review_reserve=1)
    entered = 0

    async def task() -> None:
        nonlocal entered
        async with controller.reserve("task", "a"):
            entered += 1
            await asyncio.sleep(0.05)

    tasks = [asyncio.create_task(task()) for _ in range(3)]
    await asyncio.sleep(0.01)
    assert entered == 2  # the third waits: one slot is kept for reviews
    async with controller.reserve("review", "a"):  # admitted at once
        assert controller.snapshot().held == 3
    await asyncio.gather(*tasks)
    assert entered == 3


async def test_chat_is_counted_but_never_queued(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=2)
    async with controller.reserve("chat", "x"), controller.reserve("chat", "y"):
        assert controller.snapshot().held == 2
        async with controller.reserve("chat", "z"):  # a third chat still goes straight in
            assert controller.snapshot().held == 3
        waiter = asyncio.create_task(controller.reserve("task", "a").__aenter__())
        await asyncio.sleep(0.01)
        assert not waiter.done()  # tasks do wait for chat to finish
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter


async def test_unblocking_work_goes_first_but_a_starved_build_ages_in(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=1)
    order: list[str] = []
    gate = asyncio.Event()

    async def run(build: str, unblocks: int) -> None:
        async with controller.reserve("task", build, unblocks=unblocks):
            order.append(build)
            await gate.wait()
            gate.clear()

    first = asyncio.create_task(run("a", 5))
    await asyncio.sleep(0.01)
    starved = asyncio.create_task(run("b", 0))
    competitors = [asyncio.create_task(run("a", 5)) for _ in range(AGE_LIMIT + 2)]
    await asyncio.sleep(0.01)
    for _ in range(len(competitors) + 2):
        gate.set()
        await asyncio.sleep(0.01)
    await asyncio.gather(first, starved, *competitors)

    assert order[0] == "a"
    assert "b" in order[: AGE_LIMIT + 2]  # b did not wait for every a
    assert order.index("b") > 1  # but useful work went first


async def test_fairness_survives_reopen(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=1)
    gate = asyncio.Event()

    async def run(build: str, unblocks: int) -> None:
        async with controller.reserve("task", build, unblocks=unblocks):
            await gate.wait()
            gate.clear()

    first = asyncio.create_task(run("a", 5))
    await asyncio.sleep(0.01)
    starved = asyncio.create_task(run("b", 0))
    other = asyncio.create_task(run("a", 5))
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.sleep(0.01)  # a's second task won; b was passed over once
    on_disk = json.loads((tmp_path / "admission.json").read_text(encoding="utf-8"))
    assert on_disk["builds"]["b"]["passed_over"] == 1
    assert on_disk["builds"]["a"]["admitted"] == 2

    reopened = AdmissionController(tmp_path / "admission.json", capacity=1)
    assert reopened.fairness("b").passed_over == 1
    for _ in range(3):
        gate.set()
        await asyncio.sleep(0.01)
    await asyncio.gather(first, starved, other)


async def test_capacity_can_shrink_without_evicting_and_grow_to_admit(tmp_path: Path) -> None:
    controller = AdmissionController(tmp_path / "admission.json", capacity=2)
    async with controller.reserve("task", "a"), controller.reserve("task", "a"):
        controller.set_capacity(1)
        assert controller.snapshot().held == 2  # nothing evicted
        waiter = asyncio.create_task(controller.reserve("task", "b").__aenter__())
        await asyncio.sleep(0.01)
        assert not waiter.done()
        controller.set_capacity(3)
        await asyncio.wait_for(waiter, 1)
        assert controller.snapshot().held == 3
