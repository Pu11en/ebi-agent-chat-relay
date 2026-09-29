"""The Discord side of :class:`~claude_discord.session_lifecycle.SessionLifecycleService`.

The service talks to a *turn tracker* and a *conversation surface* through two
small protocols. These are the Discord implementations: the chat cog's
active-runner table says whether a turn is in flight, and a thread is closed
by editing it — archived *and* locked, never deleted, so the service keeps its
promise that closing destroys nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import discord

from claude_code_core.session_repo import CloseAuthority

from .session_lifecycle import SessionLifecycleService
from .voice_labels import strip_title_tag, title_tag

logger = logging.getLogger(__name__)

_WORKFLOW_CLOSE = CloseAuthority.WORKFLOW_CLOSE_ON_DONE.value


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
    """Close and reopen a thread: archived and locked, never deleted.

    The closing note is posted *before* archiving — Discord un-archives a
    thread that receives a message, so anything said after the edit would
    undo it. With a repository the note quotes the stored wrap-up.

    Archiving alone was not enough, which is the whole reason lock is here: an
    archived-but-unlocked thread comes straight back the next time *anything*
    posts in it — a later bot notice, or the person themselves typing one more
    line — so a closed session kept reappearing in the sidebar and "close this
    out" visibly did nothing. Locking is not locking anyone out: the bot holds
    manage_threads, :meth:`unarchive` clears both flags, and every reopen path
    (Sessions -> Open, `/resume`) goes through it. Nothing is deleted, and the
    conversation is still there when it is reopened.
    """

    def __init__(self, bot: Any, repo: Any | None = None) -> None:
        self.bot = bot
        self.repo = repo

    async def archive(self, thread_id: int) -> bool:
        thread = await self._thread(thread_id)
        if thread is None:
            return False
        record = await self.repo.get(thread_id) if self.repo is not None else None
        if record is not None and record.close_authority == _WORKFLOW_CLOSE:
            # A workflow's finished worker: it already posted its outcome, and its
            # thread must stay unlocked so its history can still be read and linked.
            await self._drop_spoken_tag(thread)
            return await self._set_archived(thread, True, lock=False)
        note = await self._closing_note(thread_id)
        if note:
            with contextlib.suppress(discord.HTTPException):
                await thread.send(note, allowed_mentions=discord.AllowedMentions.none())
        await self._drop_spoken_tag(thread)
        return await self._set_archived(thread, True)

    @staticmethod
    async def _drop_spoken_tag(thread: discord.Thread) -> None:
        """Take the spoken tag out of the title, while the title can still change.

        A closed session gives up its tag, but the title is a second copy of it
        and Discord refuses to rename a thread once it is archived. Leaving it
        produced two threads in the sidebar both reading "[bravo]", one of them
        finished — the tag looked like it belonged to a conversation that was
        over. So the rename happens here, before the archive lands, and only when
        there is actually a tag to remove: one needless rename per close would
        spend a budget Discord keeps very tight. A rename that fails changes
        nothing about the close, which is the part that matters.
        """
        name = getattr(thread, "name", "") or ""
        if title_tag(name) is None:
            return
        with contextlib.suppress(discord.HTTPException):
            await thread.edit(name=strip_title_tag(name))

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
    async def _set_archived(thread: discord.Thread, archived: bool, *, lock: bool = True) -> bool:
        """Archive and lock together, or unarchive and unlock together."""
        try:
            if lock or not archived:
                await thread.edit(archived=archived, locked=archived)
            else:
                await thread.edit(archived=True)
        except discord.HTTPException:
            logger.warning("Could not set archived=%s on thread %s", archived, thread.id)
            return False
        return True


def build_lifecycle_service(bot: Any, chat: Any, repo: Any) -> SessionLifecycleService:
    """The one lifecycle service every Discord entry point shares."""
    return SessionLifecycleService(
        repo, surface=DiscordThreadSurface(bot, repo), turns=ChatTurnActivity(chat)
    )
