"""Retry policy for model capacity recovery: bounded, hinted, jittered, deadlined.

These are the rules the shared coordinator applies after a classified attempt.
They are pure: no clock, no sleep, no backend. Randomness and time are passed in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from claude_code_core.capacity import BackendFailure, CapacityCategory, classify_failure
from claude_code_core.capacity_policy import (
    FallbackTarget,
    RecoveryPhase,
    RecoveryPolicy,
    RecoveryTracker,
    parse_fallback_chain,
    retry_delay,
)

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
POLICY = RecoveryPolicy(
    min_delay_seconds=5,
    base_delay_seconds=30,
    multiplier=2,
    max_delay_seconds=600,
    jitter_ratio=0.2,
    max_attempts=6,
    max_total_seconds=3600,
    fallback_after_attempts=2,
)


def saturated(model: str = "opus") -> object:
    return classify_failure(BackendFailure(error="Model is at capacity", model=model))


def rate_limited(retry_after: float | None = None) -> object:
    return classify_failure(
        BackendFailure(error="429 rate limit", retry_after_seconds=retry_after, model="opus")
    )


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_delay_grows_exponentially_and_is_capped() -> None:
    delays = [
        retry_delay(POLICY, attempt=n, retry_after=None, rng=lambda: 0.0) for n in range(1, 8)
    ]
    assert delays == [30, 60, 120, 240, 480, 600, 600]


def test_retry_after_hint_wins_and_is_clamped_to_the_configured_bounds() -> None:
    assert retry_delay(POLICY, attempt=1, retry_after=90, rng=lambda: 0.0) == 90
    assert retry_delay(POLICY, attempt=1, retry_after=1, rng=lambda: 0.0) == 5
    assert retry_delay(POLICY, attempt=1, retry_after=5000, rng=lambda: 0.0) == 600


def test_jitter_stays_within_its_ratio_and_never_exceeds_the_maximum() -> None:
    low = retry_delay(POLICY, attempt=2, retry_after=None, rng=lambda: 0.0)
    high = retry_delay(POLICY, attempt=2, retry_after=None, rng=lambda: 1.0)
    assert low == 60
    assert high == pytest.approx(72)
    assert retry_delay(POLICY, attempt=6, retry_after=None, rng=lambda: 1.0) == 600


def test_the_first_delay_never_drops_below_the_minimum() -> None:
    tiny = RecoveryPolicy(min_delay_seconds=10, base_delay_seconds=1)
    assert retry_delay(tiny, attempt=1, retry_after=None, rng=lambda: 0.0) == 10


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_temporary_saturation_is_retried_with_a_scheduled_next_attempt() -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    decision = tracker.next(saturated(), rng=lambda: 0.0)
    assert decision.action == "retry"
    assert decision.delay_seconds == 30
    assert decision.status.phase is RecoveryPhase.RETRYING
    assert decision.status.attempt == 1
    assert decision.status.max_attempts == 6
    assert decision.status.next_attempt_at == NOW + timedelta(seconds=30)


def test_the_attempt_limit_ends_automatic_recovery() -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    actions = [tracker.next(saturated(), rng=lambda: 0.0).action for _ in range(6)]
    assert actions[-1] == "exhausted"
    assert "retry" in actions
    assert tracker.next(saturated(), rng=lambda: 0.0).status.phase is RecoveryPhase.EXHAUSTED


def test_the_total_recovery_window_ends_automatic_recovery() -> None:
    clock = [NOW]
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: clock[0])
    assert tracker.next(saturated(), rng=lambda: 0.0).action == "retry"
    clock[0] = NOW + timedelta(seconds=3601)
    decision = tracker.next(saturated(), rng=lambda: 0.0)
    assert decision.action == "exhausted"
    assert "next" in decision.status.line.lower()


def test_a_scheduled_retry_never_reaches_past_the_recovery_deadline() -> None:
    clock = [NOW]
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: clock[0])
    assert tracker.next(saturated(), rng=lambda: 0.0).action == "retry"
    clock[0] = NOW + timedelta(seconds=3590)
    decision = tracker.next(rate_limited(retry_after=500), rng=lambda: 0.0)
    assert decision.action == "retry"
    assert decision.delay_seconds <= 10


def test_partial_output_makes_a_retryable_failure_ambiguous() -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    decision = tracker.next(saturated(), output_delivered=True, rng=lambda: 0.0)
    assert decision.action == "ambiguous"
    assert decision.status.phase is RecoveryPhase.AMBIGUOUS
    assert "already" in decision.status.line.lower()


@pytest.mark.parametrize(
    ("error", "phase"),
    [
        ("You've hit your usage limit", RecoveryPhase.QUOTA),
        ("invalid api key", RecoveryPhase.AUTHENTICATION),
        ("TypeError: cannot read property", RecoveryPhase.PERMANENT),
    ],
)
def test_non_recoverable_outcomes_stop_immediately(error: str, phase: RecoveryPhase) -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    decision = tracker.next(
        classify_failure(BackendFailure(error=error, model="opus")), rng=lambda: 0.0
    )
    assert decision.action == "stop"
    assert decision.status.phase is phase
    assert tracker.attempts == 0


# ---------------------------------------------------------------------------
# Fallback chains (explicit authority only)
# ---------------------------------------------------------------------------


def test_fallback_is_taken_only_after_the_configured_number_of_attempts() -> None:
    chain = (FallbackTarget("codex", "gpt-5.5", authority="task"),)
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", chain=chain, now=lambda: NOW)
    first = tracker.next(saturated(), rng=lambda: 0.0)
    second = tracker.next(saturated(), rng=lambda: 0.0)
    assert first.action == "retry"
    assert second.action == "fallback"
    assert second.target == chain[0]
    assert second.status.phase is RecoveryPhase.FALLBACK
    assert "codex" in second.status.line
    assert tracker.current == chain[0]


def test_without_a_chain_no_provider_is_ever_selected() -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    actions = {tracker.next(saturated(), rng=lambda: 0.0).action for _ in range(8)}
    assert actions <= {"retry", "exhausted"}
    assert (tracker.current.backend, tracker.current.model) == ("claude", "opus")


def test_a_saturated_fallback_advances_only_to_another_authorized_target() -> None:
    chain = (
        FallbackTarget("codex", "gpt-5.5", authority="session"),
        FallbackTarget("local", "qwen", authority="computer"),
    )
    policy = RecoveryPolicy(fallback_after_attempts=1, max_attempts=10)
    tracker = RecoveryTracker(policy, backend="claude", model="opus", chain=chain, now=lambda: NOW)
    first = tracker.next(saturated(), rng=lambda: 0.0)
    second = tracker.next(saturated("gpt-5.5"), rng=lambda: 0.0)
    third = tracker.next(saturated("qwen"), rng=lambda: 0.0)
    assert (first.action, first.target) == ("fallback", chain[0])
    assert (second.action, second.target) == ("fallback", chain[1])
    assert third.action == "retry"
    assert third.status.phase is RecoveryPhase.RETRYING
    assert tracker.current == chain[1]


def test_parse_fallback_chain_reads_backend_and_model_pairs() -> None:
    chain = parse_fallback_chain("codex:gpt-5.5, claude , local:qwen3", authority="computer")
    assert chain == (
        FallbackTarget("codex", "gpt-5.5", authority="computer"),
        FallbackTarget("claude", None, authority="computer"),
        FallbackTarget("local", "qwen3", authority="computer"),
    )
    assert parse_fallback_chain(None) == ()
    assert parse_fallback_chain("  ") == ()


def test_fallback_targets_round_trip_through_the_stored_record_shape() -> None:
    target = FallbackTarget("codex", "gpt-5.5", authority="task")
    record = target.to_record()
    assert record == {"backend": "codex", "model": "gpt-5.5", "authority": "task"}
    assert FallbackTarget.from_record(record) == target
    assert FallbackTarget.from_record({"model": "x"}) is None


# ---------------------------------------------------------------------------
# Status wording: every phase reads differently
# ---------------------------------------------------------------------------


def test_status_lines_are_distinct_and_name_the_category() -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    retry = tracker.next(saturated(), rng=lambda: 0.0).status
    limited = (
        RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
        .next(rate_limited(), rng=lambda: 0.0)
        .status
    )
    assert "at capacity" in retry.line
    assert "1 of 6" in retry.line
    assert "limiting" in limited.line
    assert retry.line != limited.line
    assert retry.category is CapacityCategory.MODEL_SATURATED
    assert limited.category is CapacityCategory.PROVIDER_RATE_LIMITED


def test_status_public_view_excludes_diagnostics_and_prompts() -> None:
    tracker = RecoveryTracker(POLICY, backend="claude", model="opus", now=lambda: NOW)
    outcome = classify_failure(
        BackendFailure(error="Model is at capacity: sk-ant-SECRET", model="opus")
    )
    view = tracker.next(outcome, rng=lambda: 0.0).status.public_view()
    assert view["phase"] == "retrying"
    assert view["category"] == "model_saturated"
    assert view["attempt"] == 1
    assert view["next_attempt_at"] == (NOW + timedelta(seconds=30)).isoformat()
    assert "SECRET" not in repr(view)
    assert "prompt" not in view


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_policy_reads_overrides_from_the_environment_with_safe_defaults() -> None:
    policy = RecoveryPolicy.from_env({})
    assert policy == RecoveryPolicy()
    assert policy.enabled

    custom = RecoveryPolicy.from_env(
        {
            "CCDB_CAPACITY_RETRY_BASE_SECONDS": "10",
            "CCDB_CAPACITY_RETRY_MAX_SECONDS": "40",
            "CCDB_CAPACITY_RETRY_MAX_ATTEMPTS": "3",
            "CCDB_CAPACITY_RETRY_WINDOW_SECONDS": "120",
            "CCDB_CAPACITY_FALLBACK_AFTER_ATTEMPTS": "1",
            "CCDB_CAPACITY_RETRY": "0",
        }
    )
    assert (custom.base_delay_seconds, custom.max_delay_seconds) == (10, 40)
    assert (custom.max_attempts, custom.max_total_seconds) == (3, 120)
    assert custom.fallback_after_attempts == 1
    assert not custom.enabled


def test_nonsense_environment_values_fall_back_to_defaults() -> None:
    policy = RecoveryPolicy.from_env({"CCDB_CAPACITY_RETRY_MAX_ATTEMPTS": "lots"})
    assert policy.max_attempts == RecoveryPolicy().max_attempts
