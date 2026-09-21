"""The Discord side of :class:`~claude_discord.session_lifecycle.SessionLifecycleService`.

The service talks to a *turn tracker* and a *conversation surface* through two
small protocols. These are the Discord implementations: the chat cog's
active-runner table says whether a turn is in flight, and a thread is
archived by editing it — never locked, never deleted, so the service keeps
its promise that closing destroys nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import discord

from .session_lifecycle import SessionLifecycleService

logger = logging.getLogger(__name__)


class ChatTurnActivity:
    """Whether ``ClaudeChatCog`` is running a turn in a thread, and how to wait it out."""

    def __init__(self, chat: Any) -> None:
        self.chat = chat

    async def is_active(self, thread_id: int) -> bool:
        return thread_id in getattr(self.chat, "_active_runners", {})

    async def wait_until_idle(self, thread_id: int) -> None:
        task = getattr(self.chat, "_active_tasks", {}).get(thread_id)
        if task is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.shield(task)


class DiscordThreadSurface:
    """Archive and unarchive a thread. There is deliberately no lock or delete here.

    The closing note is posted *before* archiving — Discord un-archives a
    thread that receives a message, so anything said after the edit would
    undo it. With a repository the note quotes the stored wrap-up.
    """

    def __init__(self, bot: Any, repo: Any | None = None) -> None:
        self.bot = bot
        self.repo = repo

    async def archive(self, thread_id: int) -> bool:
        thread = await self._thread(thread_id)
        if thread is None:
            return False
        note = await self._closing_note(thread_id)
        if note:
            with contextlib.suppress(discord.HTTPException):
                await thread.send(note, allowed_mentions=discord.AllowedMentions.none())
        return await self._set_archived(thread, True)

    async def unarchive(self, thread_id: int) -> bool:
        thread = await self._thread(thread_id)
        if thread is None:
            return False
        return await self._set_archived(thread, False)

    async def _closing_note(self, thread_id: int) -> str | None:
        if self.repo is None:
            return None
        record = await self.repo.get(thread_id)
        if record is None or not record.wrap_up:
            return None
        return (
            "📦 Session closed and archived. Reopen it from **Sessions**.\n"
            f"> {record.wrap_up[:900]}"
        )

    async def _thread(self, thread_id: int) -> discord.Thread | None:
        thread = self.bot.get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except discord.HTTPException:
                return None
        return thread if isinstance(thread, discord.Thread) else None

    @staticmethod
    async def _set_archived(thread: discord.Thread, archived: bool) -> bool:
        try:
            await thread.edit(archived=archived)
        except discord.HTTPException:
            logger.warning("Could not set archived=%s on thread %s", archived, thread.id)
            return False
        return True


def build_lifecycle_service(bot: Any, chat: Any, repo: Any) -> SessionLifecycleService:
    """The one lifecycle service every Discord entry point shares."""
    return SessionLifecycleService(
        repo, surface=DiscordThreadSurface(bot, repo), turns=ChatTurnActivity(chat)
    )
