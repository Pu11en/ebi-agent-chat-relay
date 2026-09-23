"""Settings: the entry point to what *this* computer can configure.

Every computer runs the same framework but not the same harnesses or
subscription, so the settings it presents differ. The rule here is the
"unsupported" scenario from the spec: an entry a computer cannot honour is
absent, never shown as a working choice. Support is decided from plain data
(:class:`SupportedFeatures`) so it can be tested without Discord and overridden
per instance with two environment lists.

Opening Settings runs no model. Entries either carry an ``open`` coroutine (a
button) or only a hint naming the slash command that already does the job.

Extension point
---------------
Another feature adds itself with one call and no subclassing::

    from claude_discord.discord_ui.settings_home import SettingsEntry, register_entry

    register_entry(SettingsEntry("ai-setup", "My AI Setup", "What is installed", open=opener))

:func:`register_entry` feeds every :class:`SettingsHome` built afterwards;
:meth:`SettingsHome.add` does the same for one live home (the launcher exposes
its home as ``ProjectLauncherCog.settings_home``). Entries replace by ``key``.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

import discord

Opener = Callable[[discord.Interaction], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SettingsEntry:
    """One thing Settings can show.

    ``harnesses`` names the harnesses that must be configured for the entry to
    make sense (all of them, or any one when ``any_harness`` is set); empty
    means any computer. ``features`` names subscription features the instance
    must declare. ``open`` makes the entry a button; without it the entry is a
    line of text pointing at ``hint`` (a slash command).
    """

    key: str
    label: str
    description: str
    harnesses: frozenset[str] = field(default_factory=frozenset)
    features: frozenset[str] = field(default_factory=frozenset)
    any_harness: bool = False
    hint: str | None = None
    open: Opener | None = None


@dataclass(frozen=True, slots=True)
class SupportedFeatures:
    """Which harnesses are configured here and which subscription features are on."""

    harnesses: frozenset[str]
    features: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def detect(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        codex_available: bool | None = None,
    ) -> SupportedFeatures:
        """Read the instance's configuration; two env lists override detection.

        ``CCDB_SUPPORTED_HARNESSES`` and ``CCDB_SUBSCRIPTION_FEATURES`` are
        comma-separated. Without them: Claude is always present, Codex when
        its CLI is on PATH, and local / dsh / agui when their addresses are set.
        """
        source = os.environ if env is None else env
        explicit = _csv(source.get("CCDB_SUPPORTED_HARNESSES"))
        if explicit:
            harnesses = explicit
        else:
            if codex_available is None:
                codex_command = source.get("CCDB_CODEX_COMMAND") or "codex"
                codex_available = shutil.which(codex_command) is not None
            harnesses = {"claude"}
            if codex_available:
                harnesses.add("codex")
            if source.get("CCDB_LOCAL_BASE_URL", "").strip():
                harnesses.add("local")
            if source.get("DSH_ROUTES", "").strip() or source.get("DSH_MODELS_PATH", "").strip():
                harnesses.add("dsh")
            if source.get("CCDB_AGUI_URL", "").strip():
                harnesses.add("agui")
        return cls(frozenset(harnesses), frozenset(_csv(source.get("CCDB_SUBSCRIPTION_FEATURES"))))

    def supports(self, entry: SettingsEntry) -> bool:
        if entry.harnesses:
            if entry.any_harness:
                if not (entry.harnesses & self.harnesses):
                    return False
            elif not entry.harnesses <= self.harnesses:
                return False
        return entry.features <= self.features


def _csv(raw: str | None) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def default_entries() -> list[SettingsEntry]:
    """The framework's own entries; each points at the command that already exists."""
    return [
        SettingsEntry(
            "model",
            "Model and harness",
            "Choose the model a session runs on; its harness follows automatically.",
            hint="/switch",
        ),
        SettingsEntry(
            "effort",
            "Reasoning effort",
            "How hard the model thinks per turn, for Claude or Codex.",
            harnesses=frozenset({"claude", "codex"}),
            any_harness=True,
            hint="/effort",
        ),
        SettingsEntry(
            "tools",
            "Allowed tools",
            "Which tools Claude may use in this computer's sessions.",
            harnesses=frozenset({"claude"}),
            hint="/tools-show",
        ),
        SettingsEntry(
            "ollama",
            "Local runtime",
            "What is installed and loaded in the local model runtime.",
            harnesses=frozenset({"local"}),
            hint="/ollama",
        ),
    ]


#: Entries added by other features before a home is built (see the module docstring).
_REGISTERED: list[SettingsEntry] = []


def register_entry(entry: SettingsEntry) -> None:
    """Add (or replace by key) an entry that every later :class:`SettingsHome` shows."""
    _REGISTERED[:] = [existing for existing in _REGISTERED if existing.key != entry.key]
    _REGISTERED.append(entry)


class SettingsHome:
    """The filtered list of entries for one computer, and the message that shows it."""

    def __init__(
        self, supported: SupportedFeatures, entries: Iterable[SettingsEntry] | None = None
    ) -> None:
        self.supported = supported
        self.entries: list[SettingsEntry] = list(
            entries if entries is not None else [*default_entries(), *_REGISTERED]
        )

    def add(self, entry: SettingsEntry) -> None:
        """The live extension point: add or replace one entry by key."""
        self.entries = [existing for existing in self.entries if existing.key != entry.key]
        self.entries.append(entry)

    def visible(self) -> list[SettingsEntry]:
        """Only what this computer supports; unsupported entries are absent."""
        return [entry for entry in self.entries if self.supported.supports(entry)]

    def render(self, computer_name: str, *, user_id: int = 0) -> tuple[str, SettingsHomeView]:
        visible = self.visible()
        lines = [f"**{computer_name} · Settings**"]
        harnesses = ", ".join(sorted(self.supported.harnesses)) or "none"
        lines.append(f"-# Configured harnesses: {harnesses}")
        for entry in visible:
            if entry.open is None:
                hint = f" — `{entry.hint}`" if entry.hint else ""
                lines.append(f"• **{entry.label}**{hint}: {entry.description}")
        if len(lines) == 2:
            lines.append("No settings apply to this computer's configuration.")
        return "\n".join(lines), SettingsHomeView(visible, user_id=user_id)


class SettingsHomeView(discord.ui.View):
    """One button per entry that can be opened; personal and short-lived."""

    def __init__(self, entries: Iterable[SettingsEntry], *, user_id: int) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        for entry in entries:
            if entry.open is None:
                continue
            self.add_item(_EntryButton(replace(entry)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.user_id and interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own Settings menu.", ephemeral=True)
            return False
        return True


class _EntryButton(discord.ui.Button[SettingsHomeView]):
    def __init__(self, entry: SettingsEntry) -> None:
        super().__init__(label=entry.label[:80], style=discord.ButtonStyle.secondary)
        self.entry = entry

    async def callback(self, interaction: discord.Interaction) -> None:
        opener = self.entry.open
        if opener is not None:
            await opener(interaction)
