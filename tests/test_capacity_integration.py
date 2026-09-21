"""Interactive turns go through the shared recovery coordinator.

A fake runner scripts what each backend attempt yields; the Discord thread is a
mock; the coordinator sleeps through an injected no-op. Every test checks the
two things the change promises: the prompt is never retyped, and the thread
ends with one recovery status and at most one answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.capacity_policy import FallbackTarget, RecoveryPolicy
from claude_discord.capacity_recovery import (
    CapacityRecoveryCoordinator,
    CapacityRestartLoader,
)
from claude_discord.claude.types import MessageType, StreamEvent
from claude_discord.cogs import _run_helper as rh
from claude_discord.cogs.run_config import RunConfig
from claude_discord.concurrency import SessionRegistry
from claude_discord.database.capacity_recovery_repo import (
    CapacityRecoveryRepository,
    PendingTurnState,
)
from claude_discord.database.models import init_db

SATURATED = "API Error: 529 {\"type\":\"overloaded_error\",\"message\":\"Overloaded\"}"
QUOTA = "You've hit your usage limit · resets 7pm"


def _events(*, error: str | None = None, text: str | None = None, partial: str | None = None):
    events = [StreamEvent(message_type=MessageType.SYSTEM, session_id="s1")]
    if partial:
        events.append(StreamEvent(message_type=MessageType.ASSISTANT, text=partial))
    if text:
        events.append(StreamEvent(message_type=MessageType.ASSISTANT, text=text))
    events.append(
        StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            error=error,
            text=text,
            session_id="s1",
            cost_usd=0.0,
            duration_ms=1,
        )
    )
    return events


class FakeRunner:
    """Yields one scripted attempt per ``run`` call and records how it was called."""

    def __init__(self, *scripts: list[StreamEvent], model: str = "opus") -> None:
        self.scripts = list(scripts)
        self.calls: list[tuple[str, str | None]] = []
        self.model = model
        self.working_dir = None
        self.append_system_prompt = None
        self.images = None
        self.command = "claude"

    def clone(self, **kwargs: object) -> FakeRunner:
        return self

    def describe_api(self) -> str:
        return "fake"

    async def interrupt(self) -> None:
        return None

    async def run(self, prompt: str, session_id: str | None = None):
        self.calls.append((prompt, session_id))
        if not self.scripts:
            raise AssertionError("unexpected extra attempt")
        for event in self.scripts.pop(0):
            yield event


def _thread() -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 4242
    thread.name = "t"
    sent: list[MagicMock] = []

    async def send(*args: object, **kwargs: object) -> MagicMock:
        message = MagicMock(spec=discord.Message)
        message.edit = AsyncMock()
        message.sent_args = (args, kwargs)
        sent.append(message)
        return message

    thread.send = AsyncMock(side_effect=send)
    thread.sent = sent
    return thread


def _embed_texts(thread: MagicMock) -> list[str]:
    """Every embed title/description that was sent or edited, in order."""
    out: list[str] = []
    for message in thread.sent:
        _args, kwargs = message.sent_args
        embed = kwargs.get("embed")
        if embed is not None:
            out.append(f"{embed.title or ''} {embed.description or ''}")
        for call in message.edit.await_args_list:
            embed = call.kwargs.get("embed")
            if embed is not None:
                out.append(f"{embed.title or ''} {embed.description or ''}")
    return out


class Sleeper:
    def __init__(self) -> None:
        self.slept: list[float] = []
        self.hold = False

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        if self.hold:
            await asyncio.Event().wait()


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()


@pytest.fixture(autouse=True)
def _reset_recovery() -> Iterator[None]:
    yield
    rh.configure_capacity_recovery(None)


@pytest.fixture
async def repo() -> AsyncIterator[CapacityRecoveryRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield CapacityRecoveryRepository(path)
    finally:
        os.unlink(path)


def _configure(
    sleeper: Sleeper,
    *,
    store: CapacityRecoveryRepository | None = None,
    policy: RecoveryPolicy | None = None,
    chain: tuple[FallbackTarget, ...] = (),
    factory: object | None = None,
) -> CapacityRecoveryCoordinator:
    coordinator = CapacityRecoveryCoordinator(
        policy=policy or RecoveryPolicy(min_delay_seconds=1, jitter_ratio=0.0),
        store=store,
        sleep=sleeper,
        rng=lambda: 0.0,
    )
    rh.configure_capacity_recovery(coordinator, fallback_chain=chain, backend_factory=factory)
    return coordinator


async def _run(thread: MagicMock, runner: FakeRunner, **extra: object) -> list[tuple]:
    captured: list[tuple] = []

    async def sink(text: str | None, error: str | None) -> None:
        captured.append((text, error))

    config = RunConfig(
        thread=thread, runner=runner, prompt="Write the notes", result_sink=sink, **extra
    )  # type: ignore[arg-type]
    await rh.run_claude_with_config(config)
    return captured


async def test_a_saturated_turn_waits_then_finishes_without_retyping(sleeper: Sleeper) -> None:
    _configure(sleeper)
    runner = FakeRunner(_events(error=SATURATED), _events(text="Final reply."))
    thread = _thread()

    captured = await _run(thread, runner, session_id="abc")

    assert runner.calls == [("Write the notes", "abc"), ("Write the notes", "abc")]
    assert captured == [("Final reply.", None)]
    assert sleeper.slept == [30.0]
    texts = _embed_texts(thread)
    assert not any("Error" in t and "Overloaded" in t for t in texts), texts
    recovery = [t for t in texts if "at capacity" in t]
    assert len(recovery) >= 1
    assert "attempt 1 of" in recovery[0]
    status_messages = [
        m
        for m in thread.sent
        if (embed := m.sent_args[1].get("embed")) is not None
        and "capacity" in (embed.description or "")
    ]
    assert len(status_messages) == 1
    assert status_messages[0].edit.await_count >= 1


async def test_provider_waiting_is_not_described_as_relay_queueing(sleeper: Sleeper) -> None:
    _configure(sleeper)
    runner = FakeRunner(_events(error=SATURATED), _events(text="ok"))
    thread = _thread()

    await _run(thread, runner)

    waiting = [t for t in _embed_texts(thread) if "at capacity" in t]
    assert waiting and all("run slot" not in t and "Queued" not in t for t in waiting)


async def test_quota_stops_at_once_with_the_next_valid_action(sleeper: Sleeper) -> None:
    _configure(sleeper)
    runner = FakeRunner(_events(error=QUOTA))
    thread = _thread()

    captured = await _run(thread, runner)

    assert len(runner.calls) == 1
    assert sleeper.slept == []
    assert captured[0][0] is None and "usage limit" in (captured[0][1] or "")
    texts = _embed_texts(thread)
    assert any("Next:" in t and "quota" in t for t in texts), texts


async def test_authentication_failure_names_the_login_repair(sleeper: Sleeper) -> None:
    _configure(sleeper)
    runner = FakeRunner(_events(error="Not logged in. Please run /login"))
    thread = _thread()

    await _run(thread, runner)

    assert len(runner.calls) == 1
    assert any("login" in t and "Not retrying" in t for t in _embed_texts(thread))


async def test_an_unknown_error_stays_a_plain_error_without_recovery_noise(
    sleeper: Sleeper,
) -> None:
    _configure(sleeper)
    runner = FakeRunner(_events(error="TypeError: boom"))
    thread = _thread()

    captured = await _run(thread, runner)

    assert len(runner.calls) == 1
    assert captured == [(None, "TypeError: boom")]
    texts = _embed_texts(thread)
    assert any("TypeError: boom" in t for t in texts)
    assert not any("Next:" in t for t in texts)


async def test_the_authorized_fallback_is_announced_and_used(sleeper: Sleeper) -> None:
    fallback = FakeRunner(_events(text="From codex."), model="gpt-5.5")
    factory = MagicMock()
    factory.build = MagicMock(return_value=fallback)
    _configure(
        sleeper,
        policy=RecoveryPolicy(min_delay_seconds=1, jitter_ratio=0.0, fallback_after_attempts=1),
        chain=(FallbackTarget("codex", "gpt-5.5", authority="computer"),),
        factory=factory,
    )
    runner = FakeRunner(_events(error=SATURATED))
    thread = _thread()

    captured = await _run(thread, runner, session_id="abc")

    assert captured == [("From codex.", None)]
    factory.build.assert_called_once_with(backend="codex", model="gpt-5.5", thread_id=4242)
    assert fallback.calls == [("Write the notes", None)]
    assert any("Switching to codex · gpt-5.5" in t for t in _embed_texts(thread))
    assert sleeper.slept == []


async def test_without_a_chain_nothing_is_switched_and_exhaustion_asks(sleeper: Sleeper) -> None:
    factory = MagicMock()
    _configure(
        sleeper,
        policy=RecoveryPolicy(min_delay_seconds=1, jitter_ratio=0.0, max_attempts=2),
        factory=factory,
    )
    runner = FakeRunner(_events(error=SATURATED), _events(error=SATURATED))
    thread = _thread()

    captured = await _run(thread, runner)

    assert len(runner.calls) == 2
    factory.build.assert_not_called()
    assert captured[0][0] is None and captured[0][1]
    assert any("Gave up" in t and "Next:" in t for t in _embed_texts(thread))


async def test_a_partial_answer_before_saturation_is_not_retried(sleeper: Sleeper) -> None:
    _configure(sleeper)
    runner = FakeRunner(_events(partial="Half an answer", error=SATURATED))
    thread = _thread()

    await _run(thread, runner)

    assert len(runner.calls) == 1
    assert any("already delivered" in t for t in _embed_texts(thread))


async def test_recovery_state_is_visible_in_the_registry_and_snapshot(sleeper: Sleeper) -> None:
    coordinator = _configure(sleeper)
    registry = SessionRegistry()
    seen: list[tuple[str | None, dict[str, dict[str, object]]]] = []

    async def spy(seconds: float) -> None:
        states = {s.thread_id: s.execution_state for s in registry.list_active()}
        seen.append((states.get(4242), coordinator.snapshot()))

    coordinator._sleep = spy  # type: ignore[attr-defined]
    runner = FakeRunner(_events(error=SATURATED), _events(text="ok"))
    thread = _thread()

    registry.register(4242, "Write the notes", None)
    await _run(thread, runner, registry=registry)

    state, snapshot = seen[0]
    assert state == "recovering"
    view = next(iter(snapshot.values()))
    assert view["thread_id"] == 4242 and view["phase"] == "retrying"
    assert "Write the notes" not in repr(snapshot)


async def test_a_turn_waiting_at_restart_resumes_once_and_answers_once(
    sleeper: Sleeper, repo: CapacityRecoveryRepository
) -> None:
    _configure(sleeper, store=repo)
    sleeper.hold = True
    runner = FakeRunner(_events(error=SATURATED))
    thread = _thread()
    task = asyncio.create_task(_run(thread, runner, session_id="abc"))
    while not sleeper.slept:
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    later = datetime.now(UTC) + timedelta(seconds=120)
    (pending,) = await repo.reload_due(now=later)
    assert pending.state is PendingTurnState.SCHEDULED
    assert pending.prompt_ref == "Write the notes"

    sleeper.hold = False
    resumed = FakeRunner(_events(text="Resumed answer."))
    captured: list[tuple] = []

    async def resume(turn) -> None:  # noqa: ANN001
        async def sink(text: str | None, error: str | None) -> None:
            captured.append((text, error))

        await rh.run_claude_with_config(
            RunConfig(
                thread=thread,
                runner=resumed,
                prompt=turn.prompt_ref,
                session_id=turn.session_id,
                result_sink=sink,
                recovery_turn_key=turn.turn_key,
                recovery_claim_token=turn.claim_token,
            )
        )

    loader = CapacityRestartLoader(repo, resume, now=lambda: later)
    assert await loader.load_due() == [pending.turn_key]
    assert await loader.load_due() == []

    assert resumed.calls == [("Write the notes", "abc")]
    assert captured == [("Resumed answer.", None)]
    stored = await repo.get(pending.turn_key)
    assert stored is not None and stored.state is PendingTurnState.ACCEPTED


async def test_without_a_configured_coordinator_behaviour_is_unchanged() -> None:
    rh.configure_capacity_recovery(None)
    runner = FakeRunner(_events(error=SATURATED))
    thread = _thread()

    captured = await _run(thread, runner)

    assert len(runner.calls) == 1
    assert captured[0][1] == SATURATED


# ---------------------------------------------------------------------------
# ClaudeChatCog resumes persisted turns on startup
# ---------------------------------------------------------------------------


async def test_chat_cog_resumes_a_due_turn_once_on_ready(
    repo: CapacityRecoveryRepository,
) -> None:
    from unittest.mock import patch

    from claude_discord.cogs.claude_chat import ClaudeChatCog
    from claude_discord.database.capacity_recovery_repo import CapacityPendingTurnCreate

    now = datetime.now(UTC)
    await repo.create_pending(
        CapacityPendingTurnCreate(
            turn_key="discord:555:abc",
            frontend="discord",
            thread_id=555,
            session_id="sess-1",
            prompt_ref="Keep going",
            backend="claude",
            model="opus",
            fallback_chain=[],
            next_attempt_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=1),
        ),
        now=now,
    )
    thread = MagicMock(spec=discord.Thread)
    thread.id = 555
    thread.send = AsyncMock(return_value=MagicMock())
    thread.parent = MagicMock(spec=discord.TextChannel)
    bot = MagicMock()
    bot.get_channel.return_value = thread
    bot.resume_repo = None
    session_repo = MagicMock()
    session_repo.get = AsyncMock(return_value=None)
    cog = ClaudeChatCog(bot=bot, repo=session_repo, runner=MagicMock(), capacity_repo=repo)
    runs: list[dict[str, object]] = []

    async def fake_run_claude(*args: object, **kwargs: object) -> None:
        runs.append(kwargs)

    with patch.object(cog, "_run_claude", side_effect=fake_run_claude):
        await cog.on_ready()
        await cog.on_ready()
        await asyncio.sleep(0)

    assert len(runs) == 1
    assert runs[0]["session_id"] == "sess-1"
    assert runs[0]["recovery"][0] == "discord:555:abc"  # type: ignore[index]
    stored = await repo.get("discord:555:abc")
    assert stored is not None and stored.state is PendingTurnState.RUNNING
    assert runs[0]["recovery"][1] == stored.claim_token  # type: ignore[index]
    assert "waiting for model capacity" in thread.send.await_args.args[0].lower()
