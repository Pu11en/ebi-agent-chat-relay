"""Computer-labelled shortcuts using the bridge's existing session/folder contract."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..database.repository import SessionRepository
from ..database.settings_repo import SettingsRepository
from ..thread_policy import THREAD_AUTO_ARCHIVE_MINUTES

logger = logging.getLogger(__name__)
_LIMIT = 25


def directory(value: str) -> str:
    """Require a real absolute folder; relative paths depend on launcher cwd."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Use a full folder path on this computer.")
    try:
        path = path.resolve(strict=True)
        if path.is_dir():
            return str(path)
    except (OSError, ValueError, RuntimeError):
        pass
    raise ValueError("That folder does not exist on this computer.")


class LauncherView(discord.ui.View):
    """Stateless persistent buttons; personal menus are created on demand."""

    def __init__(self, cog: ProjectLauncherCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.cog.authorize(interaction)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        logger.error("Project launcher action failed", exc_info=error)
        text = "This action failed; please try again. Your existing sessions are unchanged."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(label="Favorite folders", custom_id="ccdb:launcher:favorites:v1")
    async def favorites_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.show_folders(interaction, manage=True)

    @discord.ui.button(
        label="New session", style=discord.ButtonStyle.primary, custom_id="ccdb:launcher:new:v1"
    )
    async def new_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_folders(interaction)

    @discord.ui.button(label="Resume", custom_id="ccdb:launcher:resume:v1")
    async def resume_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.show_resume(interaction)


class PersonalView(LauncherView):
    def __init__(self, cog: ProjectLauncherCog, user_id: int) -> None:
        super().__init__(cog)
        self.clear_items()
        self.timeout = 300
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own launcher menu.", ephemeral=True)
            return False
        return await self.cog.authorize(interaction)


class FavoriteModal(discord.ui.Modal, title="Add a favorite folder"):
    folder = discord.ui.TextInput(label="Full folder path on this computer", max_length=1000)

    def __init__(self, cog: ProjectLauncherCog, user_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id or not await self.cog.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.cog.change_favorite(
                interaction.guild_id or 0, self.user_id, str(self.folder), add=True
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            "Favorite saved. Choose **New session** on the launcher to use it.", ephemeral=True
        )


class FolderMenu(PersonalView):
    def __init__(
        self,
        cog: ProjectLauncherCog,
        user_id: int,
        folders: list[str],
        *,
        manage: bool,
        suggestions: list[str] | None = None,
    ) -> None:
        super().__init__(cog, user_id)
        self.used = False
        if folders:
            select = discord.ui.Select(
                placeholder="Remove a favorite" if manage else "Choose a folder to start",
                options=[
                    discord.SelectOption(
                        label=(Path(path).name or path)[:100],
                        description=path[-100:],
                        value=str(index),
                    )
                    for index, path in enumerate(folders)
                ],
            )

            async def choose(interaction: discord.Interaction) -> None:
                if self.used:
                    await interaction.response.send_message(
                        "This menu was already used. Open the launcher again.", ephemeral=True
                    )
                    return
                self.used = True
                path = folders[int(select.values[0])]
                if manage:
                    await interaction.response.defer(ephemeral=True)
                    await cog.change_favorite(interaction.guild_id or 0, user_id, path, add=False)
                    await interaction.followup.send("Favorite removed.", ephemeral=True)
                else:
                    await cog.new_session(interaction, path)

            select.callback = choose
            self.add_item(select)
        if manage and suggestions:
            browse = discord.ui.Select(
                placeholder="Add an existing folder to favorites",
                options=[
                    discord.SelectOption(
                        label=(Path(path).name or path)[:100],
                        description=path[-100:],
                        value=str(index),
                    )
                    for index, path in enumerate(suggestions)
                ],
            )

            async def save_folder(interaction: discord.Interaction) -> None:
                await interaction.response.defer(ephemeral=True)
                try:
                    await cog.change_favorite(
                        interaction.guild_id or 0,
                        user_id,
                        suggestions[int(browse.values[0])],
                        add=True,
                    )
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                await interaction.followup.send(
                    "Favorite saved. Choose **New session** on the launcher to use it.",
                    ephemeral=True,
                )

            browse.callback = save_folder
            self.add_item(browse)
        add = discord.ui.Button(label="Add favorite folder", style=discord.ButtonStyle.secondary)

        async def add_folder(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(FavoriteModal(cog, user_id))

        add.callback = add_folder
        self.add_item(add)


class ResumeMenu(PersonalView):
    def __init__(
        self, cog: ProjectLauncherCog, user_id: int, threads: list[discord.Thread]
    ) -> None:
        super().__init__(cog, user_id)
        select = discord.ui.Select(
            placeholder="Continue an existing thread",
            options=[
                discord.SelectOption(label=thread.name[:100], value=str(thread.id))
                for thread in threads
            ],
        )

        async def choose(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            thread = await cog.visible_thread(int(select.values[0]), interaction)
            if thread is None:
                await interaction.followup.send(
                    "That thread is no longer available. Open Resume again.", ephemeral=True
                )
                return
            # A link opens the original thread, including archived threads. No cloning,
            # session writes, extra model calls, or changes to another running turn.
            await interaction.followup.send(
                f"Continue here: https://discord.com/channels/{thread.guild.id}/{thread.id}",
                ephemeral=True,
            )

        select.callback = choose
        self.add_item(select)


class ProjectLauncherCog(commands.Cog):
    """One instance-local entry point, with favorites belonging to each operator."""

    def __init__(
        self,
        bot: commands.Bot,
        repo: SessionRepository,
        settings: SettingsRepository,
        chat: Any,
        *,
        channel_id: int,
        channel_ids: set[int],
        working_dir: str | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.settings = settings
        self.chat = chat
        self.channel_id = channel_id
        self.channel_ids = channel_ids
        self.working_dir = working_dir
        self._favorites_lock = asyncio.Lock()
        self._panel_lock = asyncio.Lock()
        self._view: LauncherView | None = None

    async def cog_load(self) -> None:
        self._view = LauncherView(self)
        self.bot.add_view(self._view)

    async def cog_unload(self) -> None:
        if self._view is not None:
            self._view.stop()

    def embed(self) -> discord.Embed:
        fallback = self.bot.user.display_name if self.bot.user else "This computer"
        name = os.environ.get("CCDB_COMPUTER_NAME", "").strip() or fallback
        return discord.Embed(
            title=f"{name[:180]} · Sessions",
            description=(
                "**Favorite folders** — save the folders you use on this computer.\n"
                "**New session** — pick a folder and start a separate thread.\n"
                "**Resume** — return to an existing conversation.\n\n"
                "Your favorites are personal; authorized teammates can share the threads."
            ),
            color=discord.Color.blurple(),
        )

    async def authorize(self, interaction: discord.Interaction) -> bool:
        allowed = self.chat._allowed_user_ids
        channel = interaction.channel
        channel_id = (
            channel.parent_id if isinstance(channel, discord.Thread) else interaction.channel_id
        )
        if (
            interaction.guild_id is None
            or channel_id not in self.channel_ids
            or (allowed is not None and interaction.user.id not in allowed)
        ):
            await interaction.response.send_message(
                "Use this computer's launcher in its channel with an authorized account.",
                ephemeral=True,
            )
            return False
        return True

    @staticmethod
    def _key(guild_id: int, user_id: int) -> str:
        return f"launcher.favorites:{guild_id}:{user_id}"

    async def favorites(self, guild_id: int, user_id: int) -> list[str]:
        raw = await self.settings.get(self._key(guild_id, user_id))
        if raw is None:
            return []
        values = json.loads(raw)
        if not isinstance(values, list) or not all(isinstance(p, str) for p in values):
            raise ValueError("Saved favorites are invalid; ask the bot to repair them.")
        return values[:_LIMIT]

    async def change_favorite(self, guild_id: int, user_id: int, path: str, *, add: bool) -> None:
        if add:
            path = await asyncio.to_thread(directory, path)
        async with self._favorites_lock:
            saved = await self.favorites(guild_id, user_id)
            if add and path not in saved:
                if len(saved) >= _LIMIT:
                    raise ValueError("You have 25 favorites. Remove one before adding another.")
                saved.append(path)
            elif not add:
                saved = [item for item in saved if item != path]
            await self.settings.set(self._key(guild_id, user_id), json.dumps(saved))

    def _suggestions(self) -> list[str]:
        roots = os.environ.get("CCDB_PROJECT_ROOTS", "").split(",")
        roots = [root.strip() for root in roots if root.strip()]
        if not roots and self.working_dir:
            roots = [self.working_dir]
        result: list[str] = []
        for raw in roots:
            try:
                root = Path(directory(raw))
                # Bounded scan; large trees never block the Discord event loop.
                for child in root.iterdir():
                    if len(result) >= _LIMIT:
                        return result
                    if (
                        child.is_dir()
                        and not child.name.startswith(".")
                        and child.name
                        not in {
                            "node_modules",
                            "venv",
                            "__pycache__",
                            "dist",
                            "build",
                        }
                        and str(child) not in result
                    ):
                        result.append(str(child))
                if str(root) not in result and len(result) < _LIMIT:
                    result.append(str(root))
            except (OSError, ValueError):
                logger.debug("Skipping unavailable launcher project root")
        return result

    async def show_folders(self, interaction: discord.Interaction, *, manage: bool = False) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        folders = await self.favorites(interaction.guild_id or 0, interaction.user.id)
        suggestions = []
        if manage:
            suggestions = [
                path for path in await asyncio.to_thread(self._suggestions) if path not in folders
            ]
        if not folders and not manage:
            folders = await asyncio.to_thread(self._suggestions)
        text = "Your favorite folders: select one to remove, or add a folder."
        if not manage:
            text = "Choose a folder on this computer for a new session."
        if not folders and not suggestions:
            text = "No folders saved yet. Add a favorite using its full path on this computer."
        await interaction.followup.send(
            text,
            view=FolderMenu(
                self, interaction.user.id, folders, manage=manage, suggestions=suggestions
            ),
            ephemeral=True,
        )

    async def new_session(self, interaction: discord.Interaction, path: str) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            path = await asyncio.to_thread(directory, path)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            channel = channel.parent
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("This channel cannot create threads.", ephemeral=True)
            return
        thread = await channel.create_thread(
            name=f"📂 {Path(path).name}"[:100],
            type=discord.ChannelType.public_thread,
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )
        try:
            # Same contract as /cdnew: first human message starts a fresh session.
            await self.repo.save(thread.id, "", working_dir=path)
        except Exception:
            with contextlib.suppress(discord.HTTPException):
                await thread.delete(reason="Folder binding failed; empty launcher thread")
            raise
        with contextlib.suppress(discord.HTTPException):
            await thread.add_user(interaction.user)
        join = getattr(self.chat, "_ensure_thread_members", None)
        if join is not None:
            await join(thread)
        await thread.send(
            f"📂 Working folder: `{path}`\n"
            "Send your task here to begin; replies continue this session.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.followup.send(
            f"New session ready: {thread.mention}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def visible_thread(
        self, thread_id: int, interaction: discord.Interaction
    ) -> discord.Thread | None:
        if not isinstance(interaction.user, discord.Member):
            return None
        try:
            channel = await self.bot.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        if (
            not isinstance(channel, discord.Thread)
            or channel.parent_id not in self.channel_ids
            or channel.guild.id != interaction.guild_id
            or not channel.permissions_for(interaction.user).view_channel
        ):
            return None
        if channel.is_private() and not channel.permissions_for(interaction.user).manage_threads:
            try:
                await channel.fetch_member(interaction.user.id)
            except (discord.NotFound, discord.Forbidden):
                return None
        return channel

    async def show_resume(self, interaction: discord.Interaction) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        records = await self.repo.list_all(limit=_LIMIT)
        threads = []
        for record in records:
            thread = await self.visible_thread(record.thread_id, interaction)
            if thread is not None:
                threads.append(thread)
        if not threads:
            await interaction.followup.send(
                "No recent accessible threads to resume.", ephemeral=True
            )
            return
        await interaction.followup.send(
            "Resume opens the original thread and keeps its conversation.",
            view=ResumeMenu(self, interaction.user.id, threads),
            ephemeral=True,
        )

    @app_commands.command(
        name="launcher", description="Open this computer's folder/session buttons"
    )
    async def launcher(self, interaction: discord.Interaction) -> None:
        if await self.authorize(interaction):
            await interaction.response.send_message(
                embed=self.embed(), view=LauncherView(self), ephemeral=True
            )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        async with self._panel_lock:
            channel = self.bot.get_channel(self.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            key = f"launcher.panel:{self.channel_id}"
            try:
                saved = await self.settings.get(key)
                message = None
                if saved and saved.isdigit():
                    with contextlib.suppress(discord.NotFound):
                        message = await channel.fetch_message(int(saved))
                if message is not None and self.bot.user and message.author.id == self.bot.user.id:
                    await message.edit(embed=self.embed(), view=self._view or LauncherView(self))
                else:
                    message = await channel.send(
                        embed=self.embed(),
                        view=self._view or LauncherView(self),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    await self.settings.set(key, str(message.id))
                if not message.pinned:
                    with contextlib.suppress(discord.HTTPException):
                        await message.pin(reason="Computer session launcher")
            except discord.HTTPException:
                logger.exception("Could not publish the computer session launcher")
