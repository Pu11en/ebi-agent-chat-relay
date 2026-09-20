"""Capacity classification contract — one typed outcome every surface can read.

These tests pin the *contract* (categories, retryability, the split between safe
user wording and raw diagnostics), not retry policy: nothing here schedules a
retry, because the coordinator does not exist yet.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from claude_code_core.capacity import (
    BackendFailure,
    CapacityCategory,
    CapacityOutcome,
    classify_failure,
    relay_queued_outcome,
)

RETRYABLE_CATEGORIES = {
    CapacityCategory.RELAY_QUEUED,
    CapacityCategory.MODEL_SATURATED,
    CapacityCategory.PROVIDER_RATE_LIMITED,
}


# --- relay queueing is its own state, never a provider failure ----------------


def test_relay_queueing_is_a_separate_category_from_provider_saturation() -> None:
    outcome = relay_queued_outcome(backend="claude", model="opus")
    assert outcome.category is CapacityCategory.RELAY_QUEUED
    assert outcome.retryable is True
    assert outcome.is_provider_failure is False
    assert "capacity" not in outcome.user_detail.lower()


def test_classifier_never_reports_relay_queueing() -> None:
    """Only the local admission path may produce the relay state."""
    for error in ("model at capacity", "429 too many requests", "boom", ""):
        assert classify_failure(BackendFailure(error=error)).category is not (
            CapacityCategory.RELAY_QUEUED
        )


# --- temporary model saturation -----------------------------------------------


def test_observed_model_at_capacity_wording_is_temporary_saturation() -> None:
    outcome = classify_failure(
        BackendFailure(error="model at capacity", backend="claude", model="opus")
    )
    assert outcome.category is CapacityCategory.MODEL_SATURATED
    assert outcome.retryable is True
    assert outcome.needs_user_action is False
    assert "opus" in outcome.user_detail


def test_structured_overloaded_code_beats_any_phrase_matching() -> None:
    outcome = classify_failure(
        BackendFailure(error_code="overloaded_error", error="unhelpful stack trace")
    )
    assert outcome.category is CapacityCategory.MODEL_SATURATED


def test_status_529_is_saturation_when_the_body_says_nothing_useful() -> None:
    outcome = classify_failure(BackendFailure(status_code=529, error="TypeError: bad input"))
    assert outcome.category is CapacityCategory.MODEL_SATURATED


# --- provider rate limiting ----------------------------------------------------


def test_status_429_is_provider_rate_limiting_with_the_structured_hint() -> None:
    outcome = classify_failure(BackendFailure(status_code=429, retry_after_seconds=42))
    assert outcome.category is CapacityCategory.PROVIDER_RATE_LIMITED
    assert outcome.retryable is True
    assert outcome.retry_after_seconds == 42


def test_retry_after_is_read_from_the_error_text_when_unstructured() -> None:
    outcome = classify_failure(
        BackendFailure(error="Rate limit exceeded. Please try again in 2 minutes.")
    )
    assert outcome.category is CapacityCategory.PROVIDER_RATE_LIMITED
    assert outcome.retry_after_seconds == 120


def test_absurd_or_negative_retry_hints_are_dropped() -> None:
    outcome = classify_failure(BackendFailure(error="rate limit", retry_after_seconds=-5))
    assert outcome.retry_after_seconds is None
    far = classify_failure(BackendFailure(error="rate limit", retry_after_seconds=999_999))
    assert far.retry_after_seconds is None


# --- subscription / quota exhaustion -------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        "insufficient_quota",
        "You have exceeded your current quota",
        "You've hit your 5-hour limit",
        "Weekly limit reached for this subscription",
        "Your credit balance is too low",
    ],
)
def test_quota_exhaustion_is_not_retried_as_saturation(error: str) -> None:
    outcome = classify_failure(BackendFailure(error=error))
    assert outcome.category is CapacityCategory.QUOTA_EXHAUSTED
    assert outcome.retryable is False
    assert outcome.needs_user_action is True


def test_quota_wording_wins_over_a_bare_429_status() -> None:
    outcome = classify_failure(
        BackendFailure(status_code=429, error="You've hit your usage limit", resets_at=1_900_000)
    )
    assert outcome.category is CapacityCategory.QUOTA_EXHAUSTED
    assert outcome.resets_at == 1_900_000


# --- authentication -------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        BackendFailure(error="Invalid API key provided"),
        BackendFailure(error="authentication failed"),
        BackendFailure(error="OAuth token has expired, please run /login"),
        BackendFailure(status_code=401),
        BackendFailure(error_code="invalid_api_key"),
    ],
)
def test_authentication_failures_stop_automatic_recovery(failure: BackendFailure) -> None:
    outcome = classify_failure(failure)
    assert outcome.category is CapacityCategory.AUTHENTICATION_FAILED
    assert outcome.retryable is False
    assert outcome.needs_user_action is True


# --- unknown errors stay terminal ------------------------------------------------


def test_unknown_error_is_permanent_and_keeps_the_original_diagnostic() -> None:
    outcome = classify_failure(BackendFailure(error="TypeError: cannot read property 'x'"))
    assert outcome.category is CapacityCategory.PERMANENT_ERROR
    assert outcome.retryable is False
    assert "TypeError" in outcome.diagnostic


def test_an_attempt_that_ended_with_no_signal_at_all_is_permanent() -> None:
    outcome = classify_failure(BackendFailure())
    assert outcome.category is CapacityCategory.PERMANENT_ERROR
    assert outcome.retryable is False


@pytest.mark.parametrize(
    "reply",
    [
        "I added rate limiting to the API and wrote a test for the 429 path.",
        "queued — waiting for capacity",
        "The recursion limit exceeded message is expected here.",
    ],
)
def test_a_worker_writing_about_limits_is_not_a_capacity_failure(reply: str) -> None:
    """Only the provider's own wording counts; a reply about limits is real work."""
    outcome = classify_failure(BackendFailure(text=reply))
    assert outcome.category is CapacityCategory.PERMANENT_ERROR


def test_provider_wording_in_the_reply_is_still_classified_when_there_is_no_error() -> None:
    outcome = classify_failure(BackendFailure(text="The model is at capacity right now."))
    assert outcome.category is CapacityCategory.MODEL_SATURATED


# --- safe user detail vs raw diagnostics ------------------------------------------


def test_safe_user_detail_never_carries_the_raw_diagnostic() -> None:
    secret = "sk-ant-notarealkey-0000"
    outcome = classify_failure(BackendFailure(error=f"Invalid API key: {secret}"))
    assert secret not in outcome.user_detail
    assert secret in outcome.diagnostic


def test_diagnostics_are_bounded() -> None:
    outcome = classify_failure(BackendFailure(error="x" * 10_000))
    assert 0 < len(outcome.diagnostic) <= 2000


# --- the contract itself -------------------------------------------------------------


def test_retryability_is_derived_from_the_category_for_every_category() -> None:
    for category in CapacityCategory:
        outcome = CapacityOutcome(category=category, user_detail="d")
        assert outcome.retryable is (category in RETRYABLE_CATEGORIES)
        assert outcome.needs_user_action is (category not in RETRYABLE_CATEGORIES)


def test_the_outcome_is_surface_neutral() -> None:
    fields = {f.name for f in dataclasses.fields(CapacityOutcome)}
    assert fields == {
        "category",
        "user_detail",
        "diagnostic",
        "retry_after_seconds",
        "resets_at",
        "backend",
        "model",
    }
    source = (Path(__file__).parent.parent / "claude_code_core" / "capacity.py").read_text(
        encoding="utf-8"
    )
    assert "discord" not in source.lower()


def test_the_module_exposes_no_retry_orchestration_yet() -> None:
    from claude_code_core import capacity

    assert set(capacity.__all__) == {
        "BackendFailure",
        "CapacityCategory",
        "CapacityOutcome",
        "classify_failure",
        "relay_queued_outcome",
    }


def test_outcomes_are_immutable() -> None:
    outcome = relay_queued_outcome()
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.category = CapacityCategory.PERMANENT_ERROR  # type: ignore[misc]
