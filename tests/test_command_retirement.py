"""Retirement of superseded commands is a switch, off until Drew's acceptance (task 4.4).

The switch only touches command registration: no service code, no stored
state. A fake command tree records what would be removed.
"""

from __future__ import annotations

from types import SimpleNamespace

from claude_discord.command_surface import (
    FINAL_COMMANDS,
    RETIREMENT_ENV,
    RETIREMENT_KEEP_ENV,
    retire_superseded_commands,
    retirement_enabled,
)


class FakeTree:
    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        self.removed: list[str] = []

    def get_commands(self):
        return [SimpleNamespace(name=name) for name in self.names]

    def remove_command(self, name: str):
        self.names.remove(name)
        self.removed.append(name)


REGISTERED = [spec.name for spec in FINAL_COMMANDS] + [
    "launcher",
    "resume",
    "fork",
    "rewind",
    "compact",
    "clear",
    "goal",
    "context",
    "model",
    "gowork",
]


def test_retirement_is_off_by_default():
    assert retirement_enabled(env={}) is False
    assert retirement_enabled(env={RETIREMENT_ENV: "0"}) is False
    assert retirement_enabled(env={RETIREMENT_ENV: "1"}) is True
    assert retirement_enabled(env={RETIREMENT_ENV: "yes"}) is True


def test_disabled_retirement_removes_nothing():
    tree = FakeTree(REGISTERED)
    assert retire_superseded_commands(tree, enabled=False) == []
    assert tree.removed == []


def test_enabled_retirement_leaves_exactly_the_eight_final_commands():
    tree = FakeTree(REGISTERED)
    removed = retire_superseded_commands(tree, enabled=True)
    assert set(tree.names) == {spec.name for spec in FINAL_COMMANDS}
    assert len(tree.names) == 8
    assert "launcher" in removed and "fork" in removed and "gowork" in removed


def test_keep_list_spares_instance_commands():
    tree = FakeTree(REGISTERED)
    retire_superseded_commands(tree, enabled=True, keep={"gowork"})
    assert "gowork" in tree.names
    assert "launcher" not in tree.names


def test_keep_list_is_read_from_the_environment():
    tree = FakeTree(REGISTERED)
    env = {RETIREMENT_ENV: "1", RETIREMENT_KEEP_ENV: "gowork, resume"}
    retire_superseded_commands(tree, env=env)
    assert {"gowork", "resume"} <= set(tree.names)
    assert "launcher" not in tree.names


def test_retirement_spares_the_cd_shortcuts():
    tree = FakeTree([*REGISTERED, "cd", "cdnew"])
    removed = retire_superseded_commands(tree, enabled=True)
    assert {"cd", "cdnew"} <= set(tree.names)
    assert "launcher" in removed
