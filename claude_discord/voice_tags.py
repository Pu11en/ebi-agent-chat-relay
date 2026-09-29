"""Giving a thread its spoken tag, from wherever the thread came from.

The words themselves and the rules for handing them out live in
:mod:`claude_discord.voice_labels`. This is the part that talks to storage and to
Discord: read what is already promised, mint what is missing, write it down, and
put it at the front of the title.

It exists as its own module because tagging used to happen in exactly two places,
both inside the API server — ``GET /api/sessions`` and ``POST /api/spawn``. A
thread opened by voice was therefore tagged at once, while every other way a
thread starts (the control center, a typed message, ``/skill``, ``/fork``) waited
for something to poll ``/api/sessions``. That poll comes from the voice
companion, so a thread created with voice switched off was never tagged at all:
the tag silently depended on a separate service being alive, which is the same
shape of failure as a fix that was committed and never deployed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import discord

from claude_code_core.session_repo import SessionRepository

from .voice_labels import (
    aliases_for,
    assign_labels,
    label_key,
    strip_title_tag,
    tagged_title,
    thread_id_from_key,
    title_tag,
)

logger = logging.getLogger(__name__)

__all__ = ["MAX_RETITLES_PER_CALL", "VoiceTagger"]

#: How many threads one bulk pass may look up and retitle.
#:
#: Discord's rename limit is per thread rather than global, so this does not
#: protect the rename budget — it bounds the *work* one pass does, which matters
#: because resolving a thread that is not in the cache costs a Discord call and
#: the bulk path is polled every few seconds. Low enough not to stall the
#: request, high enough that a fresh set is fully tagged within a couple of
#: passes rather than ten minutes — the tags are useless until they are visible.
MAX_RETITLES_PER_CALL = 12


class VoiceTagger:
    """Assigns and displays spoken tags. Safe with no settings repository."""

    def __init__(
        self,
        bot: Any,
        settings_repo: Any | None,
        *,
        session_repo: SessionRepository | None = None,
    ) -> None:
        self.bot = bot
        self.settings_repo = settings_repo
        self.session_repo = session_repo
        if self.session_repo is None:
            bot_repo = getattr(bot, "session_repo", None)
            if isinstance(bot_repo, SessionRepository):
                self.session_repo = bot_repo

    @property
    def lock(self) -> asyncio.Lock:
        """Serialize creation/registration and tag allocation across entry points."""
        lock = getattr(self.bot, "_voice_tag_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self.bot._voice_tag_lock = lock
        return lock

    @property
    def ineligible(self) -> set[int]:
        """Workers excluded in this process, honored even if storing it failed."""
        known = getattr(self.bot, "_voice_ineligible", None)
        if not isinstance(known, set):
            known = set()
            self.bot._voice_ineligible = known
        return known

    async def exclude_thread(self, thread_id: int) -> None:
        """Persist worker exclusion. Caller holds ``lock`` while registering a thread.

        Recorded in memory first: if the store refuses the write, the caller still
        sees the error, but this process never hands the worker a user's tag.
        """
        self.ineligible.add(thread_id)
        if self.settings_repo is not None:
            await self.settings_repo.set(f"voice_addressable:{thread_id}", "false")
            await self.settings_repo.delete(label_key(thread_id))

    # -- one thread, at the moment it is created ---------------------------

    async def tag_thread(self, thread: discord.Thread) -> str | None:
        async with self.lock:
            labels = await self._assign([thread.id])
            label = labels.get(thread.id) if labels is not None else None
        if label is not None and title_tag(thread.name) != label:
            with contextlib.suppress(discord.HTTPException):
                await thread.edit(name=tagged_title(thread.name, label))
        return label

    # -- the bulk pass, over a set of session views ------------------------

    async def apply(self, views: list[dict[str, Any]]) -> None:
        async with self.lock:
            labels = await self._assign(
                [v["thread_id"] for v in views],
                closed={v["thread_id"] for v in views if v.get("closed")},
            )
            for view in views:
                label = labels.get(view["thread_id"]) if labels is not None else None
                view["voice_label"] = label
                view["voice_label_aliases"] = list(aliases_for(label))
        # A failed snapshot is unknown, not evidence that a title is stale.
        if labels is not None:
            await self.show_in_titles(views)

    async def _assign(
        self, thread_ids: list[int], *, closed: set[int] | None = None
    ) -> dict[int, str] | None:
        """Assign under ``lock``; return only persisted promises, or unknown.

        Inspect every stored holder, not every historical session. Without a
        lifecycle repository only explicit closed views/worker exclusions permit
        release; unknown and archived-but-open holders keep their names.
        """
        stored = await self._stored()
        if stored is None or self.settings_repo is None:
            return None
        existing, excluded = stored
        excluded |= self.ineligible
        closed = set(closed or ())
        if self.session_repo is not None:
            try:
                for thread_id in set(existing) | set(thread_ids):
                    record = await self.session_repo.get(thread_id)
                    if record is not None:
                        if record.is_closed:
                            closed.add(thread_id)
                        else:
                            closed.discard(thread_id)
            except Exception:
                logger.warning("Could not read spoken-tag lifecycle evidence", exc_info=True)
                return None
        excluded |= closed
        released: set[int] = set()
        for thread_id in excluded & set(existing):
            try:
                await self.settings_repo.delete(label_key(thread_id))
            except Exception:
                logger.warning(
                    "Could not release spoken tag for thread %s", thread_id, exc_info=True
                )
            else:
                released.add(thread_id)
        labels, minted, _ = assign_labels(
            [tid for tid in thread_ids if tid not in excluded], existing, released_ids=released
        )
        for thread_id, label in minted.items():
            try:
                await self.settings_repo.set(label_key(thread_id), label)
            except Exception:
                labels.pop(thread_id, None)
                logger.warning(
                    "Could not assign spoken tag for thread %s", thread_id, exc_info=True
                )
        return labels

    async def show_in_titles(self, views: list[dict[str, Any]]) -> None:
        """Put each thread's tag at the front of its Discord title.

        A roster in another channel answers "which tag is that thread?"; the
        question actually being asked while looking at the sidebar is "what do I
        say to *this* one?". Only a title answers that, so the tag goes there.

        A thread with *no* tag is renamed too, to take the old one off. This only
        ever added a tag before, so a thread that lost its word kept displaying
        it: two threads read "[bravo]" while one of them answered to it, and the
        sidebar — the one place a tag is read from — was lying.
        """
        spent = 0
        for view in views:
            if spent >= MAX_RETITLES_PER_CALL:
                break
            label = view.get("voice_label")
            name = view.get("thread_name")
            if not name or title_tag(name) == label:
                continue
            # A closed session is archived, so its title cannot be changed at
            # all; the tag is taken off while it is still editable, as it closes
            # (lifecycle_adapters.py). Looking it up would only ever be waste.
            if view.get("closed"):
                continue
            spent += 1
            thread = await self.editable_thread(view["thread_id"])
            if not isinstance(thread, discord.Thread) or thread.archived or thread.locked:
                continue
            wanted = tagged_title(name, label) if label else strip_title_tag(name)
            try:
                await thread.edit(name=wanted)
            except Exception as exc:  # rate limit, permissions, archived race
                logger.debug("Could not tag thread %s: %s", view["thread_id"], exc)
                continue
            view["thread_name"] = wanted

    async def editable_thread(self, thread_id: int) -> Any:
        """The thread object, from cache or from Discord.

        ``get_channel`` only sees the cache, and a thread the bot has not touched
        since it started is not in it — such a thread silently kept whatever tag
        its title already showed, forever, because the rename was skipped rather
        than attempted. One fetch per stale title is worth paying; a title that
        already matches never gets here.
        """
        thread = self.bot.get_channel(thread_id)
        if isinstance(thread, discord.Thread):
            return thread
        try:
            return await self.bot.fetch_channel(thread_id)
        except Exception as exc:
            # Deleted, or not visible to the bot. Never worth failing the
            # request, but a silently discarded failure is how the stale tag
            # went unnoticed in the first place.
            logger.debug("Could not resolve thread %s: %s", thread_id, exc)
            return None

    async def _stored(self) -> tuple[dict[int, str], set[int]] | None:
        """Every tag currently promised, or None when it cannot be read."""
        if self.settings_repo is None:
            return None
        try:
            stored = await self.settings_repo.get_all()
        except Exception:
            logger.warning("Could not read spoken tags", exc_info=True)
            return None
        existing: dict[int, str] = {}
        excluded: set[int] = set()
        for key, value in stored.items():
            thread_id = thread_id_from_key(key)
            if thread_id is not None:
                existing[thread_id] = value
            if key.startswith("voice_addressable:") and value == "false":
                suffix = key.removeprefix("voice_addressable:")
                if suffix.isdigit():
                    excluded.add(int(suffix))
        return existing, excluded
