"""One typed reading of why a model attempt ended without an answer.

Every runner — interactive chat, `/gowork`, a headless worker — has to decide the
same thing after a failed attempt: wait, stop, or ask. Doing that with ad-hoc
string checks in each surface is how two runners come to disagree about the same
provider message, so the judgement lives here once, above any frontend.

The classifier is deliberately conservative. Structured exit information is read
first and a small set of provider phrases only as a compatibility fallback; an
error that matches nothing stays :attr:`CapacityCategory.PERMANENT_ERROR` rather
than being optimistically retried. Retry *policy* is not here — this module says
what happened, not what to do about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "BackendFailure",
    "CapacityCategory",
    "CapacityOutcome",
    "classify_failure",
    "relay_queued_outcome",
]

#: Raw provider text is kept for logs, never shown whole to a user.
_MAX_DIAGNOSTIC = 2000
#: A retry hint longer than a day is a parsing accident, not a provider promise.
_MAX_RETRY_AFTER_SECONDS = 86_400


class CapacityCategory(StrEnum):
    """What kind of "no answer" this was, in recovery terms."""

    #: The local relay has no execution slot yet; the provider was never asked.
    RELAY_QUEUED = "relay_queued"
    #: The selected model is temporarily saturated. The same request may work later.
    MODEL_SATURATED = "model_saturated"
    #: The provider is throttling this caller right now.
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    #: A subscription or usage quota is spent; waiting for a reset is the only cure.
    QUOTA_EXHAUSTED = "quota_exhausted"
    #: Credentials are missing, expired, or rejected on this computer.
    AUTHENTICATION_FAILED = "authentication_failed"
    #: Anything else, including everything unrecognized.
    PERMANENT_ERROR = "permanent_error"


#: Only these may be retried automatically; anything else needs a person.
_RETRYABLE = frozenset(
    {
        CapacityCategory.RELAY_QUEUED,
        CapacityCategory.MODEL_SATURATED,
        CapacityCategory.PROVIDER_RATE_LIMITED,
    }
)


@dataclass(frozen=True)
class CapacityOutcome:
    """A classified attempt outcome, readable by any surface.

    ``user_detail`` is built from fixed wording plus the model name, so it is safe
    to show anywhere. ``diagnostic`` holds the provider's raw text and belongs in
    logs only — the two are never the same string.
    """

    category: CapacityCategory
    user_detail: str
    diagnostic: str = ""
    retry_after_seconds: float | None = None
    resets_at: int | None = None
    backend: str = ""
    model: str = ""

    @property
    def retryable(self) -> bool:
        """Whether an automatic retry is allowed at all (not whether one is due)."""
        return self.category in _RETRYABLE

    @property
    def needs_user_action(self) -> bool:
        """Whether recovery has to stop and name a next valid action."""
        return self.category not in _RETRYABLE

    @property
    def is_provider_failure(self) -> bool:
        """False for local relay queueing, which never reached the provider."""
        return self.category is not CapacityCategory.RELAY_QUEUED


@dataclass(frozen=True)
class BackendFailure:
    """Everything a backend can say about an attempt that produced no answer.

    Fields are all optional because backends differ in how much they surface: a
    CLI may offer only ``error`` text, while an API-backed one also has a status
    code and a machine-readable ``error_code``.
    """

    #: The backend's own error line(s).
    error: str | None = None
    #: The model's reply, consulted only when there is no error at all.
    text: str | None = None
    #: Process exit code, when the backend is a spawned CLI.
    exit_code: int | None = None
    #: HTTP status, when the backend surfaces one.
    status_code: int | None = None
    #: Provider machine code, e.g. ``overloaded_error`` or ``insufficient_quota``.
    error_code: str | None = None
    #: Provider retry hint in seconds, when it gave one.
    retry_after_seconds: float | None = None
    #: Unix timestamp at which an exhausted quota resets, when known.
    resets_at: int | None = None
    backend: str = ""
    model: str = ""


#: Machine codes are unambiguous, so they outrank every phrase below.
_ERROR_CODES: dict[str, CapacityCategory] = {
    "overloaded_error": CapacityCategory.MODEL_SATURATED,
    "model_overloaded": CapacityCategory.MODEL_SATURATED,
    "capacity_exceeded": CapacityCategory.MODEL_SATURATED,
    "server_overloaded": CapacityCategory.MODEL_SATURATED,
    "rate_limit_error": CapacityCategory.PROVIDER_RATE_LIMITED,
    "rate_limited": CapacityCategory.PROVIDER_RATE_LIMITED,
    "too_many_requests": CapacityCategory.PROVIDER_RATE_LIMITED,
    "insufficient_quota": CapacityCategory.QUOTA_EXHAUSTED,
    "quota_exceeded": CapacityCategory.QUOTA_EXHAUSTED,
    "usage_limit_reached": CapacityCategory.QUOTA_EXHAUSTED,
    "billing_hard_limit_reached": CapacityCategory.QUOTA_EXHAUSTED,
    "authentication_error": CapacityCategory.AUTHENTICATION_FAILED,
    "invalid_api_key": CapacityCategory.AUTHENTICATION_FAILED,
    "permission_error": CapacityCategory.AUTHENTICATION_FAILED,
    "oauth_token_expired": CapacityCategory.AUTHENTICATION_FAILED,
}

#: Statuses are read only after phrases, so a 429 carrying quota wording stays quota.
_STATUS_CODES: dict[int, CapacityCategory] = {
    401: CapacityCategory.AUTHENTICATION_FAILED,
    403: CapacityCategory.AUTHENTICATION_FAILED,
    429: CapacityCategory.PROVIDER_RATE_LIMITED,
    503: CapacityCategory.MODEL_SATURATED,
    529: CapacityCategory.MODEL_SATURATED,
}

_AUTH_RE = re.compile(
    r"invalid[ _-]api[ _-]key|authentication (?:failed|error)|\bunauthorized\b|"
    r"not (?:logged in|authenticated)|please (?:run )?/?login|"
    r"(?:oauth|access|auth) token (?:has )?expired|"
    r"credentials (?:are )?(?:missing|invalid|expired|rejected)",
    re.IGNORECASE,
)
#: A quota needs a quota-ish qualifier: a bare "limit exceeded" is a rate limit or
#: a worker talking about its own code, not proof that the subscription is spent.
_QUOTA_RE = re.compile(
    r"(?:session|usage|weekly|daily|hourly|monthly|5-hour) limit|"
    r"hit your .{0,40}limit|reached your .{0,40}limit|"
    r"exceeded your current quota|quota (?:exceeded|exhausted)|insufficient_quota|"
    r"out of credits|credit balance is too low|billing hard limit|"
    r"(?:subscription|plan) limit",
    re.IGNORECASE,
)
_RATE_RE = re.compile(r"rate[ _-]?limit|too many requests|\b429\b", re.IGNORECASE)
#: "at capacity" is anchored so the relay's own "waiting for capacity" never matches.
_SATURATION_RE = re.compile(
    r"\bat capacity\b|overloaded|capacity constraints|temporarily unavailable|"
    r"servers? (?:are|is) (?:busy|overloaded)|service unavailable|\b529\b",
    re.IGNORECASE,
)
#: Phrases specific enough to trust inside the model's own reply, where any
#: mention of limits is far more likely to be the worker describing its work.
_PROVIDER_WORDING_RE = re.compile(
    r"\bat capacity\b|overloaded|exceeded your current quota|insufficient_quota|"
    r"hit your .{0,40}limit|(?:session|usage|weekly|daily|5-hour) limit|"
    r"invalid[ _-]api[ _-]key|authentication (?:failed|error)",
    re.IGNORECASE,
)

_RETRY_AFTER_RES = (
    re.compile(r"retry[- ]after[:=]?\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)?\b"),
    re.compile(
        r"(?:try|retry) again in\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:please )?wait\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)\b", re.IGNORECASE
    ),
)

_USER_DETAIL: dict[CapacityCategory, str] = {
    CapacityCategory.RELAY_QUEUED: "Queued — waiting for a free run slot on this computer.",
    CapacityCategory.MODEL_SATURATED: "{model} is temporarily at capacity.",
    CapacityCategory.PROVIDER_RATE_LIMITED: "The provider is limiting how fast {model} can be "
    "called right now.",
    CapacityCategory.QUOTA_EXHAUSTED: "The subscription or usage quota for {model} is used up.",
    CapacityCategory.AUTHENTICATION_FAILED: "The provider login for {model} on this computer "
    "needs to be repaired.",
    CapacityCategory.PERMANENT_ERROR: "The request for {model} failed and cannot be retried "
    "automatically.",
}


def relay_queued_outcome(*, backend: str = "", model: str = "") -> CapacityOutcome:
    """The local admission state: no provider was asked, so nothing is saturated."""
    return CapacityOutcome(
        category=CapacityCategory.RELAY_QUEUED,
        user_detail=_USER_DETAIL[CapacityCategory.RELAY_QUEUED],
        backend=backend,
        model=model,
    )


def classify_failure(failure: BackendFailure) -> CapacityOutcome:
    """Read a failed attempt as exactly one recovery category.

    Structured codes are trusted first, then provider phrases, then HTTP status.
    Anything unmatched is permanent — an unknown error is never retried on a guess.
    Never returns :attr:`CapacityCategory.RELAY_QUEUED`, which only the local
    admission path can produce.
    """
    haystack, from_reply = _signal_text(failure)
    category = (
        _from_error_code(failure.error_code)
        or _from_phrases(haystack, strict=from_reply)
        or _STATUS_CODES.get(failure.status_code or 0)
        or CapacityCategory.PERMANENT_ERROR
    )
    return CapacityOutcome(
        category=category,
        user_detail=_USER_DETAIL[category].format(model=failure.model or "The selected model"),
        diagnostic=_diagnostic(failure),
        retry_after_seconds=_retry_after(failure, haystack),
        resets_at=failure.resets_at,
        backend=failure.backend,
        model=failure.model,
    )


def _signal_text(failure: BackendFailure) -> tuple[str, bool]:
    """The text to classify, and whether it came from the model's own reply."""
    if failure.error and failure.error.strip():
        return failure.error, False
    return failure.text or "", True


def _from_error_code(code: str | None) -> CapacityCategory | None:
    return _ERROR_CODES.get((code or "").strip().lower()) if code else None


def _from_phrases(text: str, *, strict: bool) -> CapacityCategory | None:
    """Ordered phrase reading; ``strict`` limits it to unmistakable provider wording.

    Quota is checked before rate limiting because "you've hit your usage limit"
    also mentions a limit, and the two need opposite handling.
    """
    if not text.strip():
        return None
    if strict and not _PROVIDER_WORDING_RE.search(text):
        return None
    for pattern, category in (
        (_AUTH_RE, CapacityCategory.AUTHENTICATION_FAILED),
        (_QUOTA_RE, CapacityCategory.QUOTA_EXHAUSTED),
        (_RATE_RE, CapacityCategory.PROVIDER_RATE_LIMITED),
        (_SATURATION_RE, CapacityCategory.MODEL_SATURATED),
    ):
        if pattern.search(text):
            return category
    return None


def _diagnostic(failure: BackendFailure) -> str:
    """Raw provider detail for logs — never shown to a user as-is."""
    parts = [
        part.strip()
        for part in (failure.error, failure.text, _exit_note(failure.exit_code))
        if part and part.strip()
    ]
    return "\n".join(parts)[:_MAX_DIAGNOSTIC]


def _exit_note(exit_code: int | None) -> str | None:
    return None if exit_code is None else f"exit code {exit_code}"


def _retry_after(failure: BackendFailure, text: str) -> float | None:
    """The provider's own hint, if it gave a believable one."""
    return _sane_delay(failure.retry_after_seconds) or _parse_retry_after(text)


def _parse_retry_after(text: str) -> float | None:
    for pattern in _RETRY_AFTER_RES:
        match = pattern.search(text)
        if match is None:
            continue
        unit = (match.group(2) or "s").lower()
        seconds = float(match.group(1)) * (60 if unit.startswith("m") else 1)
        if (sane := _sane_delay(seconds)) is not None:
            return sane
    return None


def _sane_delay(seconds: float | None) -> float | None:
    if seconds is None or seconds <= 0 or seconds > _MAX_RETRY_AFTER_SECONDS:
        return None
    return float(seconds)
