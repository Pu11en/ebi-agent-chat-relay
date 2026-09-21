"""Every recovery phase reads differently and names its own category.

The wording lives in one place (``RecoveryStatus.line``) so Discord, Teams and
the API status all say the same thing; this file pins that each phase is
distinguishable by content, not just by colour.
"""

from __future__ import annotations

import logging

import pytest

from claude_code_core.capacity import CapacityCategory
from claude_code_core.capacity_policy import FallbackTarget, RecoveryPhase, RecoveryStatus


def _status(phase: RecoveryPhase, **extra: object) -> RecoveryStatus:
    fields: dict[str, object] = dict(
        phase=phase,
        category=CapacityCategory.MODEL_SATURATED,
        backend="claude",
        model="opus",
        attempt=2,
        max_attempts=6,
        detail="opus is temporarily at capacity.",
        next_attempt_in=45.0,
    )
    fields.update(extra)
    return RecoveryStatus(**fields)  # type: ignore[arg-type]


CASES = [
    (RecoveryPhase.RELAY_QUEUED, {}, ["run slot on this computer"]),
    (RecoveryPhase.WAITING, {}, ["Waiting", "attempt 2 of 6"]),
    (RecoveryPhase.RETRYING, {}, ["retry", "attempt 2 of 6", "45s"]),
    (
        RecoveryPhase.FALLBACK,
        {"fallback": FallbackTarget("codex", "gpt-5.5", authority="session")},
        ["Switching to codex · gpt-5.5", "session"],
    ),
    (RecoveryPhase.EXHAUSTED, {}, ["Gave up", "2 attempts", "Next:"]),
    (
        RecoveryPhase.AUTHENTICATION,
        {
            "category": CapacityCategory.AUTHENTICATION_FAILED,
            "detail": "The provider login for opus on this computer needs to be repaired.",
        },
        ["login", "Not retrying", "Next:"],
    ),
    (
        RecoveryPhase.QUOTA,
        {
            "category": CapacityCategory.QUOTA_EXHAUSTED,
            "detail": "The subscription or usage quota for opus is used up.",
            "resets_at": 1_800_000_000,
        },
        ["quota", "<t:1800000000:t>", "Not retrying", "Next:"],
    ),
    (
        RecoveryPhase.PERMANENT,
        {
            "category": CapacityCategory.PERMANENT_ERROR,
            "detail": "The request for opus failed and cannot be retried automatically.",
        },
        ["cannot be retried", "Next:"],
    ),
    (RecoveryPhase.AMBIGUOUS, {}, ["already delivered", "Next:"]),
    (RecoveryPhase.ACCEPTED, {"category": None}, ["answered after 2 attempt"]),
]


@pytest.mark.parametrize(("phase", "extra", "expected"), CASES, ids=[c[0].value for c in CASES])
def test_each_phase_names_its_category_and_next_step(
    phase: RecoveryPhase, extra: dict[str, object], expected: list[str]
) -> None:
    line = _status(phase, **extra).line
    for fragment in expected:
        assert fragment in line, (phase, line)


def test_the_ten_phase_lines_are_all_different() -> None:
    lines = [_status(phase, **extra).line for phase, extra, _ in CASES]
    assert len(set(lines)) == len(lines)


def test_relay_queueing_never_mentions_the_provider_or_the_model() -> None:
    line = _status(RecoveryPhase.RELAY_QUEUED).line
    assert "opus" not in line and "capacity" not in line.lower().replace("run slot", "")


def test_the_public_view_and_the_log_line_carry_no_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    status = _status(RecoveryPhase.RETRYING)
    view = status.public_view()
    assert set(view) == {
        "phase",
        "category",
        "backend",
        "model",
        "attempt",
        "max_attempts",
        "next_attempt_at",
        "fallback",
    }
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("capacity recovery %s", view)
    assert "prompt" not in caplog.text and "diagnostic" not in caplog.text
