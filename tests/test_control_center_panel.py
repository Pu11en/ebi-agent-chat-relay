"""Control center without buttons: you type, and that is the whole surface.

The channel accumulated two panels built at different times — a pinned
"Sessions" embed (Favorite folders / New session / Resume) and a second row
republished under every message (New session / Sessions / Settings). "Sessions"
named two different things, "New session" appeared twice and opened two
different menus, and seven slash commands covered the same ground again. Three
vocabularies for one idea is not a launcher, it is a quiz.

``CCDB_CONTROL_CENTER_PANEL=off`` removes both. It has to *remove* them, not
merely stop publishing new ones: a switch that leaves the old buttons pinned
forever has changed nothing a person can see.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.project_launcher import ProjectLauncherCog, control_panel_enabled

pytestmark = pytest.mark.asyncio


class _Settings:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str, *, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


def _cog(settings: _Settings, channel: object | None):
    cog = ProjectLauncherCog.__new__(ProjectLauncherCog)
    ProjectLauncherCog.__init__(
        cog,
        MagicMock(),
        MagicMock(),
        settings,
        MagicMock(),
        channel_id=500,
        channel_ids={500},
    )
    cog.bot.get_channel = MagicMock(return_value=channel)
    cog.bot.user = SimpleNamespace(id=9, display_name="bot")
    return cog


def _channel(messages: dict[int, object]):
    channel = MagicMock(spec=discord.TextChannel)

    async def fetch(message_id: int):
        if message_id not in messages:
            raise discord.NotFound(MagicMock(status=404), "gone")
        return messages[message_id]

    channel.fetch_message = AsyncMock(side_effect=fetch)
    channel.send = AsyncMock()
    return channel


def _bot_message(message_id: int, *, pinned: bool = False):
    message = MagicMock()
    message.id = message_id
    message.author = SimpleNamespace(id=9)
    message.pinned = pinned
    message.delete = AsyncMock()
    message.unpin = AsyncMock()
    message.pin = AsyncMock()
    message.edit = AsyncMock()
    return message


# --- the switch itself ------------------------------------------------------


async def test_the_panel_is_on_unless_it_is_turned_off(monkeypatch):
    monkeypatch.delenv("CCDB_CONTROL_CENTER_PANEL", raising=False)
    assert control_panel_enabled() is True
    for off in ("off", "0", "false", "no", "OFF", " off "):
        assert control_panel_enabled({"CCDB_CONTROL_CENTER_PANEL": off}) is False
    for on in ("on", "1", "true", ""):
        assert control_panel_enabled({"CCDB_CONTROL_CENTER_PANEL": on}) is True


# --- nothing new is published ----------------------------------------------


async def test_startup_publishes_no_panel_when_it_is_off(monkeypatch):
    monkeypatch.setenv("CCDB_CONTROL_CENTER_PANEL", "off")
    channel = _channel({})
    cog = _cog(_Settings(), channel)
    await ProjectLauncherCog.on_ready(cog)
    channel.send.assert_not_awaited()


async def test_a_message_schedules_no_control_row_when_it_is_off(monkeypatch):
    monkeypatch.setenv("CCDB_CONTROL_CENTER_PANEL", "off")
    cog = _cog(_Settings(), _channel({}))
    message = SimpleNamespace(
        channel=SimpleNamespace(id=500),
        type=discord.MessageType.default,
        author=SimpleNamespace(id=42),
        components=[],
    )
    await ProjectLauncherCog.keep_launcher_visible(cog, message)
    assert cog._shortcut_task is None


# --- and what is already there is taken down -------------------------------


async def test_startup_takes_down_the_panel_and_the_row_it_left_behind(monkeypatch):
    monkeypatch.setenv("CCDB_CONTROL_CENTER_PANEL", "off")
    panel = _bot_message(111, pinned=True)
    row = _bot_message(222)
    settings = _Settings({"launcher.panel:500": "111", "launcher.shortcut:500": "222"})
    cog = _cog(settings, _channel({111: panel, 222: row}))

    await ProjectLauncherCog.on_ready(cog)

    panel.unpin.assert_awaited_once()
    panel.delete.assert_awaited_once()
    row.delete.assert_awaited_once()
    assert "launcher.panel:500" not in settings.values
    assert "launcher.shortcut:500" not in settings.values


async def test_a_message_that_is_already_gone_is_not_an_error(monkeypatch):
    monkeypatch.setenv("CCDB_CONTROL_CENTER_PANEL", "off")
    settings = _Settings({"launcher.panel:500": "111"})
    cog = _cog(settings, _channel({}))
    await ProjectLauncherCog.on_ready(cog)
    assert "launcher.panel:500" not in settings.values


async def test_a_message_this_bot_does_not_own_is_left_alone(monkeypatch):
    """Only ccdb's own tracked messages are removed — never someone else's post."""
    monkeypatch.setenv("CCDB_CONTROL_CENTER_PANEL", "off")
    someone_else = _bot_message(111)
    someone_else.author = SimpleNamespace(id=1234)
    cog = _cog(_Settings({"launcher.panel:500": "111"}), _channel({111: someone_else}))
    await ProjectLauncherCog.on_ready(cog)
    someone_else.delete.assert_not_awaited()


# --- the default is untouched ----------------------------------------------


async def test_with_the_panel_on_startup_still_publishes_and_pins_it(monkeypatch):
    monkeypatch.delenv("CCDB_CONTROL_CENTER_PANEL", raising=False)
    monkeypatch.setenv("CCDB_COMPUTER_NAME", "this computer")
    channel = _channel({})
    posted = _bot_message(333)
    channel.send = AsyncMock(return_value=posted)
    settings = _Settings()
    cog = _cog(settings, channel)
    cog.control_content = AsyncMock(return_value="status")

    await ProjectLauncherCog.on_ready(cog)

    channel.send.assert_awaited()
    posted.pin.assert_awaited_once()
    assert settings.values["launcher.panel:500"] == "333"
