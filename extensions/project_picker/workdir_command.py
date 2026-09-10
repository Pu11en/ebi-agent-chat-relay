"""Project picker from cKreymborg/claude-code-discord-bridge, commit 7805100.

Loaded via Ebi CUSTOM_COGS_DIR; current Ebi core remains unmodified.
See SOURCE.md and LICENSE for provenance, local adaptations, and usage.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from claude_discord.database.repository import SessionRepository

logger = logging.getLogger(__name__)

_DEFAULT_ROOTS = ("~/Developer",)

# Directory names never worth offering as a working dir (noise / build output).
_SKIP_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".cache",
    "target",
    ".idea",
    ".vscode",
}


def resolve_project_roots(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = "CCDB_PROJECT_ROOTS",
) -> list[Path]:
    """Return the configured project roots (``~`` expanded).

    Defaults to ``~/Developer`` when the env var is unset/empty.
    """
    env = os.environ if environ is None else environ
    raw = env.get(env_var, "").strip()
    specs = [s.strip() for s in raw.split(",") if s.strip()] if raw else list(_DEFAULT_ROOTS)
    roots: list[Path] = []
    for spec in specs:
        path = Path(spec).expanduser()
        if path not in roots:
            roots.append(path)
    return roots


def _is_offerable(path: Path) -> bool:
    return path.is_dir() and path.name not in _SKIP_NAMES and not path.name.startswith(".")


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(p for p in directory.iterdir() if _is_offerable(p))
    except OSError:
        return []


def find_directories(
    roots: Iterable[Path],
    query: str,
    *,
    limit: int = 25,
    max_depth: int = 2,
) -> list[Path]:
    """Return up to *limit* directories under *roots* matching *query*.

    Empty query → the immediate children of each root (your projects).
    Non-empty query → case-insensitive substring match on the directory name,
    searched up to *max_depth* levels deep, ranking name-prefix matches first.
    """
    roots = list(roots)
    query = query.strip().lower()
    results: list[Path] = []
    seen: set[Path] = set()

    if not query:
        for root in roots:
            for child in _children(root):
                if child not in seen:
                    seen.add(child)
                    results.append(child)
                    if len(results) >= limit:
                        return results
        return results

    matches: list[tuple[int, str, Path]] = []
    for root in roots:
        # Breadth-first walk, bounded by max_depth.
        frontier = [(root, 0)]
        while frontier:
            directory, depth = frontier.pop(0)
            for child in _children(directory):
                name = child.name.lower()
                if query in name:
                    rank = 0 if name.startswith(query) else 1
                    matches.append((rank, str(child).lower(), child))
                if depth + 1 < max_depth:
                    frontier.append((child, depth + 1))

    matches.sort(key=lambda item: (item[0], item[1]))
    for _, _, directory in matches:
        if directory not in seen:
            seen.add(directory)
            results.append(directory)
            if len(results) >= limit:
                break
    return results


def display_label(path: Path) -> str:
    """A short label for a directory choice (relative to home when possible)."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


class WorkdirCommandCog(commands.Cog):
    """Exposes /cd and /cdnew for choosing a session's working directory."""

    def __init__(
        self,
        bot: commands.Bot,
        repo: SessionRepository,
        claude_channel_id: int,
        *,
        allowed_user_ids: set[int] | None = None,
        claude_channel_ids: set[int] | None = None,
        project_roots: list[Path] | None = None,
        chat_cog: object | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.claude_channel_id = claude_channel_id
        self._allowed_user_ids = allowed_user_ids
        self._channel_ids: set[int] = claude_channel_ids or {claude_channel_id}
        # When None, roots are resolved live from CCDB_PROJECT_ROOTS on each call.
        self._project_roots = project_roots
        self._chat_cog = chat_cog

    # -- helpers -------------------------------------------------------------

    def _roots(self) -> list[Path]:
        if self._project_roots is not None:
            return self._project_roots
        return resolve_project_roots()

    def _is_authorized(self, user_id: int) -> bool:
        return self._allowed_user_ids is None or user_id in self._allowed_user_ids

    def _is_claude_thread(self, channel: object) -> bool:
        return isinstance(channel, discord.Thread) and channel.parent_id in self._channel_ids

    @staticmethod
    def _validate_dir(value: str) -> Path | None:
        try:
            path = Path(value).expanduser().resolve()
            return path if path.is_dir() else None
        except (OSError, ValueError, RuntimeError):
            return None

    async def _path_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return up to 25 matching directories for autocomplete."""
        if not self._is_authorized(interaction.user.id):
            return []
        dirs = await asyncio.to_thread(find_directories, self._roots(), current or "")
        dirs = [d for d in dirs if len(str(d)) <= 100]
        return [app_commands.Choice(name=display_label(d)[:100], value=str(d)) for d in dirs]

    # -- /cd -----------------------------------------------------------------

    @app_commands.command(
        name="cd",
        description="Retarget THIS thread to a folder (starts a fresh session there)",
    )
    @app_commands.describe(path="Folder to run this thread in (type to filter)")
    @app_commands.autocomplete(path=_path_autocomplete)
    async def cd(self, interaction: discord.Interaction, path: str) -> None:
        if not self._is_authorized(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        if not self._is_claude_thread(interaction.channel):
            await interaction.response.send_message(
                "Use `/cd` inside an Ebi thread. To start a new session in a folder, use `/cdnew`.",
                ephemeral=True,
            )
            return
        directory = self._validate_dir(path)
        if directory is None:
            await interaction.response.send_message(f"Not a directory: `{path}`", ephemeral=True)
            return
        assert isinstance(interaction.channel, discord.Thread)
        # Share Ebi's per-thread lock: never retarget a running session.
        chat = self._chat_cog
        locks = getattr(chat, "_thread_locks", {})
        lock = locks.setdefault(interaction.channel.id, asyncio.Lock())
        if lock.locked():
            await interaction.response.send_message(
                "This thread is busy. Wait for it to finish, or use `/cdnew`.", ephemeral=True
            )
            return
        async with lock:
            if interaction.channel.id in getattr(chat, "_active_runners", {}):
                await interaction.response.send_message(
                    "This thread is running. Stop it first, or use `/cdnew`.", ephemeral=True
                )
                return
            # Empty session_id means fresh context on the next message.
            await self.repo.save(interaction.channel.id, "", working_dir=str(directory))
        await interaction.response.send_message(
            f"📂 This thread now targets `{directory}`.\n"
            "Your next message starts a **fresh session** there."
        )

    # -- /cdnew --------------------------------------------------------------

    @app_commands.command(name="cdnew", description="Open a NEW session in a folder")
    @app_commands.describe(path="Folder for the new session (type to filter)")
    @app_commands.autocomplete(path=_path_autocomplete)
    async def cdnew(self, interaction: discord.Interaction, path: str) -> None:
        if not self._is_authorized(interaction.user.id):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        directory = self._validate_dir(path)
        if directory is None:
            await interaction.response.send_message(f"Not a directory: `{path}`", ephemeral=True)
            return

        invoke_ch = interaction.channel
        if isinstance(invoke_ch, discord.TextChannel) and invoke_ch.id in self._channel_ids:
            channel = invoke_ch
        else:
            channel = self.bot.get_channel(self.claude_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Ebi channel not found.", ephemeral=True)
            return

        await interaction.response.defer()
        thread = await channel.create_thread(
            name=f"📂 {directory.name}"[:100],
            type=discord.ChannelType.public_thread,
        )
        # Pre-seed the working dir (empty session_id → fresh session on first message).
        await self.repo.save(thread.id, "", working_dir=str(directory))
        with contextlib.suppress(discord.HTTPException):
            await thread.add_user(interaction.user)
        await interaction.followup.send(
            f"🆕 New session in `{directory}` → {thread.mention}\n"
            "Send your first message in that thread to begin."
        )


async def setup(bot, runner, components):
    """Register through Ebi's supported extension entry point."""
    chat = bot.get_cog("ClaudeChatCog")
    if chat is None:
        raise RuntimeError("Project picker requires Ebi's chat cog")
    channel_ids = chat._channel_ids
    primary = int(os.environ["DISCORD_CHANNEL_ID"])
    await bot.add_cog(
        WorkdirCommandCog(
            bot,
            components.session_repo,
            primary,
            allowed_user_ids=chat._allowed_user_ids,
            claude_channel_ids=channel_ids,
            chat_cog=chat,
        )
    )
