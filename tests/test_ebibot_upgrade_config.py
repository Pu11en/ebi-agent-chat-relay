"""``/upgrade`` must be able to restart *this* machine's bot, not a hypothetical one.

The shipped configuration pointed at ``~/discord-bot`` and ``discord-bot.service``
with ``sudo`` in front of it. None of the three exist on a systemd **user**
deployment, so the drain-aware safe restart — the one mechanism that guarantees a
restart never kills an in-flight session — had never once run. Fixes therefore sat
undeployed, which is the expensive failure: not a broken command, a bot nobody
dares restart.

These tests assert the properties that made it unrunnable, so the config cannot
regress to naming things that are not there:

* no ``sudo`` — a user unit never needs it, and asking for a password from a
  subprocess with no tty hangs until the step timeout instead of failing;
* the restart is **detached** from the bot's own cgroup. ``systemctl restart``
  run as a direct child is inside the unit being stopped, so systemd kills the
  client while its request is in flight;
* ``working_dir`` is a real directory — a missing cwd makes
  ``create_subprocess_exec`` raise before any output reaches the thread;
* ``sync`` never runs a bare ``uv sync``, which prunes the optional extras
  ``pre-start.sh`` installs (voice, deepseek) and would break every dsh thread;
* no approval gate. Typing ``/upgrade`` *is* the approval, and the gate is
  answered only by a reaction or a button — neither of which is reachable if the
  operator answers in text.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
_COG = _REPO / "examples" / "ebibot" / "cogs" / "auto_upgrade.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_ebibot_auto_upgrade", _COG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config() -> Any:
    return _load().EBIBOT_UPGRADE_CONFIG


def test_working_dir_exists(config: Any) -> None:
    """A cwd that is not there fails before a single step can report why."""
    assert Path(config.working_dir).is_dir(), config.working_dir


def test_working_dir_defaults_to_the_checkout_the_cog_ships_in(config: Any) -> None:
    """The bot runs *from* its checkout, so that checkout is the only right default."""
    assert Path(config.working_dir).resolve() == _REPO


def test_no_sudo_anywhere(config: Any) -> None:
    commands = [
        config.upgrade_command,
        config.sync_command,
        config.restart_command,
    ]
    for command in commands:
        assert command is not None
        joined = " ".join(command)
        assert "sudo" not in joined, joined


def test_restart_targets_a_user_unit(config: Any) -> None:
    assert config.restart_command is not None
    assert "--user" in config.restart_command
    assert "discord-bot.service" not in config.restart_command


def test_restart_is_detached_from_the_bots_own_cgroup(config: Any) -> None:
    """systemd stops the whole unit, including a plain ``systemctl`` child of it."""
    assert config.restart_command is not None
    assert config.restart_command[0].endswith("systemd-run")


def test_every_command_names_a_real_executable(config: Any) -> None:
    for command in (config.upgrade_command, config.sync_command, config.restart_command):
        assert command is not None
        binary = command[0]
        found = Path(binary).is_file() if "/" in binary else shutil.which(binary)
        assert found, binary


def test_sync_does_not_prune_the_optional_extras(config: Any) -> None:
    """``uv sync`` without the extras removes the voice and dsh dependencies."""
    assert config.sync_command is not None
    if config.sync_command[:2] == ["uv", "sync"] or config.sync_command[1:2] == ["sync"]:
        assert "--extra" in config.sync_command, config.sync_command


def test_no_approval_gate(config: Any) -> None:
    """The gate is answerable only by reaction or button, so it is a dead end here."""
    assert config.upgrade_approval is False
    assert config.restart_approval is False


def test_slash_command_stays_enabled(config: Any) -> None:
    assert config.slash_command_enabled is True


@pytest.mark.asyncio
async def test_setup_waits_longer_than_a_single_claude_turn() -> None:
    """300s drains mid-turn. The proven restart script allows 480s; match it."""
    module = _load()
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await module.setup(bot, MagicMock(), MagicMock())

    bot.add_cog.assert_awaited_once()
    cog = bot.add_cog.await_args.args[0]
    assert cog._drain_timeout >= 480
