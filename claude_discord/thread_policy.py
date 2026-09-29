"""How long Discord keeps a conversation thread visible, and when it may be posted in.

Discord archives an inactive thread automatically, which removes it from the
channel's thread list — the conversation still exists, but the user has to open
"Show archived threads" to find it again. The window is chosen per thread at
creation time and only accepts 60, 1440, 4320 or 10080 minutes.

We always ask for the maximum. Threads here are conversations the user comes
back to, and several are deliberately kept open as a to-do list; a thread that
vanishes from the sidebar an hour after the last reply reads as lost work.

The reverse rule matters just as much. A thread that has been put away — by
hand, by EBI, or by Discord's own sweep — is a closed session, and Discord
un-archives a thread the moment anything is posted in it. So a post nobody
asked for (a reminder, a restart notice, a progress line, a summary) would
quietly bring back a conversation the person had finished with.
:func:`may_post_unsolicited` is the one check every such post goes through. A
person's own message is the one thing that reopens a thread, and that never
comes through here.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from claude_code_core.session_repo import LifecycleState

logger = logging.getLogger(__name__)

# Discord's maximum auto-archive window (7 days, in minutes).
THREAD_AUTO_ARCHIVE_MINUTES = 10080

#: A session on its way out, or gone: its thread is archived, or about to be.
_PUT_AWAY = frozenset({LifecycleState.CLOSED.value, LifecycleState.CLOSING.value})


def thread_is_archived(thread: Any) -> bool:
    """Discord's own word that ``thread`` is archived.

    Only a literal ``True`` counts. A real thread always carries a boolean, so
    the strictness costs nothing there; what it buys is that an object that
    cannot say — a plain channel, a stand-in — reads as visible. The failure
    mode of this guard is silence, and silence is the one failure nobody
    notices, so "unknown" must never be mistaken for "archived".
    """
    return getattr(thread, "archived", False) is True


def _thread_is_locked(thread: Any) -> bool:
    return getattr(thread, "locked", False) is True


async def may_post_unsolicited(thread: Any, repo: Any | None = None) -> bool:
    """May the bot post in ``thread`` without having been asked to?

    ``False`` for a thread that is missing, archived or locked, and — when a
    session ``repo`` is given — for one whose row is closed or closing: that
    close is on its way to archiving the thread, and a post now would race it
    or undo it. A channel that cannot be archived is not a session in this
    sense and is always fair game. A store that cannot answer silences
    nothing: Discord's own flags were consulted first and are the stronger
    signal, so the failed read is logged rather than raised.
    """
    if thread is None:
        return False
    if thread_is_archived(thread) or _thread_is_locked(thread):
        return False
    if repo is None or not isinstance(thread, discord.Thread):
        return True
    try:
        record = await repo.get(thread.id)
    except Exception:
        logger.warning("Could not read the session row for thread %s", thread.id, exc_info=True)
        return True
    return getattr(record, "lifecycle_state", None) not in _PUT_AWAY
