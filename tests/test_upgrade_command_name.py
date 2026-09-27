"""The safe-restart slash command can be named for what it does on a deployment.

On a self-hosted checkout ``/upgrade`` upgrades nothing — it drains sessions and
restarts, and pre-start pulls the code. An operator reading the menu cannot guess
that from "upgrade", so the name is configurable. The default stays ``upgrade``
so existing consumers see no change.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import discord
import pytest
from discord.ext import commands

from claude_discord.cogs.auto_upgrade import AutoUpgradeCog, UpgradeConfig


def _bot() -> commands.Bot:
    return commands.Bot(command_prefix="!", intents=discord.Intents.none())


@pytest.mark.asyncio
async def test_default_name_is_unchanged() -> None:
    bot = _bot()
    await bot.add_cog(AutoUpgradeCog(bot, UpgradeConfig(package_name="p")))
    assert [c.name for c in bot.tree.get_commands()] == ["upgrade"]


@pytest.mark.asyncio
async def test_configured_name_and_description_reach_the_tree() -> None:
    bot = _bot()
    config = UpgradeConfig(
        package_name="p",
        slash_command_name="restart",
        slash_command_description="Restart the bot once running sessions finish",
    )
    await bot.add_cog(AutoUpgradeCog(bot, config))

    (command,) = bot.tree.get_commands()
    assert command.name == "restart"
    assert command.description == "Restart the bot once running sessions finish"
    assert bot.tree.get_command("upgrade") is None


@pytest.mark.asyncio
async def test_two_bots_do_not_share_a_renamed_command() -> None:
    """The rename touches the instance's copy, never the class-level definition."""
    renamed, plain = _bot(), _bot()
    await renamed.add_cog(
        AutoUpgradeCog(renamed, UpgradeConfig(package_name="p", slash_command_name="restart"))
    )
    await plain.add_cog(AutoUpgradeCog(plain, UpgradeConfig(package_name="p")))
    assert [c.name for c in plain.tree.get_commands()] == ["upgrade"]


def test_ebibot_calls_it_restart() -> None:
    path = Path(__file__).resolve().parent.parent / "examples/ebibot/cogs/auto_upgrade.py"
    spec = importlib.util.spec_from_file_location("_ebibot_upgrade_name", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.EBIBOT_UPGRADE_CONFIG.slash_command_name == "restart"
