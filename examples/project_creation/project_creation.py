"""Optional custom Cog: /new opens local projects or creates an idle project session.

Copy this single file into CUSTOM_COGS_DIR. Configure PROJECT_HOME_CHANNEL_ID,
PROJECT_WORKERS_CHANNEL_ID, PROJECT_ROOT and PROJECT_STATE_FILE on that computer.
Uses existing CCDB_ALLOWED_USER_IDS / DISCORD_OWNER_ID and GitHub CLI credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}


def destination(root: Path, name: str) -> Path:
    """Validate against Windows rules even when the package is checked on Linux."""
    if (
        not name
        or len(name) > 80
        or name.startswith(".")
        or name.endswith((".", " "))
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or name.split(".")[0].upper() in RESERVED
    ):
        raise ValueError("Choose a folder name without slashes or Windows special characters.")
    if any(p.name.casefold() == name.casefold() for p in root.iterdir()):
        raise ValueError(
            "That folder name already exists. Choose another name or open it from All projects."
        )
    return root / name


def repository(value: str) -> tuple[str, str]:
    """Accept GitHub HTTPS URLs or owner/repo; never accept arbitrary Git transports."""
    value = value.strip().removeprefix("https://github.com/").removesuffix("/")
    value = value.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Use a GitHub repository link or owner/repo.")
    name = value.split("/")[1]
    if name in {".", ".."}:
        raise ValueError("Use a GitHub repository link or owner/repo.")
    return f"https://github.com/{value}.git", name


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("DISCORD_", "CCDB_")):
            env.pop(key)
    env.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1", GCM_INTERACTIVE="Never")
    return env


async def kill_clone(process: asyncio.subprocess.Process) -> None:
    """Terminate only this clone's process tree, including Git spawned by gh."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=child_env(),
            )
            await killer.wait()
        elif isinstance(process.pid, int):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    await process.wait()


async def run_gh(*args: str) -> None:
    executable = shutil.which("gh")
    if not executable:
        raise ValueError(
            "GitHub CLI is not visible to the bot. Its existing installation needs to be connected."
        )
    process = await asyncio.create_subprocess_exec(
        executable,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(),
        start_new_session=os.name != "nt",
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=600)
    except TimeoutError:
        await kill_clone(process)
        raise ValueError("Cloning timed out. No project session was started; try again.") from None
    except asyncio.CancelledError:
        await kill_clone(process)
        raise
    if process.returncode:
        raise ValueError(
            "Cloning failed. Check the repository link and the bot's existing GitHub access, "
            "then try again."
        )


async def create_project(root: Path, name: str, repo: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    dest = destination(root, name)
    if not repo:
        dest.mkdir()  # Exclusive: never reuse or overwrite a project.
        return dest
    url, _ = repository(repo)
    staging = Path(tempfile.mkdtemp(prefix=".clone-", dir=root))
    try:
        await run_gh("repo", "clone", url, str(staging))
        destination(root, name)  # Recheck after the potentially long clone.
        dest.mkdir()
        # Both are on the same filesystem. Publish only a successfully cloned repo.
        for child in staging.iterdir():
            child.rename(dest / child.name)
        return dest
    finally:
        # Only remove the private staging directory this request created.
        shutil.rmtree(staging)


class ProjectView(discord.ui.View):
    def __init__(self, cog: ProjectCreationCog, user_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = user_id
        self.busy = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own /new menu.", ephemeral=True)
            return False
        return await self.cog.authorize(interaction)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        logger.error("Project menu failed (%s)", type(error).__name__)
        text = (
            "The operation could not finish. Check All projects before retrying; "
            "your existing projects were not replaced."
        )
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class CreateModal(discord.ui.Modal):
    def __init__(self, view: ProjectView, clone: bool) -> None:
        super().__init__(title="Clone GitHub repo" if clone else "Create empty folder")
        self.view = view
        self.clone = clone
        self.repo = discord.ui.TextInput(label="GitHub link or owner/repo", max_length=240)
        self.folder = discord.ui.TextInput(
            label="New folder name",
            required=not clone,
            max_length=80,
            placeholder="Leave blank to use the repository name" if clone else "my-project",
        )
        if clone:
            self.add_item(self.repo)
        self.add_item(self.folder)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.view.interaction_check(interaction):
            return
        if self.view.busy:
            await interaction.response.send_message(
                "This menu already started a project. Open /new for another.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.view.busy = True
        try:
            repo = str(self.repo) if self.clone else ""
            name = str(self.folder) or repository(repo)[1]
            await interaction.followup.send(
                "Cloning into a new project folder…"
                if self.clone
                else "Creating the project folder…",
                ephemeral=True,
            )
            path = await create_project(self.view.cog.root, name, repo)
            await self.view.cog.open_session(interaction, path)
        except (ValueError, FileExistsError) as exc:
            self.view.busy = False
            text = (
                str(exc)
                if isinstance(exc, ValueError)
                else "That folder already exists. Choose another name."
            )
            await interaction.followup.send(text, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.view.on_error(interaction, error, self.folder)


class HomeView(ProjectView):
    @discord.ui.button(label="Recent")
    async def recent(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_projects(interaction, recent=True)

    @discord.ui.button(label="All projects")
    async def all_projects(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.cog.show_projects(interaction)

    @discord.ui.button(label="Create project", style=discord.ButtonStyle.primary)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Create a new project folder.", view=CreateView(self.cog, self.user_id)
        )


class CreateView(ProjectView):
    @discord.ui.button(label="Empty folder")
    async def empty(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CreateModal(self, False))

    @discord.ui.button(label="Clone GitHub repo", style=discord.ButtonStyle.primary)
    async def clone(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(CreateModal(self, True))

    @discord.ui.button(label="Back")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Choose a project or create one.", view=HomeView(self.cog, self.user_id)
        )


class ProjectList(ProjectView):
    def __init__(
        self, cog: ProjectCreationCog, user_id: int, names: list[str], page: int = 0
    ) -> None:
        super().__init__(cog, user_id)
        self.names, self.page = names, page
        options = [
            discord.SelectOption(label=name[:100], value=str(i))
            for i, name in enumerate(names[page * 25 : page * 25 + 25], start=page * 25)
        ]
        if options:
            picker = discord.ui.Select(
                placeholder="Choose a project for a new session", options=options
            )

            async def selected(interaction: discord.Interaction) -> None:
                if self.busy:
                    await interaction.response.send_message(
                        "This menu already opened a session. Use /new for another.", ephemeral=True
                    )
                    return
                self.busy = True
                await interaction.response.defer(ephemeral=True, thinking=True)
                await cog.open_session(interaction, cog.root / names[int(picker.values[0])])

            picker.callback = selected
            self.add_item(picker)
        self.previous.disabled = page == 0
        self.next_page.disabled = (page + 1) * 25 >= len(names)

    @discord.ui.button(label="Previous")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            view=ProjectList(self.cog, self.user_id, self.names, self.page - 1)
        )

    @discord.ui.button(label="Next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            view=ProjectList(self.cog, self.user_id, self.names, self.page + 1)
        )

    @discord.ui.button(label="Back")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Choose a project or create one.", view=HomeView(self.cog, self.user_id)
        )


class ProjectCreationCog(commands.Cog):
    def __init__(
        self, bot: Any, root: Path, state: Path, home_id: int, workers_id: int, users: set[int]
    ) -> None:
        self.bot, self.root, self.state = bot, root.resolve(), state.resolve()
        self.home_id, self.workers_id, self.users = home_id, workers_id, users
        self.lock = asyncio.Lock()

    async def authorize(self, interaction: discord.Interaction) -> bool:
        if interaction.channel_id != self.home_id or interaction.user.id not in self.users:
            await interaction.response.send_message(
                "Use /new in this bot's control-center with an authorized account.", ephemeral=True
            )
            return False
        return True

    def history(self) -> dict[str, list[str]]:
        if not self.state.exists():
            return {}
        data = json.loads(self.state.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(
            not isinstance(v, list) or any(not isinstance(n, str) for n in v) for v in data.values()
        ):
            raise ValueError("Recent-project history needs repair; no history was overwritten.")
        return data

    def projects(self, user_id: int, recent: bool = False) -> list[str]:
        if not self.root.is_dir():
            return []
        names = sorted(
            (
                p.name
                for p in self.root.iterdir()
                if not p.name.startswith(".")
                and p.is_dir()
                and not p.is_symlink()
                and p.resolve().parent == self.root
            ),
            key=str.casefold,
        )
        return [n for n in self.history().get(str(user_id), []) if n in names] if recent else names

    async def remember(self, user_id: int, name: str) -> None:
        async with self.lock:
            data = self.history()
            data[str(user_id)] = [name, *[n for n in data.get(str(user_id), []) if n != name]][:100]
            self.state.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state.with_suffix(".tmp")
            temp.write_text(json.dumps(data), encoding="utf-8")
            temp.replace(self.state)

    async def open_session(self, interaction: discord.Interaction, path: Path) -> None:
        path = path.resolve(strict=True)
        if not path.is_dir() or path.parent != self.root:
            raise ValueError("That project is no longer available under Projects.")
        chat = self.bot.get_cog("ClaudeChatCog")
        channel = self.bot.get_channel(self.workers_id) or await self.bot.fetch_channel(
            self.workers_id
        )
        home: Any = interaction.channel
        if (
            channel.guild.id != interaction.guild_id
            or home is None
            or home.category_id is None
            or channel.category_id != home.category_id
        ):
            raise ValueError("The workers channel must be in this control-center's category.")
        if chat is None:
            raise ValueError(
                "The bot's session launcher is unavailable; the project folder is still saved."
            )
        thread = await chat.spawn_session(
            channel=channel,
            prompt="Project ready. Send your first task here when ready.",
            thread_name=path.name,
            auto_start=False,
            invite_user_id=interaction.user.id,
            working_dir=str(path),
        )
        await interaction.followup.send(
            f"Ready: {thread.mention} — send your first task there.", ephemeral=True
        )
        try:
            await self.remember(interaction.user.id, path.name)
        except (OSError, ValueError):
            logger.warning("Could not save recent project")
            await interaction.followup.send(
                "The session is ready, but recent-project history could not be saved.",
                ephemeral=True,
            )

    async def show_projects(self, interaction: discord.Interaction, recent: bool = False) -> None:
        names = self.projects(interaction.user.id, recent)
        await interaction.response.edit_message(
            content="Choose a project."
            if names
            else "No projects here yet. Use Back to create one.",
            view=ProjectList(self, interaction.user.id, names),
        )

    @app_commands.command(
        name="new", description="Open a project, create a folder, or clone a GitHub repo"
    )
    async def new(self, interaction: discord.Interaction) -> None:
        if await self.authorize(interaction):
            await interaction.response.send_message(
                "Choose a project or create one.",
                view=HomeView(self, interaction.user.id),
                ephemeral=True,
            )


async def setup(bot: Any, runner: Any, components: Any) -> None:
    """Custom-cog loader entry; configuration is local to the consuming instance."""
    home = os.getenv("PROJECT_HOME_CHANNEL_ID", "")
    workers = os.getenv("PROJECT_WORKERS_CHANNEL_ID", "")
    root, state = os.getenv("PROJECT_ROOT", ""), os.getenv("PROJECT_STATE_FILE", "")
    if not all((home, workers, root, state)):
        raise ValueError(
            "Configure PROJECT_HOME_CHANNEL_ID, PROJECT_WORKERS_CHANNEL_ID, "
            "PROJECT_ROOT and PROJECT_STATE_FILE."
        )
    if not Path(root).is_absolute() or not Path(state).is_absolute():
        raise ValueError("Project root and state file must be absolute paths.")
    ids = re.split(r"[,\s]+", os.getenv("CCDB_ALLOWED_USER_IDS", ""))
    users = {int(value) for value in ids if value}
    owner = os.getenv("DISCORD_OWNER_ID")
    if owner:
        users.add(int(owner))
    if not users:
        raise ValueError("Configure authorized operators before enabling /new.")
    await bot.add_cog(
        ProjectCreationCog(bot, Path(root), Path(state), int(home), int(workers), users)
    )
