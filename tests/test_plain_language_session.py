"""Typing "make me a session about Boa in the boa folder" opens exactly one thread.

Control center turns a typed message into a quick chat. A message asking for a
folder-bound session used to get the same treatment: a quick chat thread, whose
agent would then open the *real* thread — two threads for one session, which is
the clutter the whole control center rework exists to remove. The folder is
resolved before anything is created, so one message makes one thread.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.cogs.project_launcher import ProjectLauncherCog
from claude_discord.session_request import SessionRequest

pytestmark = pytest.mark.asyncio


# --- matching what was said to a folder that exists ------------------------


def _launcher(recents: list[str], scan: list[str]):
    cog = ProjectLauncherCog.__new__(ProjectLauncherCog)
    ProjectLauncherCog.__init__(
        cog, MagicMock(), MagicMock(), MagicMock(), MagicMock(), channel_id=1, channel_ids={1}
    )
    cog.recent_folders = AsyncMock(return_value=recents)
    cog.scanned_folders = AsyncMock(return_value=scan)
    cog.favorites = AsyncMock(return_value=[])
    return cog


async def test_a_spoken_folder_name_finds_the_folder():
    cog = _launcher(["/p/boa"], ["/p/boa", "/p/aldus"])
    assert await cog.resolve_folder_phrase(1, 42, "boa") == "/p/boa"


async def test_the_folder_worked_in_most_recently_wins_a_tie(tmp_path):
    """Both are called "boa"; the one worked in last is the one meant.

    Real directories, because history is filtered against the disk — a recent
    folder that has since been deleted must not be offered.
    """
    shallow = tmp_path / "boa"
    deep = tmp_path / "clients" / "boa"
    deep.mkdir(parents=True)
    shallow.mkdir()
    cog = _launcher([str(deep), str(shallow)], [str(shallow), str(deep)])
    assert await cog.resolve_folder_phrase(1, 42, "boa") == str(deep)


async def test_a_folder_nobody_has_is_not_invented():
    """No match means no session — a quick chat is better than the wrong folder."""
    cog = _launcher([], ["/p/aldus", "/p/realpage"])
    assert await cog.resolve_folder_phrase(1, 42, "boa") is None


async def test_an_empty_phrase_matches_nothing():
    cog = _launcher(["/p/boa"], ["/p/boa"])
    assert await cog.resolve_folder_phrase(1, 42, "") is None


# --- the control-center hook ----------------------------------------------


def _chat(request: SessionRequest | None, resolved: str | None):
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    launcher = SimpleNamespace(
        resolve_folder_phrase=AsyncMock(return_value=resolved),
        recent_folders=AsyncMock(return_value=[]),
        scanned_folders=AsyncMock(return_value=[]),
    )
    cog = SimpleNamespace(
        bot=SimpleNamespace(cogs={"ProjectLauncherCog": launcher}),
        repo=SimpleNamespace(save=AsyncMock()),
        runner=SimpleNamespace(command="claude", _build_env=lambda: {}),
        _ensure_thread_members=AsyncMock(),
        _read_session_request=AsyncMock(return_value=request),
        _start_folder_thread=AsyncMock(),
    )
    cog._try_folder_session = lambda message: ClaudeChatCog._try_folder_session(cog, message)
    return cog, launcher


def _message(text: str):
    return SimpleNamespace(
        content=text,
        author=SimpleNamespace(id=42),
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=500),
    )


async def test_a_session_request_with_a_real_folder_opens_a_folder_thread():
    cog, _ = _chat(SessionRequest(folder="boa", task="plan the launch"), "/p/boa")
    assert await cog._try_folder_session(_message("make me a session about boa in boa")) is True
    cog._start_folder_thread.assert_awaited_once()


async def test_an_ordinary_message_is_left_to_the_quick_chat():
    cog, launcher = _chat(None, None)
    assert await cog._try_folder_session(_message("what does this repo do?")) is False
    cog._start_folder_thread.assert_not_awaited()
    launcher.resolve_folder_phrase.assert_not_awaited()


async def test_a_named_folder_that_does_not_exist_falls_back_to_a_quick_chat():
    """Better a plain chat than a session bound to the wrong folder."""
    cog, _ = _chat(SessionRequest(folder="nowhere", task="go"), None)
    assert await cog._try_folder_session(_message("session in nowhere")) is False
    cog._start_folder_thread.assert_not_awaited()


async def test_without_a_launcher_nothing_is_attempted():
    cog, _ = _chat(SessionRequest(folder="boa", task="go"), "/p/boa")
    cog.bot.cogs = {}
    assert await cog._try_folder_session(_message("session in boa")) is False


async def test_a_failure_anywhere_falls_back_instead_of_dropping_the_message():
    cog, _ = _chat(SessionRequest(folder="boa", task="go"), "/p/boa")
    cog._read_session_request = AsyncMock(side_effect=RuntimeError("model is down"))
    assert await cog._try_folder_session(_message("session in boa")) is False


# --- and the message handler actually consults it --------------------------


async def test_the_control_center_asks_before_opening_a_quick_chat(monkeypatch):
    import discord

    from claude_discord.cogs.claude_chat import ClaudeChatCog

    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", "500")
    cog = SimpleNamespace(
        _allowed_user_ids=None,
        _ensure_thread_members=AsyncMock(),
        _try_send_drewai_lookup_handoff=AsyncMock(return_value=False),
        _claimed_by_task_loop=lambda message: False,
        _is_no_mention_scope=lambda channel: True,
        _try_folder_session=AsyncMock(return_value=True),
        _handle_new_conversation=AsyncMock(),
    )
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=42),
        type=discord.MessageType.default,
        channel=SimpleNamespace(id=500),
        content="make me a session about boa in the boa folder",
    )
    await ClaudeChatCog.on_message(cog, message)
    cog._try_folder_session.assert_awaited_once()
    cog._handle_new_conversation.assert_not_awaited()


async def test_a_declined_request_still_gets_its_quick_chat(monkeypatch):
    import discord

    from claude_discord.cogs.claude_chat import ClaudeChatCog

    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    monkeypatch.setenv("CCDB_LAUNCHER_CHANNEL_ID", "500")
    cog = SimpleNamespace(
        _allowed_user_ids=None,
        _ensure_thread_members=AsyncMock(),
        _try_send_drewai_lookup_handoff=AsyncMock(return_value=False),
        _claimed_by_task_loop=lambda message: False,
        _is_no_mention_scope=lambda channel: True,
        _try_folder_session=AsyncMock(return_value=False),
        _handle_new_conversation=AsyncMock(),
    )
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=42),
        type=discord.MessageType.default,
        channel=SimpleNamespace(id=500),
        content="what does this repo do?",
    )
    await ClaudeChatCog.on_message(cog, message)
    cog._handle_new_conversation.assert_awaited_once()
