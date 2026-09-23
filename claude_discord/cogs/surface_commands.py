"""The location-aware commands of the final surface, as thin adapters.

`/session` and `/close` belong to a managed session thread; `/new` and
`/settings` belong to a control center. Discord shows all of them everywhere,
so each handler first asks :class:`CommandSurface` where it was invoked and,
in the wrong place, sends the one-line correction and changes nothing.

Nothing here implements an action. Session actions call the services on
``ClaudeChatCog`` (3.1); Close calls ``SessionLifecycleService``; New and
Settings call the launcher's flows. That is what makes a button, a slash
command and an API call the same operation.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..category_scope import category_allowed
from ..command_surface import CommandSurface, InvocationPlace, SurfaceLocation
from ..discord_ui.session_actions import SessionActionsView
from ..session_lifecycle import CloseAuthorization, SessionLifecycleService, close_outcome_text
from .session_manage import context_embed

logger = logging.getLogger(__name__)


async def locate(
    surface: CommandSurface, repo: Any, interaction: discord.Interaction
) -> SurfaceLocation:
    """Classify where ``interaction`` happened, looking up the session binding."""
    channel = interaction.channel
    if isinstance(channel, discord.Thread):
        record = await repo.get(channel.id)
        place = InvocationPlace(
            channel_id=channel.id,
            is_thread=True,
            parent_channel_id=channel.parent_id,
            session_bound=record is not None,
        )
    else:
        place = InvocationPlace(channel_id=interaction.channel_id)
    return surface.classify(place)


class ChatSessionActions:
    """`/session`'s buttons, each one call into a ``ClaudeChatCog`` service."""

    def __init__(self, cog: SurfaceCommandsCog) -> None:
        self.cog = cog

    async def _bound(self, interaction: discord.Interaction) -> tuple[discord.Thread, Any] | None:
        """The thread and its record, or ``None`` after telling the user why."""
        if not await self.cog.in_session(interaction):
            return None
        thread = interaction.channel
        assert isinstance(thread, discord.Thread)
        record = await self.cog.repo.get(thread.id)
        if record is None:
            await _say(interaction, "No session is bound to this thread yet.")
            return None
        return thread, record

    async def _idle(self, interaction: discord.Interaction, thread_id: int, verb: str) -> bool:
        if thread_id in self.cog.chat._active_runners:
            await _say(interaction, f"A turn is running. Use `/stop` before you {verb}.")
            return False
        return True

    async def fork(self, interaction: discord.Interaction) -> None:
        bound = await self._bound(interaction)
        if bound is None:
            return
        thread, record = bound
        await interaction.response.defer(ephemeral=False)
        try:
            new_thread = await self.cog.chat.fork_thread(thread, record)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            f"🔀 Forked! Continue in {new_thread.mention} — this thread is unchanged."
        )

    async def rewind(self, interaction: discord.Interaction) -> None:
        bound = await self._bound(interaction)
        if bound is None:
            return
        thread, record = bound
        view = self.cog.chat.rewind_view(thread.id, record)
        if view is None:
            await self.cog.chat.clear_thread(thread.id)
            await interaction.response.send_message(
                "⏪ No conversation history found to rewind. "
                "Session has been reset — send a new message to start fresh."
            )
            return
        ctx_note = ""
        if record.context_window and record.context_used is not None:
            pct = round(record.context_used / record.context_window * 100)
            ctx_note = f" (context {pct}% full)"
        await interaction.response.send_message(
            f"⏪ **Rewind**{ctx_note} — select a turn to go back to before:", view=view
        )

    async def compact(self, interaction: discord.Interaction) -> None:
        bound = await self._bound(interaction)
        if bound is None:
            return
        thread, record = bound
        if not await self._idle(interaction, thread.id, "compact"):
            return
        await interaction.response.defer()
        seed = await interaction.followup.send("🗜️ Compacting conversation...", wait=True)
        await self.cog.chat.compact_thread(thread, record, seed)

    async def clear(self, interaction: discord.Interaction) -> None:
        if not await self.cog.in_session(interaction):
            return
        thread = interaction.channel
        assert isinstance(thread, discord.Thread)
        if await self.cog.chat.clear_thread(thread.id):
            await interaction.response.send_message(
                "🔄 Session cleared. Next message will start a fresh session in the same folder."
            )
        else:
            await _say(interaction, "No session is bound to this thread yet.")

    async def context(self, interaction: discord.Interaction) -> None:
        bound = await self._bound(interaction)
        if bound is None:
            return
        thread, record = bound
        embed = context_embed(record, thread.name)
        if embed is None:
            await _say(
                interaction,
                "ℹ️ No context data yet — stats are recorded after the first session completes.",
            )
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def goal(self, interaction: discord.Interaction, condition: str | None) -> None:
        bound = await self._bound(interaction)
        if bound is None:
            return
        thread, record = bound
        if not await self._idle(interaction, thread.id, "change the goal"):
            return
        await interaction.response.defer()
        seed = await interaction.followup.send(self.cog.chat.goal_label(condition), wait=True)
        await self.cog.chat.run_goal(thread, record, condition, seed)


class SurfaceCommandsCog(commands.Cog):
    """`/session` now; `/close`, `/new`, `/settings` join in 3.4 and 4.1."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        surface: CommandSurface,
        repo: Any,
        chat: Any,
        lifecycle: SessionLifecycleService | None = None,
        launcher: Any | None = None,
    ) -> None:
        self.bot = bot
        self.surface = surface
        self.repo = repo
        self.chat = chat
        # The shared close/reopen service. Absent, `/close` declines; it never
        # falls back to the destructive helper.
        self.lifecycle = lifecycle
        # The launcher owns the New session / Sessions / Settings flows; the
        # control-center commands are only its slash-command spelling.
        self.launcher = launcher
        self.actions = ChatSessionActions(self)

    # -- shared checks -----------------------------------------------------

    def authorized(self, interaction: discord.Interaction) -> bool:
        allowed = getattr(self.chat, "_allowed_user_ids", None)
        if interaction.guild_id is None or not category_allowed(interaction.channel):
            return False
        return allowed is None or interaction.user.id in allowed

    async def in_place(
        self, interaction: discord.Interaction, command: str, wanted: SurfaceLocation
    ) -> bool:
        """True when ``command`` may run here; otherwise the correction is sent."""
        if not self.authorized(interaction):
            await _say(interaction, "Use this computer's commands with an authorized account.")
            return False
        location = await locate(self.surface, self.repo, interaction)
        if location is wanted and self.surface.supports(command, location):
            return True
        await _say(
            interaction,
            self.surface.correction_for(command, location) or f"`/{command}` cannot run here.",
        )
        return False

    async def in_session(self, interaction: discord.Interaction) -> bool:
        return await self.in_place(interaction, "session", SurfaceLocation.MANAGED_SESSION)

    async def _control_flow(self, interaction: discord.Interaction, command: str) -> Any | None:
        """The launcher, when ``command`` may run here; otherwise ``None`` after replying."""
        if not await self.in_place(interaction, command, SurfaceLocation.CONTROL_CENTER):
            return None
        if self.launcher is None:
            await _say(interaction, f"`/{command}` is not available on this computer yet.")
            return None
        return self.launcher

    # -- commands ----------------------------------------------------------

    async def new_folder_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Any]:
        """The launcher's folder search; empty when no launcher is wired."""
        if self.launcher is None:
            return []
        return await self.launcher.folder_autocomplete(interaction, current)

    @app_commands.command(name="new", description="Start a new session in a folder")
    @app_commands.describe(folder="Type a few letters of the folder, or leave empty for the menu")
    @app_commands.autocomplete(folder=new_folder_autocomplete)
    async def new_command(
        self, interaction: discord.Interaction, folder: str | None = None
    ) -> None:
        launcher = await self._control_flow(interaction, "new")
        if launcher is None:
            return
        # A typed folder is the whole request: no menu, no second interaction.
        if folder and folder.strip():
            await launcher.new_session(interaction, folder.strip())
            return
        await launcher.show_new_session(interaction)

    @app_commands.command(name="settings", description="Open the settings this computer supports")
    async def settings_command(self, interaction: discord.Interaction) -> None:
        launcher = await self._control_flow(interaction, "settings")
        if launcher is not None:
            await launcher.show_settings(interaction)

    async def open_sessions(self, interaction: discord.Interaction, query: str | None) -> None:
        """`/sessions`: the Sessions browser. Registered by SessionManageCog, routed here."""
        launcher = await self._control_flow(interaction, "sessions")
        if launcher is not None:
            await launcher.show_sessions(interaction, query)

    @app_commands.command(
        name="session", description="Fork, rewind, compact, clear, context, or goal"
    )
    async def session_command(self, interaction: discord.Interaction) -> None:
        if not await self.in_session(interaction):
            return
        await interaction.response.send_message(
            "**Session** — choose an action. Clear asks before it forgets anything.",
            view=SessionActionsView(self.actions, user_id=interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(
        name="close", description="Wrap up and archive this session without losing it"
    )
    async def close_command(self, interaction: discord.Interaction) -> None:
        """Close through the lifecycle service: wrap-up, closed, archived — never deleted."""
        if not await self.in_place(interaction, "close", SurfaceLocation.MANAGED_SESSION):
            return
        if self.lifecycle is None:
            await _say(interaction, "Close is not available on this computer yet.")
            return
        thread = interaction.channel
        assert isinstance(thread, discord.Thread)
        # The thread's own closing note is posted by the lifecycle surface before
        # it archives; this reply is ephemeral so it cannot un-archive the thread.
        await interaction.response.defer(ephemeral=True)
        outcome = await self.lifecycle.close(
            thread.id, CloseAuthorization.from_interaction(interaction.user.id)
        )
        await interaction.followup.send(close_outcome_text(outcome), ephemeral=True)


async def _say(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
