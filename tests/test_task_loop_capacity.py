"""`/gowork` builds recover from model capacity through the shared coordinator.

A saturated worker round is not a failed try and not a question for the
person: the build waits within the shared policy, switches only to the
fallback the build was started with, and asks only when that budget is spent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_core import task_loop as tl
from claude_code_core.capacity_policy import RecoveryPolicy
from claude_discord.capacity_recovery import CapacityRecoveryCoordinator
from claude_discord.cogs import _run_helper as rh
from claude_discord.cogs.task_loop import _Running
from tests.test_task_loop_cog import _cog_with_chat

SATURATED = 'API Error: 529 {"type":"overloaded_error","message":"Overloaded"}'


class Sleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()


def _install(sleeper: Sleeper, **policy: object) -> CapacityRecoveryCoordinator:
    coordinator = CapacityRecoveryCoordinator(
        policy=RecoveryPolicy(min_delay_seconds=1, jitter_ratio=0.0, **policy),  # type: ignore[arg-type]
        sleep=sleeper,
        rng=lambda: 0.0,
    )
    rh.configure_capacity_recovery(coordinator)
    return coordinator


def _settings(backend: str = "claude", model: str = "sonnet") -> MagicMock:
    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value=backend)
    settings.current_model = AsyncMock(return_value=model)
    settings.set_backend = AsyncMock()
    settings.set_model = AsyncMock()
    return settings


def _said(thread: MagicMock) -> str:
    return " ".join(str(c.args[0]) for c in thread.send.await_args_list if c.args)


class TestCoreClassification:
    def test_saturation_is_a_limit_for_the_loop_not_a_failed_try(self) -> None:
        assert tl.usage_limit_message(None, SATURATED)
        assert tl.usage_limit_message(None, "Model is at capacity, try again later")

    def test_a_login_failure_also_reaches_the_person_instead_of_counting(self) -> None:
        assert tl.usage_limit_message(None, "Not logged in. Please run /login")

    def test_the_line_returned_is_the_providers_own(self) -> None:
        error = "some context\nYou've hit your usage limit · resets 7pm\nmore"
        assert tl.usage_limit_message(None, error) == "You've hit your usage limit · resets 7pm"

    def test_the_old_regex_tables_are_gone(self) -> None:
        assert not hasattr(tl, "_LIMIT_RE")
        assert not hasattr(tl, "_LIMIT_IN_REPLY_RE")


class TestSaturationInTheBuild:
    async def test_waits_and_retries_the_round_without_asking(self, sleeper: Sleeper) -> None:
        _install(sleeper)
        cog, chat, thread = _cog_with_chat()
        chat._backend_settings = _settings()
        cog._wait_or_wake = AsyncMock(return_value=(None, False))
        running = _Running(MagicMock(), Path("/x"), 555, 1, thread=thread)

        assert await cog._limit_hit(running, SATURATED) is True

        assert sleeper.slept == [30.0]
        cog._wait_or_wake.assert_not_awaited()
        said = _said(thread)
        assert "at capacity" in said and "attempt 1 of" in said
        assert "Type a letter" not in said
        assert "claude" not in running.limited

    async def test_one_status_message_is_edited_not_repeated(self, sleeper: Sleeper) -> None:
        _install(sleeper)
        cog, chat, thread = _cog_with_chat()
        chat._backend_settings = _settings()
        cog._wait_or_wake = AsyncMock(return_value=(None, False))
        status = MagicMock()
        status.edit = AsyncMock()
        thread.send = AsyncMock(return_value=status)
        running = _Running(MagicMock(), Path("/x"), 555, 1, thread=thread)

        assert await cog._limit_hit(running, SATURATED) is True
        assert await cog._limit_hit(running, SATURATED) is True

        assert thread.send.await_count == 1
        assert status.edit.await_count == 1
        assert "attempt 2 of" in status.edit.await_args.kwargs.get(
            "content", ""
        ) or "attempt 2 of" in str(status.edit.await_args)
        assert sleeper.slept == [30.0, 60.0]

    async def test_switches_to_the_builds_own_fallback_after_the_threshold(
        self, sleeper: Sleeper
    ) -> None:
        _install(sleeper, fallback_after_attempts=1)
        cog, chat, thread = _cog_with_chat()
        settings = _settings()
        chat._backend_settings = settings
        cog._wait_or_wake = AsyncMock(return_value=(None, False))
        running = _Running(
            MagicMock(), Path("/x"), 555, 1, thread=thread, fallback=("dsh", "glm-5.3")
        )

        assert await cog._limit_hit(running, SATURATED) is True

        settings.set_backend.assert_awaited_once_with("dsh", thread_id=555)
        settings.set_model.assert_awaited_once_with("dsh", "glm-5.3", thread_id=555)
        assert "Switching to dsh · glm-5.3" in _said(thread)
        assert sleeper.slept == []
        assert running.fallback_used is True

    async def test_without_a_fallback_no_backend_is_ever_switched(self, sleeper: Sleeper) -> None:
        _install(sleeper, fallback_after_attempts=1, max_attempts=3)
        cog, chat, thread = _cog_with_chat()
        settings = _settings()
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(return_value=[])
        cog._wait_or_wake = AsyncMock(return_value=(None, False))
        status = MagicMock()
        status.edit = AsyncMock()
        thread.send = AsyncMock(return_value=status)
        running = _Running(MagicMock(), Path("/x"), 555, 1, thread=thread)

        results = [await cog._limit_hit(running, SATURATED) for _ in range(3)]

        assert results == [True, True, False]
        settings.set_backend.assert_not_awaited()
        edits = " ".join(str(c.kwargs.get("content", "")) for c in status.edit.await_args_list)
        assert "Gave up" in edits
        assert "Type a letter" in _said(thread)

    async def test_a_quota_limit_keeps_the_switch_or_wait_question(self, sleeper: Sleeper) -> None:
        _install(sleeper)
        cog, chat, thread = _cog_with_chat()
        settings = _settings()
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(return_value=[])
        cog._wait_or_wake = AsyncMock(return_value=(None, False))
        running = _Running(MagicMock(), Path("/x"), 555, 1, thread=thread)

        assert await cog._limit_hit(running, "You've hit your usage limit") is False

        assert sleeper.slept == []
        assert "usage limit" in _said(thread) and "wait" in _said(thread).lower()
        assert "claude" in running.limited
