"""One admission controller for every build on this host (T08).

A slot is a promise of resources. Tasks and reviews wait for one; chat never
waits but is counted, so workers do not crowd out the person. A few slots are
kept back for reviews, because a review that queues behind the workers it
must judge can never finish. Among waiting tasks, the one that unblocks the
most other work goes first — unless a build has been passed over often
enough, in which case it goes next whatever the others promise. That count
is on disk, so a restart does not reset a starving build's place in line.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

#: Passed over this many times, a build's next request goes first.
AGE_LIMIT = 3

SlotKind = Literal["task", "review", "chat"]


@dataclass(frozen=True, slots=True)
class Fairness:
    admitted: int = 0
    passed_over: int = 0
    last_admitted: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionSnapshot:
    capacity: int
    review_reserve: int
    held: int
    held_tasks: int
    held_reviews: int
    held_chat: int
    waiting: int


@dataclass(slots=True)
class _Waiter:
    kind: SlotKind
    build_id: str
    unblocks: int
    woken: asyncio.Future[None]
    passed_over: int = 0
    order: int = 0


@dataclass(slots=True)
class Reservation:
    """Held until released; usable as ``async with controller.reserve(...)``."""

    controller: AdmissionController
    kind: SlotKind
    build_id: str
    unblocks: int = 0
    _held: bool = field(default=False, init=False)

    async def acquire(self) -> Reservation:
        await self.controller._acquire(self)
        self._held = True
        return self

    def release(self) -> None:
        if self._held:
            self._held = False
            self.controller._release(self)

    async def __aenter__(self) -> Reservation:
        return await self.acquire()

    async def __aexit__(self, *_exc: object) -> None:
        self.release()


class AdmissionController:
    def __init__(self, path: Path, *, capacity: int, review_reserve: int = 0) -> None:
        if capacity < 1 or review_reserve < 0 or review_reserve >= capacity:
            raise ValueError("capacity must be ≥ 1 and larger than the review reserve")
        self.path = path
        self._capacity = capacity
        self._review_reserve = review_reserve
        self._held: dict[SlotKind, int] = {"task": 0, "review": 0, "chat": 0}
        self._waiting: list[_Waiter] = []
        self._counter = 0
        self._fairness: dict[str, Fairness] = self._load()

    # -- public --------------------------------------------------------------

    def reserve(self, kind: SlotKind, build_id: str, *, unblocks: int = 0) -> Reservation:
        return Reservation(self, kind, build_id, unblocks)

    def set_capacity(self, capacity: int) -> None:
        """Change the ceiling. Held slots stay held; waiters are re-examined."""
        if capacity < 1:
            raise ValueError("capacity must be ≥ 1")
        self._capacity = capacity
        self._review_reserve = min(self._review_reserve, capacity - 1)
        self._wake()

    def snapshot(self) -> AdmissionSnapshot:
        return AdmissionSnapshot(
            capacity=self._capacity,
            review_reserve=self._review_reserve,
            held=sum(self._held.values()),
            held_tasks=self._held["task"],
            held_reviews=self._held["review"],
            held_chat=self._held["chat"],
            waiting=len(self._waiting),
        )

    def has_room(self, kind: SlotKind) -> bool:
        """Would a request of this kind be admitted right now, without waiting?"""
        return not self._waiting and self._room_for(kind)

    def fairness(self, build_id: str) -> Fairness:
        return self._fairness.get(build_id, Fairness())

    # -- admission -----------------------------------------------------------

    def _room_for(self, kind: SlotKind) -> bool:
        if kind == "chat":
            return True
        held = sum(self._held.values())
        if kind == "review":
            return held < self._capacity
        return held < self._capacity - self._review_reserve

    async def _acquire(self, reservation: Reservation) -> None:
        if reservation.kind == "chat" or (not self._waiting and self._room_for(reservation.kind)):
            self._admit(reservation)
            return
        loop = asyncio.get_running_loop()
        self._counter += 1
        waiter = _Waiter(
            reservation.kind,
            reservation.build_id,
            reservation.unblocks,
            loop.create_future(),
            passed_over=self.fairness(reservation.build_id).passed_over,
            order=self._counter,
        )
        self._waiting.append(waiter)
        self._wake()
        try:
            await waiter.woken
        except asyncio.CancelledError:
            if waiter in self._waiting:
                self._waiting.remove(waiter)
            elif waiter.woken.done() and not waiter.woken.cancelled():
                # Admitted and cancelled in the same tick: give the slot back.
                self._held[waiter.kind] -= 1
                self._wake()
            raise
        self._fairness[reservation.build_id] = Fairness(
            admitted=self.fairness(reservation.build_id).admitted + 1,
            passed_over=0,
            last_admitted=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        )
        self._save()

    def _admit(self, reservation: Reservation) -> None:
        self._held[reservation.kind] += 1
        if reservation.kind != "chat":
            current = self.fairness(reservation.build_id)
            self._fairness[reservation.build_id] = Fairness(
                admitted=current.admitted + 1,
                passed_over=0,
                last_admitted=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            )
            self._save()

    def _release(self, reservation: Reservation) -> None:
        self._held[reservation.kind] = max(0, self._held[reservation.kind] - 1)
        self._wake()

    def _wake(self) -> None:
        """Admit the best waiter while there is room; record who was passed over."""
        while self._waiting:
            # A waiter cancelled in this same tick still sits in the line with a
            # cancelled future; resolving it would raise. Drop it first.
            self._waiting = [w for w in self._waiting if not w.woken.done()]
            chosen = self._choose()
            if chosen is None or not self._room_for(chosen.kind):
                break
            self._waiting.remove(chosen)
            for other in self._waiting:
                if other.build_id != chosen.build_id:
                    other.passed_over += 1
                    self._fairness[other.build_id] = Fairness(
                        admitted=self.fairness(other.build_id).admitted,
                        passed_over=other.passed_over,
                        last_admitted=self.fairness(other.build_id).last_admitted,
                    )
            self._held[chosen.kind] += 1
            chosen.woken.set_result(None)
        if self._waiting:
            self._save()

    def _choose(self) -> _Waiter | None:
        eligible = [w for w in self._waiting if self._room_for(w.kind)]
        if not eligible:
            return None
        starving = [w for w in eligible if w.passed_over >= AGE_LIMIT]
        pool = starving or eligible
        return min(pool, key=lambda w: (w.kind != "review", -w.unblocks, w.order))

    # -- persistence ---------------------------------------------------------

    def _load(self) -> dict[str, Fairness]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        builds = raw.get("builds", {}) if isinstance(raw, dict) else {}
        result: dict[str, Fairness] = {}
        for build_id, value in builds.items():
            if isinstance(value, dict):
                result[str(build_id)] = Fairness(
                    admitted=int(value.get("admitted", 0)),
                    passed_over=int(value.get("passed_over", 0)),
                    last_admitted=value.get("last_admitted"),
                )
        return result

    def _save(self) -> None:
        document = {
            "builds": {
                build_id: {
                    "admitted": f.admitted,
                    "passed_over": f.passed_over,
                    "last_admitted": f.last_admitted,
                }
                for build_id, f in self._fairness.items()
            }
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
