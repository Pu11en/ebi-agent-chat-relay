"""MyAISetupCog: the Settings entry, the ephemeral view, and the Setup Agent handoff.

The Cog owns no command.  It registers one :class:`SettingsEntry` (the
command-surface build owns Settings and its navigation), and pressing that
button runs :meth:`open`: authorize, collect the local inventory from the
declared roots, load the stored remote snapshots, render Browse by kind and
send it ephemerally.  No model runs; the only file reads are the adapters',
inside their declared roots, and the only write is the safe snapshot into
the repository.

**Ask Setup Agent** (task 4.4) is the one way out: it creates a normal
session through ``ClaudeChatCog.spawn_session`` — the same contract the API
and the launcher use — with the bounded safe packet from
:mod:`claude_discord.ai_setup_agent` as the first prompt.  Pressing the
button edits nothing; whatever the user then asks for in that session goes
through the ordinary task authority.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import discord
from discord.ext import commands

from ..ai_setup_agent import build_setup_agent_packet
from ..ai_setup_collector import CollectionContext, InventoryCollector
from ..ai_setup_inventory import InventoryItem, InventorySnapshot, normalize_token
from ..ai_setup_redaction import RedactionError
from ..category_scope import category_allowed
from ..database.ai_setup_repo import AISetupRepository
from ..discord_ui.my_ai_setup import SetupViewContext, ViewState, render
from ..discord_ui.settings_home import SettingsEntry, SettingsHome, register_entry

logger = logging.getLogger(__name__)

ENTRY_KEY = "ai-setup"
ENTRY_LABEL = "My AI Setup"
ENTRY_DESCRIPTION = (
    "What you added to Claude, Codex and DSH, where it lives, and how your computers compare."
)
HARNESSES: tuple[str, ...] = ("claude", "codex", "dsh", "ccdb")
OBSERVED_LIMIT = 10


def _now() -> datetime:
    return datetime.now(UTC)


class MyAISetupCog(commands.Cog):
    """Settings → My AI Setup. Read-only inventory; one safe handoff."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        repo: AISetupRepository,
        collector: InventoryCollector,
        chat: Any | None = None,
        allowed_user_ids: set[int] | None = None,
        owner: str | None = None,
        settings_home: SettingsHome | None = None,
        trusted_computers: Iterable[str] = (),
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.collector = collector
        self.chat = chat
        self.allowed_user_ids = allowed_user_ids
        self.owner = owner or "owner"
        self.settings_home = settings_home
        self.trusted_computers = tuple(
            normalize_token(name, kind="computer") for name in trusted_computers
        )
        self.clock = clock

    # -- Settings registration ---------------------------------------------------

    @property
    def entry(self) -> SettingsEntry:
        return SettingsEntry(ENTRY_KEY, ENTRY_LABEL, ENTRY_DESCRIPTION, open=self.open)

    async def cog_load(self) -> None:
        register_entry(self.entry)
        if self.settings_home is not None:
            self.settings_home.add(self.entry)

    # -- identity -------------------------------------------------------------------

    def computer_name(self) -> str:
        fallback = self.bot.user.display_name if self.bot.user else "This computer"
        return (os.environ.get("CCDB_COMPUTER_NAME", "").strip() or fallback)[:180]

    def computer(self) -> str:
        return normalize_token(self.computer_name(), kind="computer")

    # -- authorization --------------------------------------------------------------

    def authorized(self, interaction: discord.Interaction) -> bool:
        allowed = self.allowed_user_ids
        if allowed is None and self.chat is not None:
            allowed = getattr(self.chat, "_allowed_user_ids", None)
        if interaction.guild_id is None or not category_allowed(interaction.channel):
            return False
        return allowed is None or interaction.user.id in allowed

    async def _refuse(self, interaction: discord.Interaction) -> None:
        await _say(
            interaction,
            "My AI Setup is available from this computer's Settings, in its channel, "
            "with an authorized account.",
        )

    # -- collection -----------------------------------------------------------------

    def _context(self) -> CollectionContext:
        return CollectionContext(
            computer=self.computer(),
            owner=self.owner,
            collected_at=self.clock(),
            harnesses=HARNESSES,
            source_label="local collection",
        )

    async def collect_local(self) -> InventorySnapshot:
        """Collect off the event loop (the adapters read files) and store the result."""
        context = self._context()
        result = await asyncio.to_thread(self.collector.collect, context)
        for name in result.failed_adapters:
            logger.warning("My AI Setup: the %s source failed and was skipped", name)
        try:
            await self.repo.save_snapshot(result.snapshot)
        except RedactionError as error:
            logger.error("My AI Setup: snapshot not stored — %s", error)
        return result.snapshot

    async def build_context(self) -> SetupViewContext:
        local = await self.collect_local()
        computers = self.trusted_computers or tuple(
            name for name in await self.repo.list_computers() if name != local.computer
        )
        remotes: dict[str, InventorySnapshot | None] = {}
        exceptions: dict[str, tuple[Any, ...]] = {}
        for name in computers:
            if name == local.computer:
                continue
            remotes[name] = await self.repo.load_snapshot(name)
            exceptions[name] = await self.repo.load_exceptions(name)
        observed = await self.repo.recent_changes(local.computer, limit=OBSERVED_LIMIT)
        return SetupViewContext(
            computer_name=self.computer_name(),
            local=local,
            now=self.clock(),
            remotes=remotes,
            exceptions=exceptions,
            observed=observed,
            agent=self.ask_setup_agent if self.chat is not None else None,
            refresh=self.refresh,
        )

    # -- the entry point ------------------------------------------------------------

    async def open(self, interaction: discord.Interaction) -> None:
        """Settings → My AI Setup: Browse by kind, ephemeral, no model turn."""
        if not self.authorized(interaction):
            await self._refuse(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        ctx = await self.build_context()
        text, view = render(ctx, ViewState(), user_id=interaction.user.id)
        await interaction.followup.send(text, view=view, ephemeral=True)

    async def refresh(self, interaction: discord.Interaction) -> None:
        """Collect again and replace the same ephemeral message."""
        if not self.authorized(interaction):
            await self._refuse(interaction)
            return
        ctx = await self.build_context()
        text, view = render(ctx, ViewState(), user_id=interaction.user.id)
        await interaction.response.edit_message(content=text, view=view)

    # -- Ask Setup Agent (task 4.4) ---------------------------------------------------

    async def ask_setup_agent(
        self, interaction: discord.Interaction, item: InventoryItem, question: str
    ) -> None:
        """One normal session, the safe packet as its first prompt, nothing edited."""
        if not self.authorized(interaction):
            await self._refuse(interaction)
            return
        if self.chat is None:
            await _say(interaction, "Ask Setup Agent is not available here (no session backend).")
            return
        channel: Any = interaction.channel
        if isinstance(channel, discord.Thread):
            channel = channel.parent
        if channel is None:
            await _say(interaction, "Ask Setup Agent needs a text channel to open the session in.")
            return
        packet = build_setup_agent_packet(item, question=question, computer=self.computer_name())
        await interaction.response.defer(ephemeral=True)
        try:
            thread = await self.chat.spawn_session(
                channel,
                prompt=packet,
                thread_name=f"Setup: {item.display_name}"[:100],
                invite_user_id=interaction.user.id,
            )
        except Exception:  # noqa: BLE001 — the user gets a sentence, the log gets the trace
            logger.exception("My AI Setup: could not open a Setup Agent session")
            await interaction.followup.send(
                "The Setup Agent session could not be opened; nothing was changed.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Opened {getattr(thread, 'mention', 'a session')} with the item's safe facts "
            "and your question. Nothing was changed.",
            ephemeral=True,
        )


async def _say(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


__all__ = ["ENTRY_DESCRIPTION", "ENTRY_KEY", "ENTRY_LABEL", "MyAISetupCog"]
