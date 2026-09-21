"""Read-only view of how much room this host has for Go Work workers (T07).

Standard library only: ``/proc`` and cgroup v2 on Linux, ``GlobalMemoryStatusEx``
on Windows, ``shutil.disk_usage`` everywhere. Every reading the host cannot
provide is listed in ``ResourceSnapshot.missing`` and read as "unknown" by the
policy — never as free capacity.
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import os
import shutil
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

#: What a worker is assumed to need until its own peaks have been observed.
DEFAULT_WORKER_MB = 1500.0
#: Below this much available memory the host is in trouble, whatever else says.
CRITICAL_AVAILABLE_MB = 1024.0
CRITICAL_DISK_MB = 1024.0
CRITICAL_SWAP_MB = 512.0
_MB = 1024 * 1024


class Pressure(StrEnum):
    HEALTHY = "healthy"
    CONSTRAINED = "constrained"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """One moment's readings; ``None`` means the host could not say."""

    memory_total_mb: float | None
    memory_available_mb: float | None
    swap_used_mb: float | None
    cpu_count: int | None
    cpu_load: float | None
    disk_free_mb: float | None
    memory_limit_mb: float | None = None
    cpu_limit: float | None = None
    memory_used_mb: float | None = None
    observed_at: str = field(default_factory=lambda: _dt.datetime.now(_dt.UTC).isoformat())

    @property
    def missing(self) -> tuple[str, ...]:
        names = (
            "memory_total_mb",
            "memory_available_mb",
            "swap_used_mb",
            "cpu_count",
            "cpu_load",
            "disk_free_mb",
        )
        return tuple(name for name in names if getattr(self, name) is None)

    @property
    def effective_memory_mb(self) -> float | None:
        values = [v for v in (self.memory_total_mb, self.memory_limit_mb) if v is not None]
        return min(values) if values else None

    @property
    def effective_cpus(self) -> float | None:
        values = [float(v) for v in (self.cpu_count, self.cpu_limit) if v is not None]
        return min(values) if values else None

    @property
    def effective_available_mb(self) -> float | None:
        if self.memory_available_mb is None:
            return None
        available = self.memory_available_mb
        if self.memory_limit_mb is not None and self.memory_used_mb is not None:
            available = min(available, self.memory_limit_mb - self.memory_used_mb)
        return max(available, 0.0)


def pressure_of(snapshot: ResourceSnapshot) -> Pressure:
    """Classify a snapshot; anything unreadable makes the answer UNKNOWN, not HEALTHY."""
    available = snapshot.effective_available_mb
    if available is not None and available < CRITICAL_AVAILABLE_MB:
        return Pressure.CRITICAL
    if snapshot.swap_used_mb is not None and snapshot.swap_used_mb > CRITICAL_SWAP_MB:
        return Pressure.CRITICAL
    if snapshot.disk_free_mb is not None and snapshot.disk_free_mb < CRITICAL_DISK_MB:
        return Pressure.CRITICAL
    if snapshot.missing:
        return Pressure.UNKNOWN
    assert available is not None and snapshot.effective_memory_mb is not None
    cpus = snapshot.effective_cpus or 1.0
    assert snapshot.cpu_load is not None
    if available < 0.2 * snapshot.effective_memory_mb or snapshot.cpu_load > 0.8 * cpus:
        return Pressure.CONSTRAINED
    return Pressure.HEALTHY


def workers_that_fit(
    snapshot: ResourceSnapshot, *, per_worker_mb: float, headroom_mb: float
) -> int | None:
    """How many more workers the memory allows after *headroom_mb* is kept back.

    ``None`` when available memory is unknown: the caller must not treat that as room.
    """
    available = snapshot.effective_available_mb
    if available is None:
        return None
    return max(0, int((available - headroom_mb) // max(per_worker_mb, 1.0)))


class WorkerPeaks:
    """The most memory each worker (with its descendants) was ever seen using."""

    def __init__(self, default_mb: float = DEFAULT_WORKER_MB) -> None:
        self._default = default_mb
        self._peaks: dict[str, float] = {}

    def observe(self, worker_id: str, *, rss_mb: float, descendants_mb: float = 0.0) -> float:
        total = max(rss_mb, 0.0) + max(descendants_mb, 0.0)
        self._peaks[worker_id] = max(self._peaks.get(worker_id, 0.0), total)
        return self._peaks[worker_id]

    def peak_mb(self, worker_id: str) -> float | None:
        return self._peaks.get(worker_id)

    def forget(self, worker_id: str) -> None:
        self._peaks.pop(worker_id, None)

    def typical_mb(self) -> float:
        """Size new workers for the biggest one seen, never below the default."""
        return max(self._default, *self._peaks.values()) if self._peaks else self._default


class HostProbe:
    """Samples this host. Each reader returns ``None`` instead of raising."""

    def __init__(self, disk_path: Path | None = None) -> None:
        self.disk_path = disk_path or Path.cwd()

    def sample(self) -> ResourceSnapshot:
        total, available, used = self._memory()
        return ResourceSnapshot(
            memory_total_mb=total,
            memory_available_mb=available,
            memory_used_mb=used,
            swap_used_mb=self._swap_used(),
            cpu_count=os.cpu_count(),
            cpu_load=self._load(),
            disk_free_mb=self._disk_free(),
            memory_limit_mb=self._cgroup_memory_limit(),
            cpu_limit=self._cgroup_cpu_limit(),
        )

    def process_tree_mb(self, pid: int) -> float | None:
        """Resident memory of *pid* and every descendant, or ``None`` where unmeasurable."""
        if sys.platform == "win32":
            return _windows_process_tree_mb(pid)
        return _proc_process_tree_mb(pid)

    # -- readers -------------------------------------------------------------

    def _memory(self) -> tuple[float | None, float | None, float | None]:
        if sys.platform == "win32":
            return _windows_memory()
        info = _proc_meminfo()
        if not info:
            return None, None, None
        total = info.get("MemTotal")
        available = info.get("MemAvailable")
        used = total - available if total is not None and available is not None else None
        return total, available, used

    def _swap_used(self) -> float | None:
        if sys.platform == "win32":
            return None
        info = _proc_meminfo()
        if "SwapTotal" in info and "SwapFree" in info:
            return info["SwapTotal"] - info["SwapFree"]
        return None

    def _load(self) -> float | None:
        getloadavg = getattr(os, "getloadavg", None)
        if getloadavg is None:
            return None
        try:
            return float(getloadavg()[0])
        except OSError:
            return None

    def _disk_free(self) -> float | None:
        try:
            return shutil.disk_usage(self.disk_path).free / _MB
        except OSError:
            return None

    def _cgroup_memory_limit(self) -> float | None:
        text = _read_text(Path("/sys/fs/cgroup/memory.max"))
        if text is None or text == "max":
            return None
        try:
            return int(text) / _MB
        except ValueError:
            return None

    def _cgroup_cpu_limit(self) -> float | None:
        text = _read_text(Path("/sys/fs/cgroup/cpu.max"))
        if text is None:
            return None
        parts = text.split()
        if len(parts) != 2 or parts[0] == "max":
            return None
        try:
            return int(parts[0]) / int(parts[1])
        except (ValueError, ZeroDivisionError):
            return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _proc_meminfo() -> dict[str, float]:
    text = _read_text(Path("/proc/meminfo"))
    if text is None:
        return {}
    values: dict[str, float] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key.strip()] = int(parts[0]) / 1024  # kB → MB
    return values


def _proc_process_tree_mb(pid: int) -> float | None:
    """Sum VmRSS over *pid* and its descendants via /proc; ``None`` if /proc is absent."""
    root = Path("/proc")
    if not root.is_dir():
        return None
    children: dict[int, list[int]] = {}
    rss: dict[int, float] = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        status = _read_text(entry / "status")
        if status is None:
            continue
        parent = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                parent = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                rss[int(entry.name)] = int(line.split()[1]) / 1024
        if parent is not None:
            children.setdefault(parent, []).append(int(entry.name))
    if pid not in rss:
        return None
    total = 0.0
    stack = [pid]
    while stack:
        current = stack.pop()
        total += rss.get(current, 0.0)
        stack.extend(children.get(current, ()))
    return total


def _windows_memory() -> tuple[float | None, float | None, float | None]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    try:
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return None, None, None
    except (AttributeError, OSError):
        return None, None, None
    total = status.ullTotalPhys / _MB
    available = status.ullAvailPhys / _MB
    return total, available, total - available


def _windows_process_tree_mb(pid: int) -> float | None:
    # Walking a process tree needs the toolhelp snapshot API; not measured here.
    return None
