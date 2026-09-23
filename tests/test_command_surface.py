"""The command surface decides *where* something is allowed, using plain data.

Every test here runs without a Discord client, a gateway, or a mocked
interaction: the coordinator is fed integers and booleans that an adapter can
read off any interaction, so location rules can be argued about in isolation
from discord.py.
"""

from __future__ import annotations

import inspect

import pytest

from claude_discord import command_surface
from claude_discord.command_surface import (
    CONTROL_CENTER_BUTTONS,
    FINAL_COMMANDS,
    CommandSurface,
    InvocationPlace,
    SurfaceLocation,
)

CONTROL_CENTER_ID = 111
SESSION_CHANNEL_ID = 222

surface = CommandSurface.for_control_centers(CONTROL_CENTER_ID)


def test_configured_control_center_channel_is_the_control_center():
    place = InvocationPlace(channel_id=CONTROL_CENTER_ID)
    assert surface.classify(place) is SurfaceLocation.CONTROL_CENTER


def test_thread_bound_to_a_session_is_a_managed_session():
    place = InvocationPlace(
        channel_id=999, is_thread=True, parent_channel_id=SESSION_CHANNEL_ID, session_bound=True
    )
    assert surface.classify(place) is SurfaceLocation.MANAGED_SESSION


def test_thread_without_a_session_record_is_unsupported():
    place = InvocationPlace(channel_id=999, is_thread=True, parent_channel_id=SESSION_CHANNEL_ID)
    assert surface.classify(place) is SurfaceLocation.UNSUPPORTED


def test_thread_under_the_control_center_is_not_itself_the_control_center():
    """A thread inherits its parent's id but not its role."""
    place = InvocationPlace(channel_id=999, is_thread=True, parent_channel_id=CONTROL_CENTER_ID)
    assert surface.classify(place) is SurfaceLocation.UNSUPPORTED


def test_unrelated_channel_and_missing_channel_are_unsupported():
    assert surface.classify(InvocationPlace(channel_id=333)) is SurfaceLocation.UNSUPPORTED
    assert surface.classify(InvocationPlace()) is SurfaceLocation.UNSUPPORTED


def test_unconfigured_control_center_fails_closed():
    """With no control center configured, no channel may claim control actions."""
    blank = CommandSurface()
    assert blank.classify(InvocationPlace(channel_id=CONTROL_CENTER_ID)) is (
        SurfaceLocation.UNSUPPORTED
    )
    assert (
        blank.classify(InvocationPlace(channel_id=999, is_thread=True, session_bound=True))
        is SurfaceLocation.MANAGED_SESSION
    )


def test_several_control_centers_are_supported():
    many = CommandSurface.for_control_centers(CONTROL_CENTER_ID, 444, None)
    assert many.classify(InvocationPlace(channel_id=444)) is SurfaceLocation.CONTROL_CENTER


def test_final_registered_surface_is_exactly_eight_commands():
    names = [spec.name for spec in FINAL_COMMANDS]
    assert sorted(names) == sorted(
        ["new", "sessions", "settings", "help", "switch", "stop", "session", "close"]
    )
    assert len(names) == len(set(names)) == 8
    assert all(spec.summary for spec in FINAL_COMMANDS)


def test_each_location_lists_only_its_own_commands():
    control = [spec.name for spec in surface.commands_for(SurfaceLocation.CONTROL_CENTER)]
    session = [spec.name for spec in surface.commands_for(SurfaceLocation.MANAGED_SESSION)]
    assert control == ["new", "sessions", "settings", "help"]
    assert session == ["switch", "stop", "session", "close", "help"]
    assert surface.commands_for(SurfaceLocation.UNSUPPORTED) == ()
    assert set(control) | set(session) == {spec.name for spec in FINAL_COMMANDS}


@pytest.mark.parametrize("name", ["new", "sessions", "settings", "help"])
def test_control_center_commands_are_supported_there(name):
    assert surface.supports(name, SurfaceLocation.CONTROL_CENTER)
    assert surface.correction_for(name, SurfaceLocation.CONTROL_CENTER) is None


@pytest.mark.parametrize("name", ["switch", "stop", "session", "close"])
def test_session_commands_outside_a_session_are_corrected_not_run(name):
    assert not surface.supports(name, SurfaceLocation.CONTROL_CENTER)
    correction = surface.correction_for(name, SurfaceLocation.CONTROL_CENTER)
    assert correction and "session thread" in correction.lower()
    assert f"/{name}" in correction


@pytest.mark.parametrize("name", ["new", "sessions", "settings"])
def test_control_commands_inside_a_session_point_back_to_the_control_center(name):
    assert not surface.supports(name, SurfaceLocation.MANAGED_SESSION)
    correction = surface.correction_for(name, SurfaceLocation.MANAGED_SESSION)
    assert correction and "control center" in correction.lower()


def test_help_works_in_both_supported_locations():
    assert surface.supports("help", SurfaceLocation.CONTROL_CENTER)
    assert surface.supports("help", SurfaceLocation.MANAGED_SESSION)
    assert not surface.supports("help", SurfaceLocation.UNSUPPORTED)


def test_unsupported_location_and_unknown_command_fail_closed():
    assert not surface.supports("new", SurfaceLocation.UNSUPPORTED)
    assert surface.correction_for("new", SurfaceLocation.UNSUPPORTED)
    assert not surface.supports("launcher", SurfaceLocation.CONTROL_CENTER)
    assert surface.correction_for("launcher", SurfaceLocation.CONTROL_CENTER)


def test_control_center_buttons_mirror_the_control_commands():
    assert [button.label for button in CONTROL_CENTER_BUTTONS] == [
        "New session",
        "Sessions",
        "Settings",
    ]
    assert [button.command for button in CONTROL_CENTER_BUTTONS] == ["new", "sessions", "settings"]


def test_the_coordinator_stays_free_of_discord():
    """Location rules must be testable without a gateway connection."""
    source = inspect.getsource(command_surface)
    assert "import discord" not in source
    assert "discord" not in vars(command_surface)


def test_help_sections_describe_only_what_works_where_you_are():
    from claude_discord.command_surface import help_sections

    control = help_sections(SurfaceLocation.CONTROL_CENTER)
    assert [name for name, _ in control] == ["Buttons", "Commands"]
    buttons, commands = control[0][1], control[1][1]
    assert [line.split("**")[1] for line in buttons] == ["New session", "Sessions", "Settings"]
    assert [line.split("`")[1] for line in commands] == [
        "/new",
        "/sessions",
        "/settings",
        "/help",
        "/cd <folder>",
    ]

    session = help_sections(SurfaceLocation.MANAGED_SESSION)
    buttons, commands = session[0][1], session[1][1]
    assert [line.split("**")[1] for line in buttons] == ["Switch", "Stop", "Session", "Close"]
    assert [line.split("`")[1] for line in commands] == [
        "/switch",
        "/stop",
        "/session",
        "/close",
        "/help",
    ]
    assert help_sections(SurfaceLocation.UNSUPPORTED) == []


def test_cd_is_a_shortcut_for_new_not_a_ninth_command():
    from claude_discord.command_surface import SHORTCUT_ALIASES

    assert SHORTCUT_ALIASES == {"cd": "new", "cdnew": "new"}
    assert len(FINAL_COMMANDS) == 8, "aliases must not grow the registered surface"
    assert surface.supports("cd", SurfaceLocation.CONTROL_CENTER)
    assert surface.supports("cdnew", SurfaceLocation.CONTROL_CENTER)
    assert surface.correction_for("cd", SurfaceLocation.CONTROL_CENTER) is None
    assert "control center" in (surface.correction_for("cd", SurfaceLocation.MANAGED_SESSION) or "")


def test_help_tells_the_control_center_about_the_cd_shortcut():
    from claude_discord.command_surface import help_sections

    sections = dict(help_sections(SurfaceLocation.CONTROL_CENTER))
    assert any(line.startswith("`/cd ") for line in sections["Commands"])
    session = dict(help_sections(SurfaceLocation.MANAGED_SESSION))
    assert not any("`/cd " in line for line in session["Commands"])
