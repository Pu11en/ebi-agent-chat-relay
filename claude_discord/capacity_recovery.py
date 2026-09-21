"""The shared recovery coordinator: one logical turn, executed at most once.

Interactive chat, ``/gowork`` and headless workers all hand the coordinator a
:class:`TurnSubmission` and a function that performs *one complete backend
attempt*. The coordinator classifies each failed attempt with
:mod:`claude_code_core.capacity`, applies :mod:`claude_code_core.capacity_policy`,
keeps the pending turn durable in :class:`CapacityRecoveryRepository`, and
accepts exactly one result — a late original answer or a second worker after a
restart finds the turn already taken and makes no model call.

Status changes go through ``on_status`` so the surface decides how to show
them; fallback switches go through ``on_switch`` so the caller rebuilds its
backend. The coordinator itself knows nothing about Discord.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from claude_code_core.capacity import (
    BackendFailure,
    CapacityCategory,
    CapacityOutcome,
    classify_failure,
)
from claude_code_core.capacity_policy import (
    FallbackTarget,
    RecoveryPhase,
    RecoveryPolicy,
    RecoveryStatus,
    RecoveryTracker,
)

from .database.capacity_recovery_repo import (
    CapacityPendingTurn,
    CapacityPendingTurnCreate,
    CapacityRecoveryRepository,
    PendingTurnState,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AttemptResult",
    "AttemptTarget",
    "CapacityRecoveryCoordinator",
    "CapacityRestartLoader",
    "StatusSink",
    "SwitchSink",
    "TurnResult",
    "TurnSubmission",
]

TurnKind = Literal["accepted", "duplicate", "exhausted", "needs_user", "ambiguous"]
StatusSink = Callable[[RecoveryStatus], Awaitable[None]]
SwitchSink = Callable[[FallbackTarget], Awaitable[None]]

#: A resume that failed to start is retried after this long rather than lost.
_RESUME_FAILURE_DELAY = timedelta(seconds=60)


@dataclass(frozen=True)
class TurnSubmission:
    """Everything recovery needs to know about a logical turn, captured once."""

    turn_key: str
    frontend: str
    thread_id: int
    session_id: str | None
    prompt: str
    backend: str
    model: str | None = None
    #: Explicitly authorized targets, in order. Empty means never switch.
    fallback_chain: tuple[FallbackTarget, ...] = ()

    @classmethod
    def from_pending(cls, turn: CapacityPendingTurn) -> TurnSubmission:
        chain = tuple(
            target
            for target in (FallbackTarget.from_record(rec) for rec in turn.fallback_chain)
            if target is not None
        )
        return cls(
            turn_key=turn.turn_key,
            frontend=turn.frontend,
            thread_id=turn.thread_id,
            session_id=turn.session_id,
            prompt=turn.prompt_ref,
            backend=turn.backend,
            model=turn.model,
            fallback_chain=chain,
        )


@dataclass(frozen=True)
class AttemptTarget:
    """Which backend/model the next attempt must use, and which attempt it is."""

    turn_key: str
    attempt: int
    backend: str
    model: str | None
    #: None on a fallback backend: the original session belongs to another CLI.
    session_id: str | None = None


@dataclass
class AttemptResult:
    """What one complete backend attempt produced."""

    text: str | None = None
    error: str | None = None
    exit_code: int | None = None
    status_code: int | None = None
    error_code: str | None = None
    retry_after_seconds: float | None = None
    resets_at: int | None = None
    #: Part of an answer already reached the user before the failure.
    delivered: bool = False
    session_id: str | None = None

    def failure(self, *, backend: str, model: str | None) -> BackendFailure:
        return BackendFailure(
            error=self.error,
            text=self.text,
            exit_code=self.exit_code,
            status_code=self.status_code,
            error_code=self.error_code,
            retry_after_seconds=self.retry_after_seconds,
            resets_at=self.resets_at,
            backend=backend,
            model=model or "",
        )


@dataclass(frozen=True)
class TurnResult:
    kind: TurnKind
    attempts: int
    result: AttemptResult | None = None
    outcome: CapacityOutcome | None = None
    status: RecoveryStatus | None = None
    session_id: str | None = None

    @property
    def accepted(self) -> bool:
        return self.kind == "accepted"


@dataclass
class _Live:
    submission: TurnSubmission
    status: RecoveryStatus
    since: datetime = field(default_factory=lambda: datetime.now(UTC))


def _failed(result: AttemptResult, failure: BackendFailure) -> CapacityOutcome | None:
    """The classified failure, or None when the attempt produced an answer."""
    if result.error and result.error.strip():
        return classify_failure(failure)
    if not (result.text or "").strip():
        return None
    outcome = classify_failure(failure)
    # With no error, the classifier reads the reply in strict mode and only
    # matches unmistakable provider wording; "permanent" there means "no match".
    return None if outcome.category is CapacityCategory.PERMANENT_ERROR else outcome


class CapacityRecoveryCoordinator:
    """Drive one logical turn through attempts, waits, switches and acceptance."""

    def __init__(
        self,
        *,
        policy: RecoveryPolicy | None = None,
        store: CapacityRecoveryRepository | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] | None = None,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.policy = policy or RecoveryPolicy.from_env()
        self._store = store
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._rng = rng
        self._live: dict[str, _Live] = {}
        # In-memory guards for callers without a store (tests, ad-hoc runs).
        self._running: set[str] = set()
        self._accepted: set[str] = set()

    @property
    def store(self) -> CapacityRecoveryRepository | None:
        return self._store

    async def wait(self, seconds: float) -> None:
        """Sleep through the coordinator's clock, so callers that drive their
        own attempts (``/gowork``) wait the way an interactive turn does."""
        if seconds > 0:
            await self._sleep(seconds)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Live recovery state per turn key: category and timing, never prompts."""
        return {
            key: {
                **live.status.public_view(),
                "turn_key": key,
                "frontend": live.submission.frontend,
                "thread_id": live.submission.thread_id,
                "since": live.since.isoformat(),
            }
            for key, live in self._live.items()
        }

    async def run_turn(
        self,
        submission: TurnSubmission,
        attempt: Callable[[AttemptTarget], Awaitable[AttemptResult]],
        *,
        on_status: StatusSink | None = None,
        on_switch: SwitchSink | None = None,
        claim_token: str | None = None,
    ) -> TurnResult:
        """Run ``attempt`` until one result is accepted or recovery must stop.

        ``claim_token`` continues a turn the restart loader already claimed;
        otherwise the coordinator claims the turn itself and a second caller
        for the same key gets ``duplicate`` without a model call.
        """
        key = submission.turn_key
        token = claim_token or uuid.uuid4().hex
        tracker = RecoveryTracker(
            self.policy,
            backend=submission.backend,
            model=submission.model,
            chain=submission.fallback_chain,
            now=self._now,
        )
        admitted, row = await self._enter(submission, token, resumed=claim_token is not None)
        if not admitted:
            return TurnResult("duplicate", 0)
        if row is not None:
            tracker.attempts = max(0, row.attempt - 1)
            tracker.started_at = row.created_at
        try:
            while True:
                target = AttemptTarget(
                    turn_key=key,
                    attempt=tracker.attempts + 1,
                    backend=tracker.current.backend,
                    model=tracker.current.model,
                    session_id=submission.session_id if tracker.chain_index < 0 else None,
                )
                result = await attempt(target)
                outcome = _failed(
                    result, result.failure(backend=target.backend, model=target.model)
                )
                if outcome is None:
                    return await self._accept(key, token, target, result, tracker, on_status)

                decision = tracker.next(outcome, output_delivered=result.delivered, rng=self._rng)
                status = decision.status
                self._live[key] = _Live(submission, status, since=self._now())
                logger.info(
                    "capacity recovery turn=%s thread=%s %s",
                    key,
                    submission.thread_id,
                    status.public_view(),
                )
                if on_status is not None:
                    await on_status(status)

                if decision.action == "retry":
                    ok = await self._wait_and_reclaim(
                        key, token, status.next_attempt_at or self._now(), decision.delay_seconds
                    )
                    if ok is not True:
                        return TurnResult(ok, tracker.attempts, result, outcome, status)
                    continue
                if decision.action == "fallback" and decision.target is not None:
                    if on_switch is not None:
                        await on_switch(decision.target)
                    ok = await self._wait_and_reclaim(key, token, self._now(), 0.0)
                    if ok is not True:
                        return TurnResult(ok, tracker.attempts, result, outcome, status)
                    continue

                await self._park(key, token)
                kind: TurnKind = (
                    "exhausted"
                    if decision.action == "exhausted"
                    else "ambiguous"
                    if decision.action == "ambiguous"
                    else "needs_user"
                )
                return TurnResult(kind, tracker.attempts, result, outcome, status)
        finally:
            self._live.pop(key, None)
            self._running.discard(key)

    # -- store interactions -------------------------------------------------

    async def _enter(
        self, submission: TurnSubmission, token: str, *, resumed: bool
    ) -> tuple[bool, CapacityPendingTurn | None]:
        """Claim the turn. ``(False, None)`` means someone else owns or finished it."""
        if self._store is None:
            if submission.turn_key in self._accepted or submission.turn_key in self._running:
                return False, None
            self._running.add(submission.turn_key)
            return True, None
        now = self._now()
        _created, row = await self._store.create_pending(
            CapacityPendingTurnCreate(
                turn_key=submission.turn_key,
                frontend=submission.frontend,
                thread_id=submission.thread_id,
                session_id=submission.session_id,
                prompt_ref=submission.prompt,
                backend=submission.backend,
                model=submission.model,
                fallback_chain=[t.to_record() for t in submission.fallback_chain],
                next_attempt_at=now,
                expires_at=now + timedelta(seconds=self.policy.max_total_seconds),
            ),
            now=now,
        )
        if resumed:
            return (True, row) if row.claim_token == token else (False, None)
        claimed = await self._store.claim_due(submission.turn_key, claim_token=token, now=now)
        return (claimed is not None), claimed

    async def _accept(
        self,
        key: str,
        token: str,
        target: AttemptTarget,
        result: AttemptResult,
        tracker: RecoveryTracker,
        on_status: StatusSink | None,
    ) -> TurnResult:
        if self._store is None:
            if key in self._accepted:
                return TurnResult("duplicate", target.attempt, session_id=result.session_id)
            self._accepted.add(key)
        else:
            accepted = await self._store.accept_once(
                key, accepted_result_ref=f"attempt:{target.attempt}", now=self._now()
            )
            if accepted is None:
                logger.info("capacity recovery turn=%s: a result was already accepted", key)
                return TurnResult("duplicate", target.attempt, session_id=result.session_id)
        if tracker.attempts and on_status is not None:
            await on_status(
                RecoveryStatus(
                    phase=RecoveryPhase.ACCEPTED,
                    category=None,
                    backend=target.backend,
                    model=target.model or "",
                    attempt=target.attempt,
                    max_attempts=self.policy.max_attempts,
                )
            )
        return TurnResult("accepted", target.attempt, result, session_id=result.session_id)

    async def _wait_and_reclaim(
        self, key: str, token: str, next_attempt_at: datetime, delay: float
    ) -> TurnKind | Literal[True]:
        """Release the claim for the wait, sleep, then claim the next attempt."""
        if self._store is not None:
            await self._store.schedule_retry(
                key, claim_token=token, next_attempt_at=next_attempt_at, now=self._now()
            )
        if delay > 0:
            await self._sleep(delay)
        if self._store is None:
            return True
        claimed = await self._store.claim_due(key, claim_token=token, now=self._now())
        if claimed is not None:
            return True
        row = await self._store.get(key)
        if row is not None and row.state is PendingTurnState.ACCEPTED:
            return "duplicate"
        if row is not None and row.claim_token not in (None, token):
            return "duplicate"
        return "exhausted"

    async def _park(self, key: str, token: str) -> None:
        """Leave a stopped turn readable but never due: the record is kept, not run."""
        if self._store is None:
            return
        row = await self._store.get(key)
        if row is None:
            return
        await self._store.schedule_retry(
            key, claim_token=token, next_attempt_at=row.expires_at, now=self._now()
        )


class CapacityRestartLoader:
    """After a restart, claim due pending turns and hand each to ``resume`` once.

    ``resume`` is expected to run the turn through the normal path — for
    Discord that is ``run_claude_with_config``, which reacquires relay
    admission before any backend is spawned. Two loaders over one database
    cannot resume the same turn: the conditional claim has one winner.
    """

    def __init__(
        self,
        repo: CapacityRecoveryRepository,
        resume: Callable[[CapacityPendingTurn], Awaitable[None]],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._resume = resume
        self._now = now or (lambda: datetime.now(UTC))

    async def load_due(self, *, limit: int = 50) -> list[str]:
        """Claim every due turn this loader wins and resume it. Returns the keys."""
        now = self._now()
        expired = await self._repo.expire_before(now)
        if expired:
            logger.info("capacity recovery: %d pending turn(s) expired before restart", expired)
        resumed: list[str] = []
        for turn in await self._repo.reload_due(now=now, limit=limit):
            claimed = await self._repo.claim_due(
                turn.turn_key, claim_token=uuid.uuid4().hex, now=now
            )
            if claimed is None:
                continue
            try:
                await self._resume(claimed)
            except Exception:
                logger.exception(
                    "capacity recovery: resuming turn %s failed; retrying later", turn.turn_key
                )
                if claimed.claim_token is not None:
                    await self._repo.schedule_retry(
                        turn.turn_key,
                        claim_token=claimed.claim_token,
                        next_attempt_at=now + _RESUME_FAILURE_DELAY,
                        now=now,
                    )
                continue
            resumed.append(turn.turn_key)
        return resumed
