"""Streaming message manager for Discord threads.

Manages a Discord message that gets edited as streaming text arrives from
the Claude Code CLI. Created on first text, then edited at a debounced
interval to respect Discord rate limits.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import discord

from .edit_budget import budget_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Streaming message edit interval (seconds). Discord rate limit is 5 edits/5s.
STREAM_EDIT_INTERVAL = 1.5

# Max characters before starting a new streaming message
STREAM_MAX_CHARS = 1900


class StreamingMessageManager:
    """Manages a Discord message that gets edited as streaming text arrives.

    Creates a message on first text, then edits it at a debounced interval.
    When text exceeds Discord's limit, starts a new message.
    """

    def __init__(
        self,
        thread: discord.Thread | discord.TextChannel,
        *,
        is_muted: Callable[[], bool] | None = None,
    ) -> None:
        self._thread = thread
        self._is_muted = is_muted
        self._muted = False
        self._current_message: discord.Message | None = None
        self._buffer: str = ""
        self._last_edit_time: float = 0
        self._pending_edit: asyncio.Task[None] | None = None
        self._finalized: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self._buffer)

    @property
    def muted(self) -> bool:
        """True once the thread must not be touched again.

        Either this manager was muted directly or its owner (the surface) says
        the thread was archived or deleted. The text still accumulates, so the
        caller gets the whole answer back from :meth:`finalize`; it just never
        reaches Discord, which would un-archive the thread on the first edit.
        """
        return self._muted or (self._is_muted is not None and self._is_muted())

    def mute(self) -> None:
        """Drop every later send and edit, including ones already queued. Idempotent."""
        self._muted = True
        if self._pending_edit and not self._pending_edit.done():
            self._pending_edit.cancel()

    async def append(self, text: str) -> None:
        """Append text to the streaming buffer and schedule an edit."""
        if self._finalized:
            return

        self._buffer += text

        # Drain overflow: finalize completed streaming messages until buffer fits.
        # Use a while loop (not if) to handle multi-overflow (e.g. a single 5000-char
        # chunk), and drop the `and self._current_message` guard so the first message
        # is also split correctly when a large chunk arrives before any message exists.
        while len(self._buffer) > STREAM_MAX_CHARS:
            await self._flush()
            self._current_message = None
            self._buffer = self._buffer[STREAM_MAX_CHARS:]

        now = time.monotonic()
        if now - self._last_edit_time >= STREAM_EDIT_INTERVAL:
            await self._flush()
        elif not self._pending_edit or self._pending_edit.done():
            self._pending_edit = asyncio.create_task(self._delayed_flush())

    async def finalize(
        self,
        transform: Callable[[str], str] | None = None,
    ) -> str:
        """Finalize the streaming message. Returns the full accumulated text.

        Args:
            transform: Optional post-processor applied to the buffer before
                the final flush.  Used by the table renderer to convert raw
                GFM pipe-tables into box-drawing tables on completion.
                If the transformed text exceeds STREAM_MAX_CHARS, overflow
                is posted as a new follow-up message.
        """
        self._finalized = True
        if self._pending_edit and not self._pending_edit.done():
            self._pending_edit.cancel()
        # Paced edits are *queued*, and the thread's budget is discarded when the
        # session closes — so a final flush that only queued would silently lose
        # the end of the answer. Everything below therefore flushes the budget
        # before returning, whichever path it takes.

        if transform and self._buffer:
            self._buffer = transform(self._buffer)

        if self._buffer:
            if len(self._buffer) <= STREAM_MAX_CHARS:
                await self._flush()
            else:
                # Transformed text grew beyond limit — edit current message
                # with the first chunk and post the rest as new messages.
                first = self._buffer[:STREAM_MAX_CHARS]
                overflow = self._buffer[STREAM_MAX_CHARS:]
                self._buffer = first
                await self._flush()
                # Post overflow chunks
                while overflow and not self.muted:
                    chunk = overflow[:STREAM_MAX_CHARS]
                    overflow = overflow[STREAM_MAX_CHARS:]
                    await self._thread.send(chunk)

        await budget_for(self._thread.id).flush()
        return self._buffer

    async def _delayed_flush(self) -> None:
        """Wait for the edit interval then flush."""
        remaining = STREAM_EDIT_INTERVAL - (time.monotonic() - self._last_edit_time)
        if remaining > 0:
            await asyncio.sleep(remaining)
        if not self._finalized:
            await self._flush()

    async def _flush(self) -> None:
        """Send or edit the current message with buffer contents.

        The buffer is always kept ≤ STREAM_MAX_CHARS (1900) by append(), so
        it fits well within Discord's 2000-char limit.  The [:STREAM_MAX_CHARS]
        slice is a defense-in-depth guard — it should never actually trim anything
        in normal operation, but prevents a Discord API error if called directly
        with an oversized buffer.
        """
        if not self._buffer:
            return
        if self.muted:
            logger.debug("Streaming flush dropped: thread %s is muted", self._thread.id)
            return

        display_text = self._buffer[:STREAM_MAX_CHARS]

        try:
            if self._current_message is None:
                self._current_message = await self._thread.send(display_text)
            else:
                # Editing spends the *thread's* budget, not this message's:
                # Discord meters edits per channel, and the tool counters in the
                # same thread are spending from the same allowance
                # (discord_ui/edit_budget.py).
                message = self._current_message
                await budget_for(self._thread.id).submit(
                    self._guarded_edit(message, display_text), key=("stream", message.id)
                )
            self._last_edit_time = time.monotonic()
        except Exception:
            # Catch all exceptions including aiohttp.ClientError (e.g. ServerDisconnectedError
            # on bot shutdown) which is not a subclass of discord.HTTPException.
            logger.debug("Failed to send/edit streaming message", exc_info=True)

    def _guarded_edit(self, message: discord.Message, text: str) -> Callable[[], Awaitable[None]]:
        """An edit re-checked when the budget runs it, not when it was queued.

        A paced edit can sit in the thread's queue for seconds; the thread may
        be archived in between, and the edit would then un-archive it.
        """

        async def edit() -> None:
            if self.muted:
                return
            await message.edit(content=text)

        return edit
