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


# --- per-backend phrase fixtures ----------------------------------------------
#
# Every supported harness reports the same six situations in its own words. The
# fixtures below are the wording actually observed from each one; the point of
# the tables is that the *category* is shared, so interactive chat and `/gowork`
# can never disagree about what a harness just said.

SUPPORTED_BACKENDS = ("claude", "codex", "dsh", "local", "agui")

#: (backend, error text) pairs that must all read as temporary model saturation.
SATURATION_FIXTURES = [
    ("claude", "Claude's response was interrupted: model at capacity"),
    ("claude", 'API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}'),
    ("claude", "Opus 5 is at capacity right now — try again shortly."),
    ("claude", "We're currently experiencing high demand. Please try again."),
    ("codex", "stream error: the model is currently overloaded, please try again later"),
    ("codex", "error sending request: 503 Service Unavailable"),
    ("codex", "We're experiencing heavy load, which may cause temporary errors."),
    ("dsh", "Error: The server is busy. Please try again later."),
    ("dsh", "router: no capacity available for deepseek-v4-pro"),
    ("local", "model runner is busy, retry in a moment"),
    ("local", "ollama: server overloaded"),
    ("agui", "upstream temporarily unavailable"),
    ("agui", "backend is out of capacity"),
]

#: (backend, error text) pairs that must all read as provider rate limiting.
RATE_LIMIT_FIXTURES = [
    ("claude", 'API Error: 429 {"type":"error","error":{"type":"rate_limit_error"}}'),
    ("codex", "429 Too Many Requests"),
    ("dsh", "Rate limit reached for deepseek-v4-pro, retry after 30s"),
    ("local", "too many concurrent requests"),
    ("agui", "request throttled by the gateway"),
]

#: (backend, error text) pairs that must all read as a spent subscription/quota.
QUOTA_FIXTURES = [
    ("claude", "You've hit your 5-hour limit. Your limit resets at 3pm."),
    ("claude", "Claude usage limit reached for this week"),
    ("codex", "You've used up your ChatGPT Plus quota for today"),
    ("dsh", "Error: Insufficient Balance"),
    ("local", "monthly limit for this deployment has been reached"),
    ("agui", "Your credit balance is too low to make this request"),
]

#: (backend, error text) pairs that must all read as a broken provider login.
AUTH_FIXTURES = [
    ("claude", "Invalid API key · Please run /login"),
    ("claude", "Your session has expired, please log in again"),
    ("codex", "401 Unauthorized: not logged in, run `codex login`"),
    ("dsh", "Authentication Fails, Your api key is invalid"),
    ("local", "authentication failure talking to the local gateway"),
    ("agui", "403 Forbidden: credentials rejected"),
]

CATEGORY_FIXTURES = [
    (CapacityCategory.MODEL_SATURATED, SATURATION_FIXTURES),
    (CapacityCategory.PROVIDER_RATE_LIMITED, RATE_LIMIT_FIXTURES),
    (CapacityCategory.QUOTA_EXHAUSTED, QUOTA_FIXTURES),
    (CapacityCategory.AUTHENTICATION_FAILED, AUTH_FIXTURES),
]

ALL_PHRASE_FIXTURES = [
    (backend, phrase, category)
    for category, fixtures in CATEGORY_FIXTURES
    for backend, phrase in fixtures
]


@pytest.mark.parametrize(
    ("backend", "phrase", "expected"),
    [pytest.param(*row, id=f"{row[0]}:{row[1][:40]}") for row in ALL_PHRASE_FIXTURES],
)
def test_observed_backend_wording_lands_on_the_expected_category(
    backend: str, phrase: str, expected: CapacityCategory
) -> None:
    outcome = classify_failure(BackendFailure(error=phrase, backend=backend, model="m"))
    assert outcome.category is expected


def test_every_supported_harness_has_saturation_wording_covered() -> None:
    """A harness with no fixture is a harness nobody has checked."""
    covered = {backend for backend, _ in SATURATION_FIXTURES}
    assert covered == set(SUPPORTED_BACKENDS)


def test_the_same_wording_reads_the_same_way_on_every_harness() -> None:
    """The classifier is shared, so the backend label must not change the verdict."""
    for _, phrase, expected in ALL_PHRASE_FIXTURES:
        categories = {
            classify_failure(BackendFailure(error=phrase, backend=backend)).category
            for backend in SUPPORTED_BACKENDS
        }
        assert categories == {expected}


def test_saturation_fixtures_are_retryable_and_rate_limits_carry_their_hint() -> None:
    for backend, phrase in SATURATION_FIXTURES:
        assert classify_failure(BackendFailure(error=phrase, backend=backend)).retryable is True
    hinted = classify_failure(
        BackendFailure(error="Rate limit reached for deepseek-v4-pro, retry after 30s")
    )
    assert hinted.retry_after_seconds == 30


def test_quota_and_auth_fixtures_never_retry_automatically() -> None:
    for _, fixtures in CATEGORY_FIXTURES[2:]:
        for backend, phrase in fixtures:
            outcome = classify_failure(BackendFailure(error=phrase, backend=backend))
            assert outcome.retryable is False
            assert outcome.needs_user_action is True


def test_a_structured_code_still_beats_every_phrase_fixture() -> None:
    """Fixtures are the compatibility path; machine codes remain authoritative."""
    outcome = classify_failure(
        BackendFailure(
            error="Error: The server is busy. Please try again later.",
            error_code="insufficient_quota",
        )
    )
    assert outcome.category is CapacityCategory.QUOTA_EXHAUSTED


# --- false positives stay terminal ---------------------------------------------

#: Sentences a worker really writes while doing capacity-related work. None of
#: them is a provider failure, so all of them must stay permanent.
FALSE_POSITIVE_REPLIES = [
    "Wrote a fixture for the 'model at capacity' error string.",
    "The docs say this endpoint returns 503 Service Unavailable when overloaded.",
    "I added rate limiting to the API and wrote a test for the 429 path.",
    "Implemented the retry path for quota exhausted responses.",
    "Simulated a 429 in the test double to check the backoff.",
    "The server is busy handling the migration, so the deploy waited.",
    "queued — waiting for capacity",
    "Capacity planning for the cluster is documented in docs/capacity.md.",
]


@pytest.mark.parametrize("reply", FALSE_POSITIVE_REPLIES)
def test_a_worker_describing_capacity_work_is_not_a_capacity_failure(reply: str) -> None:
    assert classify_failure(BackendFailure(text=reply)).category is (
        CapacityCategory.PERMANENT_ERROR
    )


@pytest.mark.parametrize(
    "error",
    [
        "FileNotFoundError: claude_code_core/capacity.py",
        "ValueError: limit must be a positive integer",
        "fatal: the remote end hung up unexpectedly",
    ],
)
def test_ordinary_tool_errors_stay_permanent(error: str) -> None:
    assert classify_failure(BackendFailure(error=error)).category is (
        CapacityCategory.PERMANENT_ERROR
    )


def test_a_bare_try_again_later_is_not_enough_to_retry() -> None:
    """ "Try again later" with no capacity wording could mean anything."""
    outcome = classify_failure(BackendFailure(error="Something went wrong, try again later."))
    assert outcome.category is CapacityCategory.PERMANENT_ERROR


def test_real_provider_wording_in_a_reply_survives_the_false_positive_guard() -> None:
    outcome = classify_failure(BackendFailure(text="The model is at capacity right now."))
    assert outcome.category is CapacityCategory.MODEL_SATURATED
