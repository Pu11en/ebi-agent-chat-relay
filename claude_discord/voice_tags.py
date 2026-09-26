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

import contextlib
import logging
from typing import Any

import discord

from .voice_labels import (
    SPOKEN_LABELS,
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

    def __init__(self, bot: Any, settings_repo: Any | None) -> None:
        self.bot = bot
        self.settings_repo = settings_repo

    # -- one thread, at the moment it is created ---------------------------

    async def tag_thread(self, thread: discord.Thread) -> str | None:
        """Give ``thread`` a tag now, and show it in the title.

        Returns the tag, or None when there is nowhere to store one or every word
        is already promised to a live thread. Untagged is the right answer in that
        case: reusing a word another thread answers to would send a spoken
        instruction to the wrong repository, which is the one unacceptable
        outcome here.
        """
        if self.settings_repo is None:
            return None
        existing = await self._stored()
        if existing is None:
            return None

        label = existing.get(thread.id)
        if label is None:
            # Deliberately *not* `assign_labels`: that takes the whole visible set
            # and may reclaim a word from a thread absent from it. Handed a single
            # id, every other thread looks absent, so it would cheerfully take a
            # word a live thread still answers to. Here only a genuinely free word
            # will do, and none available means untagged.
            label = next((w for w in SPOKEN_LABELS if w not in set(existing.values())), None)
            if label is None:
                return None
            with contextlib.suppress(Exception):
                await self.settings_repo.set(label_key(thread.id), label)
        # The tag is recorded before the rename, so a thread whose title could not
        # be changed is still addressable by the word it now owns.
        if title_tag(thread.name) != label:
            with contextlib.suppress(discord.HTTPException):
                await thread.edit(name=tagged_title(thread.name, label))
        return label

    # -- the bulk pass, over a set of session views ------------------------

    async def apply(self, views: list[dict[str, Any]]) -> None:
        """Attach a tag to each view, minting the missing ones.

        A closed session is not somewhere work is happening, so it is given no
        word and does not keep the one it had. Both mattered: the pool is 26 long
        and finished sessions were holding most of it, and the tag still resolved
        — saying it delivered an instruction into a session that was over.
        """
        existing = await self._stored()
        # `_stored` returns None for both "nowhere to store one" and "could not
        # read", and either way there is nothing to assign; narrowing on the repo
        # as well is what tells the type checker the writes below are safe.
        if existing is None or self.settings_repo is None:
            return

        ordered = [v["thread_id"] for v in views if not v.get("closed")]
        labels, minted, released = assign_labels(ordered, existing)
        finished = {v["thread_id"] for v in views if v.get("closed")} & set(existing)
        released |= finished

        for thread_id, label in minted.items():
            with contextlib.suppress(Exception):
                await self.settings_repo.set(label_key(thread_id), label)
        # A tag is only taken back when all 26 are spoken for; a thread that has
        # merely scrolled out of view keeps the word the speaker learned for it.
        for thread_id in released:
            with contextlib.suppress(Exception):
                await self.settings_repo.delete(label_key(thread_id))
        for view in views:
            view["voice_label"] = labels.get(view["thread_id"])
        await self.show_in_titles(views)

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

    async def _stored(self) -> dict[int, str] | None:
        """Every tag currently promised, or None when it cannot be read."""
        if self.settings_repo is None:
            return None
        try:
            stored = await self.settings_repo.get_all()
        except Exception:
            logger.warning("Could not read spoken tags", exc_info=True)
            return None
        existing: dict[int, str] = {}
        for key, value in stored.items():
            thread_id = thread_id_from_key(key)
            if thread_id is not None:
                existing[thread_id] = value
        return existing
