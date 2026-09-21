"""Offer a fresh session when a thread's context gets long — one tap to hand off.

After every turn :class:`ContextNudger` looks at how full the thread's context
window is. On crossing 50 / 75 / 90 % it asks, once per step, whether to
continue in a fresh session. Yes: the current session writes a handoff file
(while it still remembers everything), a new thread starts from that file, and
the old thread links to it. No: silence until the next step.

The rules are in :mod:`claude_code_core.context_nudge`.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from claude_code_core.context_nudge import (
    context_label,
    handoff_path,
    handoff_prompt,
    next_thread_name,
    nudge_step,
    starter_prompt,
)
from claude_code_core.frontend import Choice, ChoicePrompt

from ..surface import DiscordSurface

if TYPE_CHECKING:
    from .claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

#: A nudge left unanswered this long is dropped (the next step asks again).
NUDGE_TIMEOUT_SECONDS = 6 * 60 * 60

_YES = "yes"
_NO = "no"


class ContextNudger:
    """Owned by ClaudeChatCog; called after each finished turn."""

    def __init__(self, chat: ClaudeChatCog) -> None:
        self._chat = chat
        #: Highest step already offered, per thread. In memory: after a restart
        #: a long thread is asked once more, which is harmless.
        self._offered: dict[int, int] = {}
        #: Threads that must never be nudged (task-loop workers start fresh anyway).
        self.skip_thread_ids: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def after_turn(self, thread: discord.Thread | discord.TextChannel) -> None:
        """Schedule the check so a waiting nudge never blocks the next message."""
        if not isinstance(thread, discord.Thread) or thread.id in self.skip_thread_ids:
            return
        task = asyncio.create_task(self._check(thread))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _check(self, thread: discord.Thread) -> None:
        try:
            record = await self._chat.repo.get(thread.id)
            if record is None:
                return
            step = nudge_step(
                record.context_used, record.context_window, self._offered.get(thread.id, 0)
            )
            if step is None:
                return
            self._offered[thread.id] = step
            yes = await self._ask(thread, step, getattr(record, "backend", None))
            if yes:
                await self.hand_off(thread)
        except Exception:
            logger.warning("context nudge failed for thread %s", thread.id, exc_info=True)

    async def _ask(self, thread: discord.Thread, step: int, backend: str | None = None) -> bool:
        # "Yes" runs a model turn and opens a thread, so only the people allowed
        # to talk to the bot may press it — the same set the chat cog enforces.
        surface = DiscordSurface(thread, allowed_user_ids=self._chat._allowed_user_ids)
        answer = await surface.prompt_choice(
            ChoicePrompt(
                question=(
                    f"This session is about {context_label(step, backend)} full. "
                    "Long sessions get slower and "
                    "start forgetting early details. Continue in a fresh session? "
                    "I'll save a handoff first, so nothing is lost."
                ),
                header="🧹 Start fresh?",
                choices=(
                    Choice(value=_YES, label="Yes, start fresh", style="positive"),
                    Choice(value=_NO, label="Not now"),
                ),
                timeout_seconds=NUDGE_TIMEOUT_SECONDS,
            )
        )
        return bool(answer) and _YES in (answer or ())

    async def hand_off(self, thread: discord.Thread) -> discord.Thread | None:
        """Write the handoff in this session, then open the fresh thread from it."""
        record = await self._chat.repo.get(thread.id)
        workdir = (record.working_dir if record else None) or getattr(
            self._chat.runner, "working_dir", None
        )
        parent = thread.parent
        if not isinstance(workdir, str) or not isinstance(parent, discord.TextChannel):
            await thread.send("-# Couldn't hand off: this thread has no project folder.")
            return None
        path = handoff_path(Path(workdir), thread.name, datetime.date.today().isoformat())
        seed = await thread.send("-# 📝 Saving a handoff for the fresh session…")
        await self._chat.run_resumed_turn(seed, thread, handoff_prompt(path))
        if not path.is_file():
            await thread.send(
                f"-# ⚠️ The handoff file wasn't written ({path}). Staying in this session."
            )
            return None
        new = await self._chat.spawn_session(
            parent,
            starter_prompt(path, thread.mention),
            thread_name=next_thread_name(thread.name),
            working_dir=workdir,
        )
        with contextlib.suppress(discord.HTTPException):
            await thread.send(f"➡️ Continued in {new.mention}. Handoff saved at `{path}`.")
        return new
