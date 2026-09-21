"""T09 — admissions follow measured resources, not a fixed ten.

Start small, grow while the host stays healthy (bounded by what observed
worker peaks say will fit), pause on pressure, shed at most one worker per
tick under critical pressure, and wait out a cooldown before growing again
so the controller does not oscillate. Operator limits always win.
"""

from __future__ import annotations

from claude_code_core.gowork_capacity import CapacityPolicy
from claude_code_core.gowork_resources import ResourceSnapshot, WorkerPeaks


def _snap(available: float | None = 60000.0, **kwargs: object) -> ResourceSnapshot:
    base: dict = {
        "memory_total_mb": 64000.0,
        "memory_available_mb": available,
        "swap_used_mb": 0.0,
        "cpu_count": 16,
        "cpu_load": 2.0,
        "disk_free_mb": 500000.0,
    }
    base.update(kwargs)
    return ResourceSnapshot(**base)


def test_starts_conservative_and_grows_past_ten_on_a_healthy_host() -> None:
    policy = CapacityPolicy(start=2, ceiling=32, growth_step=2)
    peaks = WorkerPeaks()
    assert policy.capacity == 2
    seen = []
    held = 0
    for _ in range(12):
        decision = policy.decide(_snap(), peaks, held=held)
        seen.append(decision.capacity)
        held = decision.capacity  # every slot gets used
    assert seen[0] == 4 and all(b - a <= 2 for a, b in zip(seen, seen[1:], strict=False))
    assert max(seen) > 10
    assert max(seen) <= 32
    assert not decision.pause_starts and decision.cancel_up_to == 0


def test_growth_is_bounded_by_what_observed_peaks_say_will_fit() -> None:
    policy = CapacityPolicy(start=2, ceiling=32, growth_step=8, headroom_mb=4000)
    peaks = WorkerPeaks()
    peaks.observe("w1", rss_mb=6000.0, descendants_mb=4000.0)  # 10 GB workers
    decision = policy.decide(_snap(available=34000.0), peaks, held=0)
    assert decision.capacity == 3  # (34000 - 4000) // 10000
    assert "fit" in decision.reason


def test_constrained_pressure_pauses_starts_without_evicting() -> None:
    policy = CapacityPolicy(start=6)
    decision = policy.decide(_snap(available=9000.0, cpu_load=15.0), WorkerPeaks(), held=5)
    assert decision.pause_starts
    assert decision.capacity == 5  # what is running may finish; nothing new starts
    assert decision.cancel_up_to == 0


def test_critical_pressure_sheds_one_worker_per_tick_down_to_one() -> None:
    policy = CapacityPolicy(start=6)
    first = policy.decide(_snap(available=400.0), WorkerPeaks(), held=6)
    assert first.pause_starts and first.cancel_up_to == 1 and first.capacity == 5
    second = policy.decide(_snap(available=400.0), WorkerPeaks(), held=5)
    assert second.cancel_up_to == 1 and second.capacity == 4
    floor = policy.decide(_snap(available=400.0), WorkerPeaks(), held=1)
    assert floor.capacity == 1 and floor.cancel_up_to == 0  # never cancel the last one


def test_recovery_waits_out_the_cooldown_and_does_not_oscillate() -> None:
    policy = CapacityPolicy(start=2, cooldown_ticks=3, growth_step=2)
    peaks = WorkerPeaks()
    for _ in range(4):
        policy.decide(_snap(), peaks, held=policy.capacity)
    before = policy.capacity
    policy.decide(_snap(available=9000.0, cpu_load=15.0), peaks, held=before)  # pressure
    caps = [policy.decide(_snap(), peaks, held=policy.capacity).capacity for _ in range(5)]
    assert caps[:3] == [before] * 3  # held steady through the cooldown
    assert caps[3] > before and caps[4] >= caps[3]  # then grows, and keeps growing
    assert all(b >= a for a, b in zip(caps, caps[1:], strict=False))


def test_missing_data_means_no_growth_beyond_what_already_runs() -> None:
    policy = CapacityPolicy(start=2, growth_step=4)
    peaks = WorkerPeaks()
    for _ in range(3):
        policy.decide(_snap(), peaks, held=policy.capacity)
    grown = policy.capacity
    unknown = policy.decide(_snap(available=None), peaks, held=3)
    assert unknown.capacity == max(2, 3) and "unknown" in unknown.reason
    assert unknown.capacity < grown
    assert not unknown.pause_starts  # nothing measured says stop; it says don't grow


def test_operator_and_provider_limits_are_kept_separate_and_always_apply() -> None:
    policy = CapacityPolicy(start=2, ceiling=32, growth_step=16, operator_limit=4)
    decision = policy.decide(_snap(), WorkerPeaks(), held=2)
    assert decision.capacity == 4
    assert decision.external_limit == 4
    assert decision.measured_capacity > 4  # the measurement is reported, not hidden
    policy.set_external_limit(None)
    assert policy.decide(_snap(), WorkerPeaks(), held=4).capacity > 4


def test_the_protective_ceiling_holds_whatever_the_host_offers() -> None:
    policy = CapacityPolicy(start=30, ceiling=32, growth_step=50)
    assert policy.decide(_snap(available=600000.0), WorkerPeaks(), held=30).capacity == 32
