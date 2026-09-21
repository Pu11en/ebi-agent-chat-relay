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

from ..category_scope import category_allowed
from ..command_surface import CONTROL_CENTER_BUTTONS
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


class ControlRowView(discord.ui.View):
    """The persistent bottom control row: New session, Sessions, Settings.

    Labels come from :data:`CONTROL_CENTER_BUTTONS` so the row and `/help`
    cannot drift. The buttons are stateless; each opens a personal, ephemeral
    flow on the cog, so a row posted before a restart still works after it.
    """

    def __init__(self, cog: ProjectLauncherCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        openers = {
            "new": cog.show_new_session,
            "sessions": cog.show_sessions,
            "settings": cog.show_settings,
        }
        for index, spec in enumerate(CONTROL_CENTER_BUTTONS):
            button: discord.ui.Button[ControlRowView] = discord.ui.Button(
                label=spec.label,
                style=discord.ButtonStyle.primary if index == 0 else discord.ButtonStyle.secondary,
                custom_id=f"ccdb:control:{spec.command}:v1",
            )
            button.callback = openers[spec.command]
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.cog.authorize(interaction)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        logger.error("Control row action failed", exc_info=error)
        text = "This action failed; please try again. Your existing sessions are unchanged."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


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
        recent_folders: list[str] | None = None,
        primary_label: str = "Favorite folders",
        browsing: bool = False,
    ) -> None:
        super().__init__(cog, user_id)
        self.used = False
        if folders:
            select = discord.ui.Select(
                placeholder="Remove a favorite" if manage else primary_label,
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
                    await cog.show_browser(interaction, path, edit=True)

            select.callback = choose
            self.add_item(select)
        if recent_folders:
            recent = discord.ui.Select(
                placeholder="Recent folders",
                options=[
                    discord.SelectOption(
                        label=(Path(path).name or path)[:100],
                        description=path[-100:],
                        value=str(index),
                    )
                    for index, path in enumerate(recent_folders)
                ],
            )

            async def choose_recent(interaction: discord.Interaction) -> None:
                if self.used:
                    await interaction.response.send_message(
                        "This menu was already used. Open the launcher again.", ephemeral=True
                    )
                    return
                self.used = True
                await cog.show_browser(
                    interaction, recent_folders[int(recent.values[0])], edit=True
                )

            recent.callback = choose_recent
            self.add_item(recent)
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
        label = (
            "Add by path" if manage else ("Favorites + recent" if browsing else "Browse folders")
        )
        add = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)

        async def add_folder(interaction: discord.Interaction) -> None:
            if manage:
                await interaction.response.send_modal(FavoriteModal(cog, user_id))
            else:
                await cog.show_folders(interaction, browse=not browsing)

        add.callback = add_folder
        self.add_item(add)


class NewSessionMenu(PersonalView):
    """The New session choices: Favorites, Recent, Browse (Create and Clone join in 2.3).

    Every choice ends in :meth:`ProjectLauncherCog.new_session`, which binds a
    folder and posts a notice — it never starts a model turn.
    """

    def __init__(self, cog: ProjectLauncherCog, user_id: int) -> None:
        super().__init__(cog, user_id)
        choices: list[tuple[str, discord.ButtonStyle, Any]] = [
            ("Favorites", discord.ButtonStyle.primary, cog.show_favorite_pick),
            ("Recent", discord.ButtonStyle.secondary, cog.show_recent_pick),
            ("Browse", discord.ButtonStyle.secondary, cog.show_browse_choice),
        ]
        for label, style, opener in choices:
            button: discord.ui.Button[NewSessionMenu] = discord.ui.Button(label=label, style=style)
            button.callback = opener
            self.add_item(button)


class FolderPick(PersonalView):
    """One select over known folders; choosing one creates the idle thread."""

    def __init__(
        self, cog: ProjectLauncherCog, user_id: int, folders: list[str], *, placeholder: str
    ) -> None:
        super().__init__(cog, user_id)
        self.used = False
        select = discord.ui.Select(
            placeholder=placeholder,
            options=[
                discord.SelectOption(
                    label=(Path(path).name or path)[:100],
                    description=path[-100:],
                    value=str(index),
                )
                for index, path in enumerate(folders[:_LIMIT])
            ],
        )

        async def choose(interaction: discord.Interaction) -> None:
            if self.used:
                await interaction.response.send_message(
                    "This menu was already used. Open New session again.", ephemeral=True
                )
                return
            self.used = True
            await cog.new_session(interaction, folders[int(select.values[0])])

        select.callback = choose
        self.add_item(select)
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)

        async def go_back(interaction: discord.Interaction) -> None:
            await cog.show_new_session(interaction, edit=True)

        back.callback = go_back
        self.add_item(back)


class FolderBrowser(PersonalView):
    """Navigate a real directory tree; only Start here creates a thread."""

    def __init__(
        self,
        cog: ProjectLauncherCog,
        user_id: int,
        path: str | None,
        entries: list[Path],
        page: int,
    ) -> None:
        super().__init__(cog, user_id)
        self.used = False
        pages = max(1, (len(entries) + _LIMIT - 1) // _LIMIT)
        page = max(0, min(page, pages - 1))
        offered = entries[page * _LIMIT : (page + 1) * _LIMIT]
        if offered:
            select = discord.ui.Select(
                placeholder=f"Open a folder · page {page + 1}/{pages}",
                row=0,
                options=[
                    discord.SelectOption(label=(p.name or str(p))[:100], value=str(i))
                    for i, p in enumerate(offered)
                ],
            )

            async def open_folder(interaction: discord.Interaction) -> None:
                await cog.show_browser(interaction, str(offered[int(select.values[0])]), edit=True)

            select.callback = open_folder
            self.add_item(select)

        def navigation(
            label: str,
            target: str | None,
            *,
            number: int = 0,
            drives: bool = False,
            disabled: bool = False,
        ) -> None:
            button = discord.ui.Button(label=label, row=1, disabled=disabled)

            async def go(interaction: discord.Interaction) -> None:
                await cog.show_browser(interaction, target, page=number, drives=drives, edit=True)

            button.callback = go
            self.add_item(button)

        parent = str(Path(path).parent) if path else None
        navigation("Up", parent, disabled=path is None or parent == path)
        navigation("Home", str(Path.home()))
        navigation("Drives", None, drives=True)
        navigation("Previous", path, number=page - 1, drives=path is None, disabled=page == 0)
        navigation("Next", path, number=page + 1, drives=path is None, disabled=page + 1 >= pages)
        start = discord.ui.Button(
            label="Start here", style=discord.ButtonStyle.primary, row=2, disabled=path is None
        )

        async def start_here(interaction: discord.Interaction) -> None:
            if self.used or path is None:
                await interaction.response.send_message(
                    "Open a fresh folder picker.", ephemeral=True
                )
                return
            self.used = True
            await cog.new_session(interaction, path)

        start.callback = start_here
        self.add_item(start)
        favorite = discord.ui.Button(label="Save favorite", row=2, disabled=path is None)

        async def save_favorite(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            if path is not None:
                await cog.change_favorite(interaction.guild_id or 0, user_id, path, add=True)
                await interaction.followup.send("Favorite saved.", ephemeral=True)

        favorite.callback = save_favorite
        self.add_item(favorite)
        back = discord.ui.Button(label="Favorites + recent", row=2)

        async def go_back(interaction: discord.Interaction) -> None:
            await cog.show_folders(interaction, edit=True)

        back.callback = go_back
        self.add_item(back)


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
            record = await cog.repo.get(thread.id)
            if record is not None and record.working_dir:
                await cog.remember_folder(
                    interaction.guild_id or 0, interaction.user.id, record.working_dir
                )
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
        home_channel_id: int | None = None,
        session_channel_id: int | None = None,
        backend_settings: Any | None = None,
        backend_factory: Any | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.settings = settings
        self.chat = chat
        self.channel_id = home_channel_id or channel_id
        self.channel_ids = set(channel_ids)
        self.session_channel_id = session_channel_id
        self.working_dir = working_dir
        # Optional: lets the status line and new-thread notice name the default
        # model without a model turn. Absent, the notice names no model.
        self.backend_settings = backend_settings
        self.backend_factory = backend_factory
        self._favorites_lock = asyncio.Lock()
        self._panel_lock = asyncio.Lock()
        self._view: LauncherView | None = None
        self._control_row: ControlRowView | None = None
        self._shortcut_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._view = LauncherView(self)
        self.bot.add_view(self._view)
        self._control_row = ControlRowView(self)
        self.bot.add_view(self._control_row)

    async def cog_unload(self) -> None:
        if self._shortcut_task is not None:
            self._shortcut_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._shortcut_task
        if self._view is not None:
            self._view.stop()
        if self._control_row is not None:
            self._control_row.stop()

    # ------------------------------------------------------------------
    # The control row: status line + New session / Sessions / Settings
    # ------------------------------------------------------------------

    def computer_name(self) -> str:
        fallback = self.bot.user.display_name if self.bot.user else "This computer"
        return (os.environ.get("CCDB_COMPUTER_NAME", "").strip() or fallback)[:180]

    async def default_model(self) -> tuple[str | None, str | None]:
        """The (backend, model) a new thread starts on, read without a model turn."""
        settings = self.backend_settings
        if settings is None:
            runner = getattr(self.chat, "runner", None)
            model = getattr(runner, "model", None)
            return None, (model if isinstance(model, str) and model else None)
        try:
            backend = await settings.current_backend()
            model = await settings.current_model(backend)
            if not model and self.backend_factory is not None:
                model = self.backend_factory.default_model_for(backend)
        except Exception:
            logger.debug("Default model unavailable for the status line", exc_info=True)
            return None, None
        return backend, (model or None)

    async def status_block(self) -> str:
        """The compact computer status shown above the control buttons."""
        active = int(getattr(self.chat, "active_session_count", 0) or 0)
        backend, model = await self.default_model()
        parts = [f"**{self.computer_name()}**", f"{active} active session{'s' * (active != 1)}"]
        if backend or model:
            parts.append("default: " + " · ".join(p for p in (backend, model) if p))
        return " · ".join(parts)

    def control_row(self) -> ControlRowView:
        return self._control_row or ControlRowView(self)

    async def control_content(self) -> str:
        return (
            f"{await self.status_block()}\n"
            "-# **New session** starts a folder-bound thread · **Sessions** finds work · "
            "**Settings** shows what this computer supports"
        )

    async def show_new_session(
        self, interaction: discord.Interaction, *, edit: bool = False
    ) -> None:
        """New session: Favorites, Recent, or Browse — each ends in an idle thread."""
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        _, model = await self.default_model()
        starts_on = f"`{model}`" if model else "this computer's default model"
        text = (
            f"**New session** — choose where it works. It starts on {starts_on} "
            "and nothing runs until you send the first task."
        )
        view = NewSessionMenu(self, interaction.user.id)
        if edit:
            await interaction.edit_original_response(content=text, view=view)
        else:
            await interaction.followup.send(text, view=view, ephemeral=True)

    async def show_favorite_pick(self, interaction: discord.Interaction) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        folders = await self.favorites(interaction.guild_id or 0, interaction.user.id)
        if not folders:
            await interaction.edit_original_response(
                content="No favorites saved yet. Use **Browse** and press **Save favorite**.",
                view=NewSessionMenu(self, interaction.user.id),
            )
            return
        await interaction.edit_original_response(
            content="Choose a favorite folder; the session thread is created at once.",
            view=FolderPick(self, interaction.user.id, folders, placeholder="Favorite folders"),
        )

    async def show_recent_pick(self, interaction: discord.Interaction) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        recent = await self.recents(interaction.guild_id or 0, interaction.user.id)
        recent = await asyncio.to_thread(lambda: [p for p in recent if Path(p).is_dir()])
        if not recent:
            await interaction.edit_original_response(
                content="No recent folders on this computer yet. Use **Favorites** or **Browse**.",
                view=NewSessionMenu(self, interaction.user.id),
            )
            return
        await interaction.edit_original_response(
            content="Choose a recent folder; the session thread is created at once.",
            view=FolderPick(self, interaction.user.id, recent, placeholder="Recent folders"),
        )

    async def show_browse_choice(self, interaction: discord.Interaction) -> None:
        await self.show_browser(interaction, edit=True)

    async def show_sessions(self, interaction: discord.Interaction) -> None:
        """Sessions: find a session to continue (the browser lands in task 2.4)."""
        await self.show_resume(interaction)

    async def show_settings(self, interaction: discord.Interaction) -> None:
        """Settings: this computer's supported configuration (task 2.5)."""
        if not await self.authorize(interaction):
            return
        await interaction.response.send_message(
            "Settings for this computer are not available yet.", ephemeral=True
        )

    def embed(self) -> discord.Embed:
        return discord.Embed(
            title=f"{self.computer_name()} · Sessions",
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
            or not category_allowed(channel)
            or channel_id not in self.channel_ids | {self.channel_id}
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

    async def recents(self, guild_id: int, user_id: int) -> list[str]:
        raw = await self.settings.get(f"launcher.recents:{guild_id}:{user_id}")
        if raw is None:
            return []
        values = json.loads(raw)
        if not isinstance(values, list) or not all(isinstance(path, str) for path in values):
            raise ValueError("Saved recent folders are invalid; ask the bot to repair them.")
        return values[:_LIMIT]

    async def remember_folder(self, guild_id: int, user_id: int, path: str) -> None:
        async with self._favorites_lock:
            previous = await self.recents(guild_id, user_id)
            latest = [path] + [item for item in previous if item != path]
            await self.settings.set(
                f"launcher.recents:{guild_id}:{user_id}", json.dumps(latest[:_LIMIT])
            )

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

    async def show_folders(
        self,
        interaction: discord.Interaction,
        *,
        manage: bool = False,
        browse: bool = False,
        edit: bool = False,
    ) -> None:
        if browse:
            await self.show_browser(interaction, edit=True)
            return
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        folders = await self.favorites(interaction.guild_id or 0, interaction.user.id)
        suggestions = []
        recent_folders = []
        primary_label = "Favorite folders"
        if manage:
            suggestions = [
                path for path in await asyncio.to_thread(self._suggestions) if path not in folders
            ]
        if not manage:
            recent_folders = [
                path
                for path in await self.recents(interaction.guild_id or 0, interaction.user.id)
                if path not in folders
            ]
            recent_folders = await asyncio.to_thread(
                lambda: [path for path in recent_folders if Path(path).is_dir()]
            )
            if not folders and not recent_folders:
                folders = await asyncio.to_thread(self._suggestions)
                primary_label = "Project folders"
        text = "Your favorite folders: select one to remove, or add a folder."
        if not manage:
            text = "Choose a favorite or recent folder, then press **Start here**."
        if not folders and not suggestions and not recent_folders:
            text = "No folders saved yet. Use **Browse folders** to find one on this computer."
        view = FolderMenu(
            self,
            interaction.user.id,
            folders,
            manage=manage,
            suggestions=suggestions,
            recent_folders=recent_folders,
            primary_label=primary_label,
            browsing=browse,
        )
        if edit:
            await interaction.edit_original_response(content=text, view=view)
        else:
            await interaction.followup.send(text, view=view, ephemeral=True)

    async def show_browser(
        self,
        interaction: discord.Interaction,
        path: str | None = None,
        *,
        page: int = 0,
        drives: bool = False,
        edit: bool = False,
    ) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        def read() -> tuple[str | None, list[Path]]:
            if drives:
                reader: Any = getattr(os, "listdrives", None)
                roots = reader() if reader is not None else [Path.home().anchor]
                return None, [Path(root) for root in roots]
            current = directory(path or self.working_dir or str(Path.home()))
            children = sorted(
                (child for child in Path(current).iterdir() if child.is_dir()),
                key=lambda child: (child.name.casefold(), child.name),
            )
            return current, children

        try:
            current, entries = await asyncio.to_thread(read)
        except (OSError, ValueError):
            await interaction.followup.send(
                "That folder is unavailable or cannot be read. Choose another folder.",
                ephemeral=True,
            )
            return
        text = (
            f"📂 **Current folder**\n`{current}`" if current else "**Drives and filesystem roots**"
        )
        text += "\nOpen subfolders, or press **Start here** to create your session."
        view = FolderBrowser(self, interaction.user.id, current, entries, page)
        if edit:
            await interaction.edit_original_response(content=text, view=view)
        else:
            await interaction.followup.send(text, view=view, ephemeral=True)

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
        if self.session_channel_id is not None:
            channel = self.bot.get_channel(self.session_channel_id)
        elif isinstance(channel, discord.Thread):
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
        # The notice is the whole start: no model turn is spent until the first task.
        _, model = await self.default_model()
        model_line = f"🤖 Default model: `{model}`\n" if model else ""
        await thread.send(
            f"📂 Working folder: `{path}`\n{model_line}"
            "Send your task here to begin; replies continue this session.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.remember_folder(interaction.guild_id or 0, interaction.user.id, path)
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

    @commands.Cog.listener("on_message")
    async def keep_launcher_visible(self, message: discord.Message) -> None:
        if message.channel.id != self.channel_id or message.type not in (
            discord.MessageType.default,
            discord.MessageType.reply,
        ):
            return
        if self.bot.user and message.author.id == self.bot.user.id:
            for row in message.components:
                for component in getattr(row, "children", ()):
                    custom_id = getattr(component, "custom_id", None)
                    if isinstance(custom_id, str) and custom_id.startswith(
                        ("ccdb:launcher:", "ccdb:control:")
                    ):
                        return
        if self._shortcut_task is None or self._shortcut_task.done():
            self._shortcut_task = asyncio.create_task(self._delayed_shortcut())

    async def _delayed_shortcut(self) -> None:
        await asyncio.sleep(10)
        try:
            await self.refresh_shortcut()
        except Exception:
            logger.exception("Could not refresh the control row")

    async def _saved_control_row(self, channel: discord.TextChannel) -> discord.Message | None:
        """The bot-owned control row the settings point at, if it still exists."""
        saved = await self.settings.get(f"launcher.shortcut:{self.channel_id}")
        if not saved or not saved.isdigit():
            return None
        try:
            message = await channel.fetch_message(int(saved))
        except discord.HTTPException:
            return None
        if self.bot.user and message.author.id == self.bot.user.id:
            return message
        return None

    async def refresh_shortcut(self) -> None:
        """Publish a fresh control row at the bottom and remove only the previous one.

        The new id is saved before the old row is deleted: a failed delete
        leaves an extra row for the next repair pass, never a missing one.
        The pinned anchor and channel history are never touched.
        """
        async with self._panel_lock:
            channel = self.bot.get_channel(self.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            previous = await self._saved_control_row(channel)
            message = await channel.send(
                content=await self.control_content(),
                view=self.control_row(),
                allowed_mentions=discord.AllowedMentions.none(),
                silent=True,
            )
            await self.settings.set(f"launcher.shortcut:{self.channel_id}", str(message.id))
            if previous is not None:
                with contextlib.suppress(discord.HTTPException):
                    await previous.delete()

    async def ensure_control_row(self) -> None:
        """On reconnect, restore the saved control row or replace a missing one.

        Editing the surviving row keeps its place; only when it is gone (or
        was never posted) is a new one sent, so a restart adds no second row.
        """
        channel = self.bot.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        async with self._panel_lock:
            existing = await self._saved_control_row(channel)
            if existing is not None:
                await existing.edit(content=await self.control_content(), view=self.control_row())
                return
        await self.refresh_shortcut()

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
        try:
            await self.ensure_control_row()
        except discord.HTTPException:
            logger.exception("Could not restore the control row")
