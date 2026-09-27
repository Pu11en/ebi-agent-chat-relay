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

    async def test_the_nudge_is_bound_to_the_chat_allowlist(self, tmp_path: Path) -> None:
        """ "Yes, start fresh" runs a model turn and opens a thread: only the
        people allowed to talk to the bot may press it."""
        chat = _chat(tmp_path)
        chat._allowed_user_ids = {11, 22}
        nudger = ContextNudger(chat)
        with _answer("no") as surface_cls:
            await nudger._check(_thread())
        assert surface_cls.call_args.kwargs["allowed_user_ids"] == {11, 22}

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


class TestHandoffIsClean:
    """A handoff that leaves work behind is not a handoff.

    Two things were left to the person by hand, and both are the kind of chore
    the flow exists to remove: the spoken tag stayed on the dead thread (so the
    word learned for this conversation addressed a session that was over), and
    the old thread stayed open (so it had to be closed manually, and until then
    it still showed as a session).
    """

    @staticmethod
    def _writes_handoff(chat: MagicMock) -> None:
        async def write_handoff(seed, thread, prompt):  # noqa: ANN001
            path = Path(prompt.split("Write a handoff file at ")[1].split(" (create")[0])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("## Goal\n")

        chat.run_resumed_turn = AsyncMock(side_effect=write_handoff)

    async def test_the_spoken_tag_moves_to_the_new_thread(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path)
        self._writes_handoff(chat)
        store = {"voice_label:42": "bravo", "voice_label:99": "alpha"}
        chat._settings_repo = MagicMock()
        chat._settings_repo.get = AsyncMock(side_effect=lambda k: store.get(k))
        chat._settings_repo.set = AsyncMock(side_effect=lambda k, v: store.__setitem__(k, v))
        chat._settings_repo.delete = AsyncMock(side_effect=lambda k: store.pop(k, None))
        new = chat.spawn_session.return_value
        new.id = 43
        new.name = "realpage · part 2"
        new.edit = AsyncMock()

        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(_thread())

        # The word the speaker learned now reaches the live conversation …
        assert store["voice_label:43"] == "bravo"
        # … and no longer reaches the finished one.
        assert "voice_label:42" not in store
        # Another thread's tag is untouched.
        assert store["voice_label:99"] == "alpha"
        # And it is visible in the sidebar without waiting for the next poll.
        assert new.edit.await_args.kwargs["name"] == "[bravo] realpage · part 2"

    async def test_the_finished_thread_stops_showing_the_tag(self, tmp_path: Path) -> None:
        """Two rows reading "[bravo]" with one of them dead is worse than none."""
        chat = _chat(tmp_path)
        self._writes_handoff(chat)
        chat._settings_repo = MagicMock()
        chat._settings_repo.get = AsyncMock(return_value="bravo")
        chat._settings_repo.set = AsyncMock()
        chat._settings_repo.delete = AsyncMock()
        new = chat.spawn_session.return_value
        new.id = 43
        new.name = "realpage · part 2"
        new.edit = AsyncMock()
        thread = _thread("[bravo] realpage")
        thread.edit = AsyncMock()

        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(thread)

        assert thread.edit.await_args.kwargs["name"] == "realpage"

    async def test_an_untagged_thread_hands_off_without_one(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path)
        self._writes_handoff(chat)
        chat._settings_repo = MagicMock()
        chat._settings_repo.get = AsyncMock(return_value=None)
        chat._settings_repo.set = AsyncMock()
        chat._settings_repo.delete = AsyncMock()
        new = chat.spawn_session.return_value
        new.id = 43
        new.name = "realpage · part 2"
        new.edit = AsyncMock()

        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(_thread())

        chat._settings_repo.set.assert_not_awaited()
        new.edit.assert_not_awaited()

    async def test_the_old_session_is_closed_for_you(self, tmp_path: Path) -> None:
        chat = _chat(tmp_path)
        self._writes_handoff(chat)
        chat._settings_repo = None
        chat.close_session = AsyncMock()
        thread = _thread()

        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(thread)

        chat.close_session.assert_awaited_once_with(thread)

    async def test_a_failed_handoff_leaves_the_session_alone(self, tmp_path: Path) -> None:
        """Nothing was carried over, so closing the only live session would lose it."""
        chat = _chat(tmp_path)
        chat.run_resumed_turn = AsyncMock()  # the file is never written
        chat._settings_repo = None
        chat.close_session = AsyncMock()

        nudger = ContextNudger(chat)
        with _answer("yes"):
            await nudger._check(_thread())

        chat.close_session.assert_not_awaited()
