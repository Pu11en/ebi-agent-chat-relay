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
from .thread_policy import thread_is_archived
from .voice_labels import strip_title_tag, title_tag

logger = logging.getLogger(__name__)

_WORKFLOW_CLOSE = CloseAuthority.WORKFLOW_CLOSE_ON_DONE.value
_DISCORD_ARCHIVED = CloseAuthority.DISCORD_ARCHIVED.value


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

    Two closes never reach Discord at all. A close Discord itself started —
    the thread archived by hand, by the seven-day auto-archive, or deleted —
    is only recorded: a note would un-archive the thread it is meant to close,
    a rename is refused once a thread is archived, and a lock would stop the
    person typing their way back in. And a thread that no longer exists counts
    as archived for every authority, because there is nothing left to do and
    reporting failure kept the close pending, retried on every restart against
    a thread that was gone. A fetch the bot is refused is not that: the thread
    may well still be there, so that stays pending. A thread Discord already
    shows as archived is treated the same way whoever closes it: posting the
    note into it would bring it straight back into view.
    """

    def __init__(self, bot: Any, repo: Any | None = None) -> None:
        self.bot = bot
        self.repo = repo

    async def archive(self, thread_id: int) -> bool:
        record = await self.repo.get(thread_id) if self.repo is not None else None
        authority = record.close_authority if record is not None else None
        if authority == _DISCORD_ARCHIVED:
            # Discord did the archiving; the close only records it. Not even a
            # fetch: the thread may already be deleted, and nothing here needs it.
            return True
        try:
            thread = await self._thread(thread_id)
        except discord.HTTPException:
            logger.warning(
                "Could not reach thread %s to archive it; retry on reconciliation", thread_id
            )
            return False
        if thread is None:
            return True
        if thread_is_archived(thread):
            # Already out of sight — by hand, or by Discord's own sweep while a
            # close waited for a restart. The note would un-archive the very
            # thread it closes, and Discord refuses a rename or a lock on an
            # archived thread, so the close stands as it is.
            return True
        if authority == _WORKFLOW_CLOSE:
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
        try:
            thread = await self._thread(thread_id)
        except discord.HTTPException:
            logger.warning("Could not reach thread %s to unarchive it", thread_id)
            return False
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
        """The live thread, or ``None`` when Discord no longer has one to edit.

        ``None`` means gone for good — deleted, or never a thread — and the
        callers treat that as already archived. A fetch that fails for now
        (refused, rate limited, an outage) raises instead, so a passing
        failure is never mistaken for a deletion and acknowledged as done.
        """
        thread = self.bot.get_channel(thread_id)
        if isinstance(thread, discord.Thread):
            return thread
        try:
            thread = await self.bot.fetch_channel(thread_id)
        except discord.NotFound:
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
