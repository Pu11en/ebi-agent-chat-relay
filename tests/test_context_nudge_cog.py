"""Tests for cogs.context_nudge — the one-tap "start fresh" flow."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs.context_nudge import ContextNudger


def _thread(name: str = "realpage") -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    thread.name = name
    thread.mention = "<#42>"
    thread.parent = MagicMock(spec=discord.TextChannel)
    thread.send = AsyncMock(return_value=MagicMock())
    return thread


def _chat(
    workdir: Path, used: int = 600, window: int = 1000, backend: str | None = None
) -> MagicMock:
    chat = MagicMock()
    record = SimpleNamespace(
        context_used=used,
        context_window=window,
        working_dir=str(workdir),
        session_id="s",
        backend=backend,
    )
    chat.repo.get = AsyncMock(return_value=record)
    new = MagicMock(spec=discord.Thread)
    new.mention = "<#43>"
    chat.spawn_session = AsyncMock(return_value=new)
    return chat


def _answer(value: str | None):  # noqa: ANN202
    surface = MagicMock()
    surface.prompt_choice = AsyncMock(return_value=(value,) if value else None)
    return patch("claude_discord.cogs.context_nudge.DiscordSurface", return_value=surface)


class TestCheck:
    async def test_quiet_below_half(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path, used=100)
        nudger = ContextNudger(chat)
        with _answer("yes") as surface_cls:
            await nudger._check(_thread())
        surface_cls.assert_not_called()

    async def test_the_question_says_estimate_on_dsh(self, tmp_path: Path) -> None:
        """D10b: the DSH figure is characters / 4, and the nudge must say so."""
        nudger = ContextNudger(_chat(tmp_path, backend="dsh"))
        with _answer("no") as surface_cls:
            await nudger._check(_thread())
        question = surface_cls.return_value.prompt_choice.await_args.args[0].question
        assert "50%" in question and "estimate" in question.lower()

        nudger = ContextNudger(_chat(tmp_path, backend="claude"))
        with _answer("no") as surface_cls:
            await nudger._check(_thread())
        question = surface_cls.return_value.prompt_choice.await_args.args[0].question
        assert "estimate" not in question.lower()

    async def test_asks_once_per_step(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path)
        nudger = ContextNudger(chat)
        with _answer("no") as surface_cls:
            await nudger._check(_thread())
            await nudger._check(_thread())
        assert surface_cls.call_count == 1

    async def test_yes_writes_handoff_then_opens_the_next_part(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path)

        async def write_handoff(seed, thread, prompt):  # noqa: ANN001
            path = Path(prompt.split("Write a handoff file at ")[1].split(" (create")[0])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("## Goal\n")

        chat.run_resumed_turn = AsyncMock(side_effect=write_handoff)
        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(_thread())

        kwargs = chat.spawn_session.await_args.kwargs
        assert kwargs["thread_name"] == "realpage · part 2"
        assert kwargs["working_dir"] == str(tmp_path)
        assert "handoffs" in chat.spawn_session.await_args.args[1]

    async def test_missing_handoff_stays_put(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path)
        chat.run_resumed_turn = AsyncMock()  # session never writes the file
        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(_thread())
        chat.spawn_session.assert_not_called()

    @pytest.mark.parametrize("skip", [True, False])
    async def test_skipped_threads_are_never_checked(self, tmp_path: Path, skip: bool) -> None:
        chat = _chat(tmp_path)
        nudger = ContextNudger(chat)
        thread = _thread()
        if skip:
            nudger.skip_thread_ids.add(thread.id)
        with patch.object(nudger, "_check", AsyncMock()) as check:
            nudger.after_turn(thread)
            for t in list(nudger._tasks):
                await t
        assert check.called is (not skip)
