"""What to do after a classified capacity outcome: wait, switch, stop, or ask.

:mod:`claude_code_core.capacity` says what happened. This module says what
happens next, and it is deliberately pure so every surface — interactive chat,
``/gowork``, a headless worker — applies the same rules: bounded exponential
backoff with the provider's own hint winning when it gives one, a total
recovery window, and a fallback chain that was captured explicitly at
submission. No clock, no sleep, no backend: time and randomness are passed in.

A :class:`RecoveryTracker` carries the per-turn state (attempts so far, when
recovery began, which target is current) and produces one
:class:`RecoveryDecision` per failed attempt. The decision's
:class:`RecoveryStatus` is the one line a surface shows and the one record an
API exposes — it never carries a prompt or a raw provider diagnostic.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from .capacity import CapacityCategory, CapacityOutcome

__all__ = [
    "FallbackTarget",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryPhase",
    "RecoveryPolicy",
    "RecoveryStatus",
    "RecoveryTracker",
    "parse_fallback_chain",
    "retry_delay",
]

RecoveryAction = Literal["retry", "fallback", "exhausted", "stop", "ambiguous"]

_ENV_PREFIX = "CCDB_CAPACITY_"


@dataclass(frozen=True)
class FallbackTarget:
    """One explicitly authorized place a turn may move to.

    ``authority`` names who allowed it — ``task``, ``session`` or ``computer`` —
    so a status line can say why the switch was permitted. Recovery never
    invents a target; it only walks the chain it was handed at submission.
    """

    backend: str
    model: str | None = None
    authority: str = "computer"

    @property
    def label(self) -> str:
        return " · ".join(part for part in (self.backend, self.model) if part)

    def to_record(self) -> dict[str, object]:
        return {"backend": self.backend, "model": self.model, "authority": self.authority}

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> FallbackTarget | None:
        backend = record.get("backend")
        if not isinstance(backend, str) or not backend.strip():
            return None
        model = record.get("model")
        authority = record.get("authority")
        return cls(
            backend=backend.strip(),
            model=model.strip() if isinstance(model, str) and model.strip() else None,
            authority=authority if isinstance(authority, str) and authority else "computer",
        )


def parse_fallback_chain(
    text: str | None, *, authority: str = "computer"
) -> tuple[FallbackTarget, ...]:
    """Read ``"codex:gpt-5.5, claude"`` into targets. Blank or missing means no chain."""
    if not text or not text.strip():
        return ()
    targets: list[FallbackTarget] = []
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            continue
        backend, _, model = item.partition(":")
        backend = backend.strip()
        if not backend:
            continue
        targets.append(FallbackTarget(backend, model.strip() or None, authority=authority))
    return tuple(targets)


@dataclass(frozen=True)
class RecoveryPolicy:
    """Bounds for automatic recovery. Defaults are ccdb's; env overrides them."""

    #: A provider hint below this is rounded up: retrying faster gains nothing.
    min_delay_seconds: float = 5.0
    base_delay_seconds: float = 30.0
    multiplier: float = 2.0
    max_delay_seconds: float = 600.0
    #: Added on top of the computed delay, as a fraction of it, to spread retries.
    jitter_ratio: float = 0.2
    max_attempts: int = 6
    #: Total time a turn may spend recovering before it asks for a decision.
    max_total_seconds: float = 3600.0
    #: Attempts on one target before the next authorized target is tried.
    fallback_after_attempts: int = 2
    #: ``False`` stops new recovery scheduling (rollback) without touching records.
    enabled: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RecoveryPolicy:
        source = os.environ if env is None else env
        defaults = cls()

        def number(key: str, fallback: float) -> float:
            raw = source.get(_ENV_PREFIX + key, "")
            try:
                value = float(raw)
            except ValueError:
                return fallback
            return value if value > 0 else fallback

        base = number("RETRY_BASE_SECONDS", defaults.base_delay_seconds)
        maximum = max(number("RETRY_MAX_SECONDS", defaults.max_delay_seconds), base)
        return cls(
            min_delay_seconds=min(number("RETRY_MIN_SECONDS", defaults.min_delay_seconds), base),
            base_delay_seconds=base,
            max_delay_seconds=maximum,
            max_attempts=int(number("RETRY_MAX_ATTEMPTS", defaults.max_attempts)),
            max_total_seconds=number("RETRY_WINDOW_SECONDS", defaults.max_total_seconds),
            fallback_after_attempts=int(
                number("FALLBACK_AFTER_ATTEMPTS", defaults.fallback_after_attempts)
            ),
            enabled=source.get(_ENV_PREFIX + "RETRY", "1").strip().lower()
            not in {"0", "false", "no", "off"},
        )


def retry_delay(
    policy: RecoveryPolicy,
    *,
    attempt: int,
    retry_after: float | None,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before attempt ``attempt + 1``.

    A provider hint wins as-is (clamped); otherwise the delay doubles per failed
    attempt with a little jitter so a fleet of waiting turns does not retry in
    lock-step. Never above ``max_delay_seconds``, never below ``min_delay_seconds``.
    """
    if retry_after is not None and retry_after > 0:
        return _clamp(policy, float(retry_after))
    exponent = max(0, attempt - 1)
    base = _clamp(policy, policy.base_delay_seconds * (policy.multiplier**exponent))
    jitter = base * policy.jitter_ratio * max(0.0, min(1.0, rng()))
    return _clamp(policy, base + jitter)


def _clamp(policy: RecoveryPolicy, seconds: float) -> float:
    return max(policy.min_delay_seconds, min(policy.max_delay_seconds, seconds))


class RecoveryPhase(StrEnum):
    """Where a turn is in recovery. Each phase has its own wording."""

    RELAY_QUEUED = "relay_queued"
    WAITING = "waiting"
    RETRYING = "retrying"
    FALLBACK = "fallback"
    EXHAUSTED = "exhausted"
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    PERMANENT = "permanent"
    AMBIGUOUS = "ambiguous"
    ACCEPTED = "accepted"


_TERMINAL_PHASES = {
    CapacityCategory.QUOTA_EXHAUSTED: RecoveryPhase.QUOTA,
    CapacityCategory.AUTHENTICATION_FAILED: RecoveryPhase.AUTHENTICATION,
    CapacityCategory.PERMANENT_ERROR: RecoveryPhase.PERMANENT,
}


@dataclass(frozen=True)
class RecoveryStatus:
    """The current recovery state of one logical turn, safe to show anywhere."""

    phase: RecoveryPhase
    category: CapacityCategory | None
    backend: str
    model: str
    attempt: int = 0
    max_attempts: int = 0
    detail: str = ""
    next_attempt_at: datetime | None = None
    next_attempt_in: float | None = None
    fallback: FallbackTarget | None = None
    resets_at: int | None = None

    @property
    def line(self) -> str:
        """One human line naming the category and what happens next."""
        model = self.model or "The selected model"
        count = f"attempt {self.attempt} of {self.max_attempts}"
        wait = f"next try in {_human_seconds(self.next_attempt_in)}"
        phase = self.phase
        if phase is RecoveryPhase.RELAY_QUEUED:
            return "⏳ Queued — waiting for a free run slot on this computer."
        if phase is RecoveryPhase.WAITING:
            return f"⏳ Waiting: {self.detail} ({count})"
        if phase is RecoveryPhase.RETRYING:
            return f"⏳ Waiting to retry: {self.detail} ({count}, {wait})"
        if phase is RecoveryPhase.FALLBACK:
            target = self.fallback.label if self.fallback else "the fallback"
            why = f", allowed by the {self.fallback.authority}" if self.fallback else ""
            return f"🔀 {self.detail} Switching to {target}{why} ({count})."
        if phase is RecoveryPhase.EXHAUSTED:
            return (
                f"🛑 Gave up waiting for {model} after {self.attempt} attempts. "
                "The request is kept. Next: send it again later, or switch model/backend."
            )
        if phase is RecoveryPhase.AUTHENTICATION:
            return (
                f"🔑 {self.detail} Not retrying. Next: repair the provider login for "
                f"{self.backend or 'this backend'} on this computer."
            )
        if phase is RecoveryPhase.QUOTA:
            reset = f" It resets at <t:{self.resets_at}:t>." if self.resets_at else ""
            return (
                f"⛔ {self.detail}{reset} Not retrying. Next: wait for the reset, "
                "or switch to a backend you already authorized."
            )
        if phase is RecoveryPhase.AMBIGUOUS:
            return (
                f"⚠️ {self.detail} Part of an answer was already delivered, so it was "
                "not retried automatically. Next: say whether to try again."
            )
        if phase is RecoveryPhase.ACCEPTED:
            return f"✅ {model} answered after {self.attempt} attempt(s)."
        return f"💥 {self.detail} Next: check the logs on this computer and send it again."

    def public_view(self) -> dict[str, object]:
        """The API/log shape: category and timing, never prompt or diagnostic."""
        return {
            "phase": self.phase.value,
            "category": self.category.value if self.category else None,
            "backend": self.backend,
            "model": self.model,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "fallback": self.fallback.to_record() if self.fallback else None,
        }


def _human_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "a moment"
    whole = int(round(seconds))
    if whole < 60:
        return f"{whole}s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes}m" if rest == 0 else f"{minutes}m {rest}s"


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    status: RecoveryStatus
    delay_seconds: float = 0.0
    target: FallbackTarget | None = None


@dataclass
class RecoveryTracker:
    """Per-turn recovery state; one :meth:`next` per failed attempt.

    ``chain`` is the fallback chain captured when the turn was submitted. The
    tracker walks it in order and never steps outside it.
    """

    policy: RecoveryPolicy
    backend: str
    model: str | None = None
    chain: tuple[FallbackTarget, ...] = ()
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    attempts: int = 0
    started_at: datetime | None = None
    #: Index into ``chain`` of the current target; -1 is the original.
    chain_index: int = -1
    #: Failed attempts on the current target only.
    attempts_on_target: int = 0

    @property
    def current(self) -> FallbackTarget:
        if 0 <= self.chain_index < len(self.chain):
            return self.chain[self.chain_index]
        return FallbackTarget(self.backend, self.model, authority="submission")

    @property
    def deadline(self) -> datetime | None:
        if self.started_at is None:
            return None
        return self.started_at + timedelta(seconds=self.policy.max_total_seconds)

    def next(
        self,
        outcome: CapacityOutcome,
        *,
        output_delivered: bool = False,
        rng: Callable[[], float] = random.random,
    ) -> RecoveryDecision:
        """Decide after one failed attempt. Non-retryable outcomes stop at once."""
        now = self.now()
        target = self.current

        def status(phase: RecoveryPhase, **extra: object) -> RecoveryStatus:
            return RecoveryStatus(
                phase=phase,
                category=outcome.category,
                backend=target.backend,
                model=target.model or outcome.model,
                detail=outcome.user_detail,
                attempt=self.attempts,
                max_attempts=self.policy.max_attempts,
                resets_at=outcome.resets_at,
                **extra,  # type: ignore[arg-type]
            )

        if not outcome.retryable:
            phase = _TERMINAL_PHASES.get(outcome.category, RecoveryPhase.PERMANENT)
            return RecoveryDecision("stop", status(phase))

        if self.started_at is None:
            self.started_at = now
        self.attempts += 1
        self.attempts_on_target += 1
        attempt = self.attempts

        if output_delivered:
            return RecoveryDecision("ambiguous", status(RecoveryPhase.AMBIGUOUS))

        deadline = self.deadline
        remaining = (deadline - now).total_seconds() if deadline is not None else None
        budget_spent = attempt >= self.policy.max_attempts or (
            remaining is not None and remaining <= 0
        )
        if not self.policy.enabled or budget_spent:
            return RecoveryDecision("exhausted", status(RecoveryPhase.EXHAUSTED))

        next_index = self.chain_index + 1
        if self.attempts_on_target >= self.policy.fallback_after_attempts and next_index < len(
            self.chain
        ):
            self.chain_index = next_index
            self.attempts_on_target = 0
            fallback = self.chain[next_index]
            return RecoveryDecision(
                "fallback", status(RecoveryPhase.FALLBACK, fallback=fallback), target=fallback
            )

        delay = retry_delay(
            self.policy, attempt=attempt, retry_after=outcome.retry_after_seconds, rng=rng
        )
        if remaining is not None:
            delay = max(0.0, min(delay, remaining))
        retrying = status(
            RecoveryPhase.RETRYING,
            next_attempt_at=now + timedelta(seconds=delay),
            next_attempt_in=delay,
        )
        return RecoveryDecision("retry", retrying, delay_seconds=delay)
