"""Retirement runs when commands are synced, not when the bridge is wired.

``setup_bridge`` used to retire commands, but custom Cogs are loaded *after* it
returns — so a custom Cog's command was never on the tree when the keep list was
applied and could not be retired whatever the list said. ``/upgrade`` survived
for exactly that reason (21 retired, 23 expected). The one moment every Cog is
guaranteed to be registered is the sync in ``on_ready``, so that is where the
keep list is applied.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_discord.bot import ClaudeDiscordBot
from claude_discord.command_surface import (
    FINAL_COMMANDS,
    RETIREMENT_ENV,
    RETIREMENT_KEEP_ENV,
)


class RecordingTree:
    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        self.events: list[str] = []

    def get_commands(self):
        return [SimpleNamespace(name=name) for name in self.names]

    def remove_command(self, name: str):
        self.names.remove(name)
        self.events.append(f"remove:{name}")

    def copy_global_to(self, *, guild):
        self.events.append("copy")

    async def sync(self, *, guild):
        self.events.append("sync")
        return list(self.names)


def _bot(tree: RecordingTree) -> SimpleNamespace:
    return SimpleNamespace(tree=tree, guilds=[SimpleNamespace(name="g", id=1)])


@pytest.mark.asyncio
async def test_a_command_added_after_setup_is_retired_before_sync(monkeypatch) -> None:
    """``upgrade`` stands in for any command a custom Cog registers late."""
    monkeypatch.setenv(RETIREMENT_ENV, "1")
    monkeypatch.setenv(RETIREMENT_KEEP_ENV, "gowork")
    finals = [spec.name for spec in FINAL_COMMANDS]
    tree = RecordingTree([*finals, "gowork", "upgrade", "launcher"])

    await ClaudeDiscordBot._sync_commands(_bot(tree))  # type: ignore[arg-type]

    assert "upgrade" not in tree.names
    assert "launcher" not in tree.names
    assert "gowork" in tree.names
    assert tree.events.index("remove:upgrade") < tree.events.index("sync")


@pytest.mark.asyncio
async def test_sync_leaves_the_tree_alone_when_retirement_is_off(monkeypatch) -> None:
    monkeypatch.delenv(RETIREMENT_ENV, raising=False)
    tree = RecordingTree(["upgrade", "launcher"])

    await ClaudeDiscordBot._sync_commands(_bot(tree))  # type: ignore[arg-type]

    assert tree.names == ["upgrade", "launcher"]
    assert tree.events == ["copy", "sync"]


def test_setup_bridge_no_longer_retires() -> None:
    """Retiring there is too early; doing it in both places would hide the bug again."""
    import inspect

    from claude_discord import setup

    assert "retire_superseded_commands(" not in inspect.getsource(setup.setup_bridge)
