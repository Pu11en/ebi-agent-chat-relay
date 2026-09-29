"""ThreadFollowCog — a session is open exactly while Discord shows its thread.

"Open" means visible in Discord. A thread archived by hand, by EBI, or by
Discord's own seven-day sweep, or deleted outright, is a closed session, and its
spoken tag goes back to the pool. Before this cog the two drifted apart: a
thread Discord had put away stayed "open" in the database for weeks, holding a
tag nobody could see, until all ten words were held by conversations that no
longer existed.

It follows Discord both ways:

* **Archived or deleted** — the run in that thread is stopped at once (nothing
  keeps working where nobody can see it), then the session is closed on
  Discord's authority. That close posts nothing, renames nothing and locks
  nothing: a post would bring the thread straight back, and Discord refuses the
  rest on an archived thread anyway.
* **Unarchived** — the session reopens with its memory (stored native session
  id, same backend), the thread is unlocked if it was locked, and it gets a tag
  again. EBI's own close is never undone this way; see
  :meth:`SessionLifecycleService.reopen_from_discord`.

Gateway events can be missed — the bot was down, or an archived thread was
deleted, which Discord does not report at all — so :meth:`sweep` compares every
open row against Discord at startup and every few minutes. A clean pass makes
no Discord writes, and costs a fetch only for rows whose thread is not cached.

Nothing here ever posts into a thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import discord
from discord.ext import commands, tasks

from ..session_lifecycle import CloseAuthorization, CloseState
from ..thread_policy import thread_is_archived
from ..voice_tags import VoiceTagger

logger = logging.getLogger(__name__)

__all__ = ["SWEEP_INTERVAL_SECONDS", "ThreadFollowCog"]

#: How often open rows are compared against Discord. Events carry the news
#: within a second; the sweep only catches what they missed, so a few minutes
#: of delay costs nothing and keeps the fetches for uncached rows rare.
SWEEP_INTERVAL_SECONDS = 300

#: Origins whose ``thread_id`` is a Discord channel id. A Teams conversation is
#: stored in the same table under an id Discord has never heard of, and a fetch
#: for it would come back NotFound and close a live Teams session.
_DISCORD_ORIGINS = frozenset({"discord", "cli"})

#: A close that took effect now, or will as soon as the stopped run lets go.
_CLOSING = frozenset({CloseState.CLOSED, CloseState.PENDING})


def _archived_flag(data: Any) -> bool | None:
    """Discord's archived flag in a THREAD_UPDATE payload, or None when absent."""
    metadata = data.get("thread_metadata") if isinstance(data, dict) else None
    if not isinstance(metadata, dict) or "archived" not in metadata:
        return None
    return metadata["archived"] is True


class ThreadFollowCog(commands.Cog):
    """Close sessions whose threads Discord put away, and reopen the ones it brought back."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        repo: Any | None = None,
        settings_repo: Any | None = None,
        lifecycle: Any | None = None,
        chat: Any | None = None,
    ) -> None:
        self.bot = bot
        self._repo = repo
        self._settings_repo = settings_repo
        self._lifecycle = lifecycle
        self._chat = chat
        self._sweep_lock = asyncio.Lock()

    # -- collaborators, resolved late so wiring order never matters ----------

    @property
    def chat(self) -> Any | None:
        return self._chat if self._chat is not None else self.bot.get_cog("ClaudeChatCog")

    @property
    def lifecycle(self) -> Any | None:
        if self._lifecycle is not None:
            return self._lifecycle
        return getattr(self.chat, "lifecycle", None)

    @property
    def repo(self) -> Any | None:
        if self._repo is not None:
            return self._repo
        return getattr(self.bot, "session_repo", None)

    @property
    def settings_repo(self) -> Any | None:
        if self._settings_repo is not None:
            return self._settings_repo
        return getattr(self.chat, "_settings_repo", None)

    # -- the periodic sweep ---------------------------------------------------

    async def cog_load(self) -> None:
        if not self.periodic_sweep.is_running():
            self.periodic_sweep.start()

    async def cog_unload(self) -> None:
        self.periodic_sweep.cancel()

    @tasks.loop(seconds=SWEEP_INTERVAL_SECONDS)
    async def periodic_sweep(self) -> None:
        """One sweep. Never raises — a watcher must not kill the bot."""
        try:
            await self.sweep()
        except Exception:
            logger.exception("Thread follow sweep failed")

    @periodic_sweep.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    async def sweep(self) -> dict[str, int]:
        """Compare every open row with Discord and close the ones it put away.

        Reads every open row, with no cap: a row hidden behind a page limit
        would be a session that never closes. A cached thread costs nothing to
        check; an uncached one costs one fetch. Archived and NotFound threads
        are closed on Discord's authority; a fetch that fails any other way
        (refused, rate limited) proves nothing, so the row is left open and
        counted as an error. Never posts; a clean pass writes nothing.
        """
        report = {"checked": 0, "closed": 0, "reopened": 0, "retagged": 0, "errors": 0}
        repo = self.repo
        if repo is None:
            return report
        async with self._sweep_lock:
            live: dict[int, discord.Thread] = {}
            for row in await repo.open_rows():
                if row.origin not in _DISCORD_ORIGINS:
                    continue
                report["checked"] += 1
                thread_id = row.thread_id
                thread = self.bot.get_channel(thread_id)
                reason: str | None = None
                if thread is None:
                    try:
                        thread = await self.bot.fetch_channel(thread_id)
                    except discord.NotFound:
                        reason = "deleted"
                    except discord.HTTPException as exc:
                        logger.info("Could not check thread %s: %s", thread_id, exc)
                        report["errors"] += 1
                        continue
                if reason is None:
                    if not isinstance(thread, discord.Thread):
                        continue  # a channel session: nothing Discord can archive
                    if not thread_is_archived(thread):
                        live[thread_id] = thread
                        continue
                    reason = "archived"
                if await self._close(thread_id, CloseAuthorization.from_discord(reason)):
                    report["closed"] += 1
                else:
                    report["errors"] += 1
            tagged = await self._tagger().rebalance(live)
        report["retagged"] = tagged.get("retagged", 0)
        report["reclaimed"] = tagged.get("reclaimed", 0)
        if report["closed"] or report["errors"]:
            logger.info("Thread follow sweep: %s", report)
        return report

    # -- gateway events -------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_thread_update(self, payload: discord.RawThreadUpdateEvent) -> None:
        """Follow an archive or unarchive. Renames arrive here too and are ignored.

        Only a change of state acts: an archive of an open row, or an unarchive
        of a row that is not open. The same payload twice finds the row already
        where it points, so it writes nothing.
        """
        archived = _archived_flag(payload.data)
        record = await self._record(payload.thread_id)
        if archived is None or record is None:
            return
        if archived and record.is_open:
            await self._close(payload.thread_id, CloseAuthorization.from_discord())
            await self._tag_pass()
        elif not archived and not record.is_open:
            await self._reopen(payload.thread_id, payload.thread)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        """A deleted thread is a closed session; there is nothing left to fetch.

        A row still `closing` on a person's or a workflow's authority is left
        to finish its own close: its archive step finds the thread gone and
        settles without a Discord call.
        """
        record = await self._record(payload.thread_id)
        if record is None:
            return
        if record.is_open or (record.is_closed and record.archive_pending):
            await self._close(payload.thread_id, CloseAuthorization.from_discord("deleted"))
            await self._tag_pass()

    # -- helpers --------------------------------------------------------------

    async def _record(self, thread_id: int) -> Any | None:
        repo = self.repo
        if repo is None:
            return None
        try:
            return await repo.get(thread_id)
        except Exception:
            logger.warning("Could not read the session row for thread %s", thread_id, exc_info=True)
            return None

    async def _close(self, thread_id: int, authorization: CloseAuthorization) -> bool:
        """Stop the thread's run at once, then close its session on Discord's word.

        The stop comes first because the owner's rule is that nothing keeps
        working in a thread nobody can see. If the runner has not let go by the
        time the close is asked for, the close comes back pending and run
        finalization completes it with no Discord call.
        """
        lifecycle = self.lifecycle
        if lifecycle is None:
            logger.warning("No lifecycle service; thread %s stays open", thread_id)
            return False
        stop = getattr(self.chat, "stop_thread_run", None)
        if stop is not None:
            try:
                await stop(thread_id)
            except Exception:
                logger.warning("Could not stop the run in thread %s", thread_id, exc_info=True)
        try:
            outcome = await lifecycle.close(thread_id, authorization)
        except Exception:
            logger.warning("Could not close thread %s", thread_id, exc_info=True)
            return False
        logger.info("Thread %s put away in Discord (%s)", thread_id, authorization.actor)
        return outcome.state in _CLOSING

    async def _reopen(self, thread_id: int, cached: Any | None) -> None:
        lifecycle = self.lifecycle
        if lifecycle is None:
            return
        try:
            reopened = await lifecycle.reopen_from_discord(thread_id)
        except Exception:
            logger.warning("Could not reopen thread %s", thread_id, exc_info=True)
            return
        if not reopened:
            return
        logger.info("Thread %s brought back in Discord; session reopened", thread_id)
        thread = self.bot.get_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            thread = cached
        if not isinstance(thread, discord.Thread):
            with contextlib.suppress(discord.HTTPException):
                thread = await self.bot.fetch_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            return
        if thread.locked is True:
            try:
                await thread.edit(locked=False)
            except discord.HTTPException as exc:
                logger.info("Could not unlock reopened thread %s: %s", thread_id, exc)
        await self._tag_pass(first=thread)

    async def _tag_pass(self, first: discord.Thread | None = None) -> None:
        """Release the words of closed rows and hand free ones out, from the cache only."""
        repo = self.repo
        if repo is None:
            return
        threads: dict[int, Any] = {}
        if first is not None:
            threads[first.id] = first
        try:
            for row in await repo.open_rows():
                thread = self.bot.get_channel(row.thread_id)
                if isinstance(thread, discord.Thread):
                    threads.setdefault(row.thread_id, thread)
            await self._tagger().rebalance(threads)
        except Exception:
            logger.warning("Spoken-tag pass failed", exc_info=True)

    def _tagger(self) -> VoiceTagger:
        return VoiceTagger(self.bot, self.settings_repo, session_repo=self.repo)
