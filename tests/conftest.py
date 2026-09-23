"""Shared pytest fixtures for claude_discord tests.

These fixtures are automatically available to all test files in this directory.
Class-level fixtures with the same name take precedence (pytest scoping rules).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.claude.types import MessageType, StreamEvent

#: Every test starts from an unconfigured instance. A Discord/agent session
#: inherits the running bot's environment (CCDB_PROJECT_ROOTS, channel ids, the
#: category boundary), and a test that reads one of those passes on CI and fails
#: on the machine the bot runs on — 45 of them did. Sanitizing here makes a local
#: run mean the same thing as CI; a test that wants a value sets it with
#: monkeypatch.setenv.
_LEAKY_PREFIXES = ("CCDB_", "DISCORD_", "CLAUDE_", "ANTHROPIC_", "CODEX_")
_KEPT = frozenset({"CLAUDE_CODE_ENTRYPOINT"})


@pytest.fixture(autouse=True)
def _unconfigured_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith(_LEAKY_PREFIXES) and name not in _KEPT:
            monkeypatch.delenv(name, raising=False)


if sys.platform == "win32" or sys.version_info[:2] == (3, 12):
    from asyncio import base_subprocess

    _original_try_finish = base_subprocess.BaseSubprocessTransport._try_finish

    def _try_finish_even_if_pipes_never_connected(self: base_subprocess.BaseSubprocessTransport):
        # CPython on Windows and on Python 3.12 (any platform): cancelling a task
        # mid-create_subprocess_exec can cancel the transport's _connect_pipes
        # before it runs, leaving pipe slots None forever, so _wait() never
        # resolves and pytest-asyncio's loop teardown hangs. Once closed and
        # exited, treat never-connected pipes as disconnected so the transport
        # can finish.
        if (
            self._closed  # type: ignore[attr-defined]
            and self._returncode is not None  # type: ignore[attr-defined]
            and not self._finished  # type: ignore[attr-defined]
            and all(p is None or p.disconnected for p in self._pipes.values())  # type: ignore[attr-defined]
        ):
            self._finished = True  # type: ignore[attr-defined]
            # Not self._call: that queues into _pending_calls, drained only by _connect_pipes.
            self._loop.call_soon(self._call_connection_lost, None)  # type: ignore[attr-defined]
            return
        _original_try_finish(self)

    base_subprocess.BaseSubprocessTransport._try_finish = _try_finish_even_if_pipes_never_connected  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _isolate_operator_statusline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not execute commands from the operator's real settings file.

    Explicit paths and per-test patches still exercise statusline behavior.
    """
    from claude_discord.discord_ui import statusline

    original = statusline.read_statusline_command
    monkeypatch.setattr(
        statusline,
        "read_statusline_command",
        lambda settings_path=None: original(settings_path) if settings_path else None,
    )


@pytest.fixture(autouse=True)
def _isolate_capacity_recovery() -> Iterator[None]:
    """A coordinator installed by one test (e.g. setup_bridge) must not make the
    next test's scripted "429" wait thirty real seconds for a retry."""
    yield
    from claude_discord.cogs import _run_helper

    _run_helper.configure_capacity_recovery(None)


@pytest.fixture
def thread() -> MagicMock:
    """A MagicMock discord.Thread with send and id set."""
    t = MagicMock(spec=discord.Thread)
    t.id = 12345
    msg = MagicMock(spec=discord.Message)
    msg.edit = AsyncMock()
    t.send = AsyncMock(return_value=msg)
    return t


@pytest.fixture
def runner() -> MagicMock:
    """A MagicMock ClaudeRunner with interrupt() wired up.

    clone() returns the same mock so tests that set runner.run = ...
    keep working after _build_system_context triggers runner.clone().
    """
    r = MagicMock()
    r.interrupt = AsyncMock()
    r.clone = MagicMock(return_value=r)
    return r


@pytest.fixture
def repo() -> MagicMock:
    """A MagicMock SessionRepository with async save/get."""
    r = MagicMock()
    r.save = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture(autouse=True)
def _patch_build_system_context(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch _build_system_context to return None by default.

    The always-on File Delivery injection causes runner.clone() on every run,
    which breaks tests using runner.run = async_gen on a plain MagicMock.
    Tests that need real system context should use @pytest.mark.real_system_context.
    """
    if "real_system_context" in {m.name for m in request.node.iter_markers()}:
        return
    monkeypatch.setattr(
        "claude_discord.cogs._run_helper._build_system_context",
        AsyncMock(return_value=None),
    )


def make_async_gen(events: list[StreamEvent]):
    """Return an async generator factory that yields the given events.

    Usage::

        runner.run = make_async_gen([event1, event2])
        async for e in runner.run("prompt"):
            ...
    """

    async def gen(*args, **kwargs):
        for e in events:
            yield e

    return gen


def simple_events(session_id: str = "sess-1") -> list[StreamEvent]:
    """Return a minimal sequence: SYSTEM + RESULT (no tool use)."""
    return [
        StreamEvent(message_type=MessageType.SYSTEM, session_id=session_id),
        StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            text="Done.",
            session_id=session_id,
            cost_usd=0.01,
            duration_ms=500,
        ),
    ]
