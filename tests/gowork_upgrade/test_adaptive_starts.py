"""T10 — real process starts go through the adaptive admission path.

With no explicit MAX_CONCURRENT_SESSIONS, every run reserves a slot in the
shared controller: workers wait for capacity that grows with measured
health, chat never waits, cancellation leaves nothing behind, and the
number of running workers passes ten only once the policy admitted it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.gowork_admission import AdmissionController
from claude_code_core.gowork_capacity import CapacityPolicy
from claude_code_core.gowork_resources import ResourceSnapshot
from claude_discord.cogs import _run_helper as helper
from claude_discord.cogs.run_config import RunConfig
from claude_discord.concurrency import SessionRegistry


class FakeProbe:
    def __init__(self) -> None:
        self.available = 64000.0

    def sample(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            memory_total_mb=64000.0,
            memory_available_mb=self.available,
            swap_used_mb=0.0,
            cpu_count=16,
            cpu_load=1.0,
            disk_free_mb=500000.0,
        )


@pytest.fixture
def adaptive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AdmissionController, FakeProbe]:
    probe = FakeProbe()
    controller = AdmissionController(tmp_path / "admission.json", capacity=4, review_reserve=1)
    policy = CapacityPolicy(start=4, ceiling=32, growth_step=4, cooldown_ticks=1)
    monkeypatch.setattr(helper, "_global_semaphore", None)
    helper.configure_adaptive_limit(controller=controller, policy=policy, probe=probe)
    yield controller, probe
    helper.configure_adaptive_limit(controller=None, policy=None, probe=None)


def _run(
    kind: str,
    gate: asyncio.Event,
    started: list[str],
    name: str,
    registry: SessionRegistry | None = None,
):
    async def events(*_a, **_k):
        started.append(name)
        await gate.wait()
        if False:
            yield

    thread = MagicMock(spec=discord.Thread)
    thread.id = abs(hash(name)) % 10**6
    thread.send = AsyncMock()
    if registry is not None:
        registry.register(thread.id, name)
    runner = MagicMock()
    runner.working_dir = None
    runner.run = events
    config = RunConfig(
        thread=thread,
        runner=runner,
        prompt=name,
        registry=registry,
        slot_kind=kind,
        slot_build_id="b1",
    )
    return asyncio.create_task(helper.run_claude_with_config(config))


async def test_workers_exceed_ten_only_when_admitted(adaptive: tuple) -> None:
    controller, _probe = adaptive
    gate = asyncio.Event()
    started: list[str] = []
    tasks = [_run("task", gate, started, f"w{i}") for i in range(14)]
    await asyncio.sleep(0.05)
    assert len(started) == 3  # capacity 4 minus the review reserve
    assert helper.session_limit() == 4

    for _ in range(3):
        helper.tick_capacity()
        await asyncio.sleep(0.02)
    assert helper.session_limit() > 10
    assert len(started) > 10
    assert len(started) == min(14, helper.session_limit() - 1)

    gate.set()
    await asyncio.gather(*tasks)
    assert controller.snapshot().held == 0 and controller.snapshot().waiting == 0


async def test_pressure_pauses_new_starts(adaptive: tuple) -> None:
    controller, probe = adaptive
    gate = asyncio.Event()
    started: list[str] = []
    running = [_run("task", gate, started, f"w{i}") for i in range(3)]
    await asyncio.sleep(0.02)
    assert len(started) == 3

    probe.available = 900.0  # critical
    decision = helper.tick_capacity()
    assert decision is not None and decision.pause_starts
    waiting = _run("task", gate, started, "late")
    await asyncio.sleep(0.02)
    assert len(started) == 3 and controller.snapshot().waiting == 1

    probe.available = 64000.0
    helper.tick_capacity()  # cooldown tick
    helper.tick_capacity()  # growth
    await asyncio.sleep(0.02)
    assert "late" in started
    gate.set()
    await asyncio.gather(*running, waiting)


async def test_a_cancelled_queued_worker_leaves_nothing_behind(adaptive: tuple) -> None:
    controller, _probe = adaptive
    registry = SessionRegistry()
    gate = asyncio.Event()
    started: list[str] = []
    holders = [_run("task", gate, started, f"w{i}", registry) for i in range(3)]
    await asyncio.sleep(0.02)
    queued = _run("task", gate, started, "queued", registry)
    await asyncio.sleep(0.02)
    assert controller.snapshot().waiting == 1
    assert any(s.execution_state == "queued" for s in registry.list_active())

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert controller.snapshot().waiting == 0
    assert not any(s.execution_state == "queued" for s in registry.list_active())
    gate.set()
    await asyncio.gather(*holders)
    assert controller.snapshot().held == 0


async def test_chat_stays_responsive_without_a_worker_slot(adaptive: tuple) -> None:
    controller, _probe = adaptive
    gate = asyncio.Event()
    started: list[str] = []
    workers = [_run("task", gate, started, f"w{i}") for i in range(3)]
    await asyncio.sleep(0.02)
    blocked = _run("task", gate, started, "blocked")
    chat = _run("chat", gate, started, "chat")
    await asyncio.sleep(0.02)
    assert "chat" in started and "blocked" not in started
    assert controller.snapshot().held_chat == 1
    gate.set()
    await asyncio.gather(*workers, blocked, chat)


async def test_reviews_get_the_reserved_slot(adaptive: tuple) -> None:
    gate = asyncio.Event()
    started: list[str] = []
    workers = [_run("task", gate, started, f"w{i}") for i in range(4)]
    await asyncio.sleep(0.02)
    assert len(started) == 3
    review = _run("review", gate, started, "review")
    await asyncio.sleep(0.02)
    assert "review" in started
    gate.set()
    await asyncio.gather(*workers, review)


def test_explicit_limit_keeps_the_fixed_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    helper.configure_session_limit(6)
    assert isinstance(helper._global_semaphore, asyncio.Semaphore)
    assert helper.session_limit() == 6
    assert helper.parallel_limit() == 6
