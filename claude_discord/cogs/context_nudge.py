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
import uuid
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
    valid_handoff,
)
from claude_code_core.frontend import Choice, ChoicePrompt

from ..surface import DiscordSurface
from ..voice_labels import label_key, strip_title_tag, tagged_title

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
        self._handing_off: set[int] = set()

    def after_turn(self, thread: discord.Thread | discord.TextChannel) -> None:
        """Schedule the check so a waiting nudge never blocks the next message."""
        if not isinstance(thread, discord.Thread) or thread.id in self.skip_thread_ids:
            return
        task = asyncio.create_task(self._check(thread))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _check(self, thread: discord.Thread) -> None:
        try:
            if thread.id in self._handing_off:
                return
            record = await self._chat.repo.get(thread.id)
            if record is None:
                return
            step = nudge_step(
                record.context_used, record.context_window, self._offered.get(thread.id, 0)
            )
            if step is None:
                return
            self._offered[thread.id] = step
            pct = (record.context_used or 0) * 100 / (record.context_window or 1)
            yes = await self._ask(thread, pct, getattr(record, "backend", None))
            if yes:
                await self.hand_off(thread)
        except Exception:
            logger.warning("context nudge failed for thread %s", thread.id, exc_info=True)

    async def _ask(self, thread: discord.Thread, pct: float, backend: str | None = None) -> bool:
        # "Yes" runs a model turn and opens a thread, so only the people allowed
        # to talk to the bot may press it — the same set the chat cog enforces.
        surface = DiscordSurface(thread, allowed_user_ids=self._chat._allowed_user_ids)
        answer = await surface.prompt_choice(
            ChoicePrompt(
                question=(
                    f"This session is about {context_label(pct, backend)} full. "
                    "Continue in a fresh session with a saved handoff?"
                ),
                header="🧹 Start fresh?",
                choices=(
                    Choice(value=_YES, label="Continue", style="positive"),
                    Choice(value=_NO, label="Not now"),
                ),
                timeout_seconds=NUDGE_TIMEOUT_SECONDS,
            )
        )
        return bool(answer) and _YES in (answer or ())

    async def hand_off(self, thread: discord.Thread) -> discord.Thread | None:
        """Write the handoff in this session, then open the fresh thread from it."""
        if thread.id in self._handing_off:
            return None
        self._handing_off.add(thread.id)
        try:
            return await self._hand_off(thread)
        finally:
            self._handing_off.discard(thread.id)

    async def _hand_off(self, thread: discord.Thread) -> discord.Thread | None:
        record = await self._chat.repo.get(thread.id)
        workdir = (record.working_dir if record else None) or getattr(
            self._chat.runner, "working_dir", None
        )
        parent = thread.parent
        if not isinstance(workdir, str) or not isinstance(parent, discord.TextChannel):
            await thread.send("-# Couldn't hand off: this thread has no project folder.")
            return None
        path = handoff_path(Path(workdir), thread.name, datetime.date.today().isoformat())
        path = path.with_name(f"{path.stem}-{uuid.uuid4().hex}.md")
        if path.exists() or not path.resolve().is_relative_to(Path(workdir).resolve()):
            await thread.send("Couldn't create a fresh handoff inside this project. Staying here.")
            return None
        seed = await thread.send("-# 📝 Saving a handoff for the fresh session…")
        await self._chat.run_resumed_turn(seed, thread, handoff_prompt(path))
        if not valid_handoff(path, Path(workdir)):
            await thread.send(
                "The handoff wasn't saved with the required details. Staying in this session."
            )
            return None
        settings = self._chat._backend_settings
        backend = record.backend if record else None
        model = record.model if record else None
        effort = None
        if settings is not None:
            backend = await settings.current_backend(thread.id)
            model = await settings.current_model(backend, thread.id)
            effort = await settings.current_effort(backend, thread.id)
        prompt = starter_prompt(path, thread.mention)
        new = await self._chat.spawn_session(
            parent,
            prompt,
            thread_name=next_thread_name(thread.name),
            working_dir=workdir,
            backend=backend,
            model=model,
            auto_start=False,
        )
        if settings is not None and backend and effort:
            await settings.set_effort(backend, effort, thread_id=new.id)
        await thread.send(
            f"Continuing in {new.mention}. This session stays open until it succeeds."
        )
        outcome: tuple[str | None, str | None] | None = None

        async def capture(text: str | None, error: str | None) -> None:
            nonlocal outcome
            outcome = (text, error)

        seed = await new.send("Reading the handoff and continuing approved work.")
        await self._chat.run_resumed_turn(seed, new, prompt, result_sink=capture)
        if outcome is None or outcome[1] or not outcome[0]:
            await thread.send(
                "The new session couldn't complete its first turn. This one stays open."
            )
            return None
        await self._carry_spoken_tag(thread, new)
        with contextlib.suppress(discord.HTTPException):
            await thread.send(f"➡️ Continued in {new.mention}. Handoff saved at `{path}`.")
        # The conversation moved, so this session is finished. Leaving it open
        # made the person close it by hand, and until they did it still counted
        # as a live session and still answered to the spoken tag.
        await self._chat.close_session(thread)
        return new

    async def _carry_spoken_tag(self, old: discord.Thread, new: discord.Thread) -> None:
        """Hand ``old``'s spoken tag to ``new``, title and all.

        A tag is a word the speaker has learned for a conversation, and the
        conversation is what continues — so it has to follow the handoff. Left
        alone, the tag stayed on the thread that just ended: saying it reached a
        session that was over, while the live one answered to a word nobody had
        been told.

        The rename is done here rather than left to the next ``/api/sessions``
        poll because the first thing said after a handoff is usually said
        straight away, and a tag that is not in the sidebar yet cannot be read
        off it.
        """
        repo = getattr(self._chat, "_settings_repo", None)
        if repo is None:
            return
        try:
            label = await repo.get(label_key(old.id))
            if not label:
                return
            await repo.set(label_key(new.id), label)
            await repo.delete(label_key(old.id))
        except Exception:
            logger.warning("could not move the spoken tag to thread %s", new.id, exc_info=True)
            return
        with contextlib.suppress(discord.HTTPException):
            await new.edit(name=tagged_title(new.name, label))
        # And take it off the finished thread, so the sidebar never shows the
        # same word twice with only one of them listening.
        with contextlib.suppress(discord.HTTPException):
            await old.edit(name=strip_title_tag(old.name))
