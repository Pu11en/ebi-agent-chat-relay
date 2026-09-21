"""How many workers the host should be allowed right now (T09).

Every tick the policy looks at one resource snapshot and answers with a
capacity for the admission controller. It starts small, grows only while
the host is healthy and only as far as observed worker peaks say will fit,
pauses new starts under pressure, sheds one worker per tick when pressure
is critical, and waits out a cooldown before growing again so a host on
the edge does not see-saw. An operator or provider limit is kept apart
from the measurement and applied last.
"""

from __future__ import annotations

from dataclasses import dataclass

from claude_code_core.gowork_resources import (
    Pressure,
    ResourceSnapshot,
    WorkerPeaks,
    pressure_of,
    workers_that_fit,
)


@dataclass(frozen=True, slots=True)
class CapacityDecision:
    capacity: int
    measured_capacity: int
    external_limit: int | None
    pressure: Pressure
    pause_starts: bool
    cancel_up_to: int
    reason: str


class CapacityPolicy:
    def __init__(
        self,
        *,
        start: int = 2,
        ceiling: int = 32,
        growth_step: int = 2,
        headroom_mb: float = 4096.0,
        cooldown_ticks: int = 3,
        operator_limit: int | None = None,
    ) -> None:
        if start < 1 or ceiling < start or growth_step < 1 or cooldown_ticks < 0:
            raise ValueError("capacity policy parameters out of range")
        self.start = start
        self.ceiling = ceiling
        self.growth_step = growth_step
        self.headroom_mb = headroom_mb
        self.cooldown_ticks = cooldown_ticks
        self._external_limit = operator_limit
        self._capacity = start
        self._cooldown = 0

    @property
    def capacity(self) -> int:
        return self._apply_limit(self._capacity)

    def set_external_limit(self, limit: int | None) -> None:
        """An explicit operator/provider constraint; never inferred from measurements."""
        if limit is not None and limit < 1:
            raise ValueError("an external limit must be ≥ 1")
        self._external_limit = limit

    def decide(
        self, snapshot: ResourceSnapshot, peaks: WorkerPeaks, *, held: int
    ) -> CapacityDecision:
        pressure = pressure_of(snapshot)
        held = max(0, held)
        pause = False
        cancel = 0

        if pressure is Pressure.CRITICAL:
            self._cooldown = self.cooldown_ticks
            pause = True
            cancel = 1 if held > 1 else 0
            measured = max(1, min(self._capacity, held) - 1) if held > 1 else 1
            reason = "critical pressure: no new starts, shedding one worker"
        elif pressure is Pressure.CONSTRAINED:
            self._cooldown = self.cooldown_ticks
            pause = True
            measured = max(1, held)
            reason = "constrained: running work may finish, nothing new starts"
        elif pressure is Pressure.UNKNOWN:
            measured = max(self.start, min(self._capacity, held))
            reason = f"unknown resources ({', '.join(snapshot.missing)}): holding, not growing"
        else:
            fit = workers_that_fit(
                snapshot, per_worker_mb=peaks.typical_mb(), headroom_mb=self.headroom_mb
            )
            room = held + (fit if fit is not None else 0)
            if self._cooldown > 0:
                self._cooldown -= 1
                measured = min(self._capacity, room) if room >= 1 else 1
                reason = f"healthy, cooling down ({self._cooldown} ticks left)"
            else:
                measured = min(self._capacity + self.growth_step, room)
                reason = f"healthy: {fit} more would fit at {peaks.typical_mb():.0f} MB each"
            measured = max(1, measured)

        measured = min(measured, self.ceiling)
        self._capacity = measured
        return CapacityDecision(
            capacity=self._apply_limit(measured),
            measured_capacity=measured,
            external_limit=self._external_limit,
            pressure=pressure,
            pause_starts=pause,
            cancel_up_to=cancel,
            reason=reason,
        )

    def _apply_limit(self, value: int) -> int:
        if self._external_limit is None:
            return value
        return min(value, self._external_limit)
