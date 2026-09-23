"""T07 — resource pressure is measured, not assumed.

Snapshots are injectable, so the policy that reads them (T09) can be tested
against healthy, constrained and unavailable hosts without touching this one.
Missing observations are named and treated as "we don't know", never as room.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_core.gowork_resources import (
    DEFAULT_WORKER_MB,
    HostProbe,
    Pressure,
    ResourceSnapshot,
    WorkerPeaks,
    pressure_of,
    workers_that_fit,
)


def _snap(**kwargs: object) -> ResourceSnapshot:
    base: dict = {
        "memory_total_mb": 32000.0,
        "memory_available_mb": 20000.0,
        "swap_used_mb": 0.0,
        "cpu_count": 8,
        "cpu_load": 1.0,
        "disk_free_mb": 100000.0,
    }
    base.update(kwargs)
    return ResourceSnapshot(**base)


def test_healthy_host_has_no_missing_data_and_room_for_workers() -> None:
    snapshot = _snap()
    assert snapshot.missing == ()
    assert pressure_of(snapshot) is Pressure.HEALTHY
    assert workers_that_fit(snapshot, per_worker_mb=1500, headroom_mb=4000) == 10


def test_constrained_host_is_reported_and_admits_fewer() -> None:
    snapshot = _snap(memory_available_mb=3000.0, cpu_load=7.5)
    assert pressure_of(snapshot) is Pressure.CONSTRAINED
    assert workers_that_fit(snapshot, per_worker_mb=1500, headroom_mb=2000) == 0


def test_critical_pressure_when_swap_is_in_use_or_memory_is_nearly_gone() -> None:
    assert pressure_of(_snap(memory_available_mb=500.0)) is Pressure.CRITICAL
    assert pressure_of(_snap(swap_used_mb=2048.0)) is Pressure.CRITICAL
    assert pressure_of(_snap(disk_free_mb=200.0)) is Pressure.CRITICAL


def test_missing_observations_are_explicit_and_conservative() -> None:
    snapshot = _snap(memory_available_mb=None, cpu_load=None)
    assert snapshot.missing == ("memory_available_mb", "cpu_load")
    assert pressure_of(snapshot) is Pressure.UNKNOWN
    assert (
        workers_that_fit(snapshot, per_worker_mb=1500, headroom_mb=1000) is None
    )  # not "unlimited"


def test_partial_data_still_reports_what_it_can() -> None:
    snapshot = _snap(cpu_load=None, disk_free_mb=None)
    assert snapshot.missing == ("cpu_load", "disk_free_mb")
    assert pressure_of(snapshot) is Pressure.UNKNOWN
    assert workers_that_fit(snapshot, per_worker_mb=2000, headroom_mb=4000) == 8  # memory known


def test_container_limit_caps_the_host_numbers() -> None:
    snapshot = _snap(memory_limit_mb=8000.0, cpu_limit=2.0)
    assert snapshot.effective_memory_mb == 8000.0
    assert snapshot.effective_cpus == 2.0
    # Available memory can never exceed what the container allows minus what it uses.
    capped = _snap(memory_limit_mb=8000.0, memory_used_mb=6000.0, memory_available_mb=20000.0)
    assert capped.effective_available_mb == 2000.0
    assert workers_that_fit(capped, per_worker_mb=1000, headroom_mb=500) == 1


def test_worker_peaks_include_descendants_and_inform_sizing() -> None:
    peaks = WorkerPeaks()
    assert peaks.typical_mb() == DEFAULT_WORKER_MB  # nothing observed yet: conservative default
    peaks.observe("w1", rss_mb=800.0, descendants_mb=1200.0)  # the worker plus its test run
    peaks.observe("w1", rss_mb=700.0, descendants_mb=100.0)  # lower later; the peak sticks
    peaks.observe("w2", rss_mb=600.0, descendants_mb=0.0)
    assert peaks.peak_mb("w1") == 2000.0
    assert peaks.peak_mb("w2") == 600.0
    assert peaks.typical_mb() == 2000.0  # size for the biggest recent worker, not the average
    peaks.forget("w1")
    assert peaks.peak_mb("w1") is None
    assert peaks.typical_mb() == max(600.0, DEFAULT_WORKER_MB)


def test_host_probe_returns_a_snapshot_and_names_what_it_could_not_read(tmp_path: Path) -> None:
    snapshot = HostProbe(disk_path=tmp_path).sample()
    assert isinstance(snapshot, ResourceSnapshot)
    assert snapshot.cpu_count is not None and snapshot.cpu_count >= 1
    assert snapshot.disk_free_mb is not None and snapshot.disk_free_mb > 0
    assert all(name in ResourceSnapshot.__annotations__ for name in snapshot.missing)
    assert snapshot.observed_at


def test_host_probe_measures_a_process_tree_or_says_it_cannot() -> None:
    import os

    probe = HostProbe()
    measured = probe.process_tree_mb(os.getpid())
    assert measured is None or measured > 0
