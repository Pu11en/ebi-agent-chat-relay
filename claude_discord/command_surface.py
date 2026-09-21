"""Where a command is allowed to run, decided from plain data.

Discord hands every guild the same command tree. It gives a bot no reliable way
to show `/close` inside a session thread and hide it in the channel next door,
so "location aware" here cannot mean a changing command list — it means one
small registered union, buttons and help that differ by place, and a runtime
check that refuses a command used in the wrong place without touching state.

That check is the whole job of this module, and it is deliberately ignorant of
the chat client: an adapter reads a channel id, whether the channel is a thread
and whether a session record is bound to it, and passes those in as an
:class:`InvocationPlace`. Keeping the rules on plain integers and booleans is
what lets both a slash command and a button ask the same question, and what
lets the answer be argued about in a unit test with no gateway attached.

Two defaults fail closed on purpose. A channel is a control center only when the
instance configured it as one — an unconfigured bot exposes no control actions
anywhere rather than treating every channel as a lobby. And a thread is a
managed session only when a durable session record is bound to it, so an
ordinary thread under a configured channel does not inherit session commands.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SurfaceLocation(Enum):
    """The three places a command can be invoked from."""

    CONTROL_CENTER = "control_center"
    MANAGED_SESSION = "managed_session"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class InvocationPlace:
    """What an adapter knows about where a command was invoked.

    ``session_bound`` is the caller's answer to "does a stored session belong to
    this thread?" — the lookup stays with the adapter so this module needs no
    repository and no I/O.
    """

    channel_id: int | None = None
    is_thread: bool = False
    parent_channel_id: int | None = None
    session_bound: bool = False


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One registered slash command and the locations that may run it."""

    name: str
    summary: str
    locations: frozenset[SurfaceLocation]


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """A control-row button and the command it mirrors."""

    label: str
    command: str


# The final registered surface, in the order each location should present it.
# Nothing outside this tuple is a supported command anywhere; `/help` is the one
# entry that belongs to both places.
FINAL_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "new",
        "Start a new session in a folder on this computer",
        frozenset({SurfaceLocation.CONTROL_CENTER}),
    ),
    CommandSpec(
        "sessions",
        "Find a session, then open, close, or start another in its folder",
        frozenset({SurfaceLocation.CONTROL_CENTER}),
    ),
    CommandSpec(
        "settings",
        "Open the settings this computer supports",
        frozenset({SurfaceLocation.CONTROL_CENTER}),
    ),
    CommandSpec(
        "switch",
        "Choose the model for this session",
        frozenset({SurfaceLocation.MANAGED_SESSION}),
    ),
    CommandSpec(
        "stop",
        "Interrupt the running turn and keep the session open",
        frozenset({SurfaceLocation.MANAGED_SESSION}),
    ),
    CommandSpec(
        "session",
        "Fork, rewind, compact, clear, context, or goal",
        frozenset({SurfaceLocation.MANAGED_SESSION}),
    ),
    CommandSpec(
        "close",
        "Wrap up and archive this session without losing it",
        frozenset({SurfaceLocation.MANAGED_SESSION}),
    ),
    CommandSpec(
        "help",
        "List what works where you are",
        frozenset({SurfaceLocation.CONTROL_CENTER, SurfaceLocation.MANAGED_SESSION}),
    ),
)

CONTROL_CENTER_BUTTONS: tuple[ButtonSpec, ...] = (
    ButtonSpec("New session", "new"),
    ButtonSpec("Sessions", "sessions"),
    ButtonSpec("Settings", "settings"),
)


@dataclass(frozen=True, slots=True)
class CommandSurface:
    """Classifies a place and says which commands that place supports."""

    control_center_ids: frozenset[int] = field(default_factory=frozenset)

    @classmethod
    def for_control_centers(cls, *channel_ids: int | None) -> CommandSurface:
        """Build from configured ids, ignoring the unset ones."""
        return cls(frozenset(cid for cid in channel_ids if cid is not None))

    @classmethod
    def from_ids(cls, channel_ids: Iterable[int | None]) -> CommandSurface:
        return cls.for_control_centers(*channel_ids)

    def classify(self, place: InvocationPlace) -> SurfaceLocation:
        if place.is_thread:
            if place.session_bound:
                return SurfaceLocation.MANAGED_SESSION
            return SurfaceLocation.UNSUPPORTED
        if place.channel_id is not None and place.channel_id in self.control_center_ids:
            return SurfaceLocation.CONTROL_CENTER
        return SurfaceLocation.UNSUPPORTED

    def commands_for(self, location: SurfaceLocation) -> tuple[CommandSpec, ...]:
        return tuple(spec for spec in FINAL_COMMANDS if location in spec.locations)

    def supports(self, command: str, location: SurfaceLocation) -> bool:
        spec = _spec_for(command)
        return spec is not None and location in spec.locations

    def correction_for(self, command: str, location: SurfaceLocation) -> str | None:
        """Plain guidance for a command used in the wrong place, or ``None``.

        A caller that gets a string must send it and change nothing else.
        """
        if self.supports(command, location):
            return None
        spec = _spec_for(command)
        if spec is None:
            return f"`/{command}` is not one of this computer's commands. Try `/help`."
        if SurfaceLocation.MANAGED_SESSION in spec.locations and (
            SurfaceLocation.CONTROL_CENTER not in spec.locations
        ):
            return f"`/{command}` works inside a session thread. Open a session first."
        if SurfaceLocation.CONTROL_CENTER in spec.locations and (
            SurfaceLocation.MANAGED_SESSION not in spec.locations
        ):
            return f"`/{command}` works in this computer's control center."
        return f"`/{command}` works in the control center or inside a session thread."


#: The session-thread actions `/help` describes there; each mirrors one command.
SESSION_ACTIONS: tuple[ButtonSpec, ...] = (
    ButtonSpec("Switch", "switch"),
    ButtonSpec("Stop", "stop"),
    ButtonSpec("Session", "session"),
    ButtonSpec("Close", "close"),
)


def help_sections(location: SurfaceLocation) -> list[tuple[str, list[str]]]:
    """What `/help` says in ``location``: the actions there, then its commands.

    Plain strings so any frontend can render them; an unsupported location
    gets nothing, because there is nothing that works there.
    """
    if location is SurfaceLocation.CONTROL_CENTER:
        actions = CONTROL_CENTER_BUTTONS
    elif location is SurfaceLocation.MANAGED_SESSION:
        actions = SESSION_ACTIONS
    else:
        return []
    by_name = {spec.name: spec for spec in FINAL_COMMANDS}
    buttons = [f"**{action.label}** — {by_name[action.command].summary}" for action in actions]
    commands = [
        f"`/{spec.name}` — {spec.summary}" for spec in FINAL_COMMANDS if location in spec.locations
    ]
    return [("Buttons", buttons), ("Commands", commands)]


#: Retirement of superseded commands (task 4.4) is a switch, off by default:
#: it may only be turned on after the operator has recorded acceptance of the
#: replacement surface in Discord. It removes registrations only — no service
#: code and no stored state — so turning it back off restores the old commands.
RETIREMENT_ENV = "CCDB_RETIRE_SUPERSEDED_COMMANDS"
#: Comma-separated command names an instance keeps registered despite retirement
#: (its own custom-cog commands, for example).
RETIREMENT_KEEP_ENV = "CCDB_RETIREMENT_KEEP"


def retirement_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(RETIREMENT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def retire_superseded_commands(
    tree: Any,
    *,
    enabled: bool | None = None,
    keep: Iterable[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Remove every top-level command outside the final eight; returns what was removed.

    ``tree`` is a ``discord.app_commands.CommandTree`` (or anything with
    ``get_commands()`` and ``remove_command(name)``). With retirement off —
    the default — nothing is touched.
    """
    source = os.environ if env is None else env
    if enabled is None:
        enabled = retirement_enabled(source)
    if not enabled:
        return []
    spared = {spec.name for spec in FINAL_COMMANDS}
    spared.update(name.strip() for name in (keep or ()) if name.strip())
    spared.update(
        name.strip() for name in source.get(RETIREMENT_KEEP_ENV, "").split(",") if name.strip()
    )
    removed: list[str] = []
    for command in list(tree.get_commands()):
        if command.name in spared:
            continue
        tree.remove_command(command.name)
        removed.append(command.name)
    return removed


def _spec_for(command: str) -> CommandSpec | None:
    return next((spec for spec in FINAL_COMMANDS if spec.name == command), None)
