"""Understanding "make me a session about Boa in the boa folder" when it is typed.

The control center is a place you type. Most of what lands there is a question,
and the answer is a quick chat in a thread. Some of it is "I need a session
about X in the Y folder", and the answer is a thread bound to that folder — the
same thing `/cd` does, asked for in the way a person actually says it.

Telling those apart is done in two stages, and the order is the point. A cheap
pure check runs on every message and lets the ordinary ones through untouched,
so typing a question stays instant and costs nothing. Only a message that
mentions a session at all reaches the model, and the model is asked one narrow
question with a machine-readable answer.

Every failure here means "not a session request": an unreachable model, a
timeout, unparseable output, a folder nobody recognises. The fallback is the
quick chat that would have happened anyway, which is why this can be wrong
without being harmful. Guessing *wrong* about a folder is the thing to avoid,
not declining to guess.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from claude_code_core.win_subprocess import NO_WINDOW

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30

#: The cheap gate. A message that never mentions a session or a thread is not
#: asking for one, and must not cost a model call — that is most of what gets
#: typed. Deliberately generous: a false positive costs one call and lands back
#: on the quick chat, a false negative silently ignores what was asked for.
_MENTIONS_A_SESSION = re.compile(r"\b(session|sessions|thread|threads)\b", re.IGNORECASE)

_PROMPT_TEMPLATE = """\
Decide whether the message below is asking to START a new working session in a \
particular folder on this computer.

Reply with one line of JSON and nothing else:
{{"start": true|false, "folder": "<folder name they named, or empty>", "task": "<what \
the session should do, in their own words>"}}

Rules:
- "start" is true only if they are asking for a NEW session to be opened.
- Asking a question about an existing session, or just chatting, is false.
- "folder" is the folder name as they said it. Empty if they named none.
- "task" is what they want the session to work on, with the folder and the \
"make me a session" wrapper removed. If they gave no task, use an empty string.
- The message was spoken aloud and transcribed, so it may ramble or be \
mispunctuated. Judge the intent, not the grammar.

Known folder names on this computer: {folders}

Message:
{text}
"""


@dataclass(frozen=True, slots=True)
class SessionRequest:
    """A request to open a session, as read out of plain language."""

    folder: str
    """The folder name the person said, not yet resolved to a path."""

    task: str
    """What the session should work on. May be empty — an idle thread is fine."""


def mentions_a_session(text: str) -> bool:
    """The cheap gate: whether *text* is worth asking a model about at all."""
    return bool(_MENTIONS_A_SESSION.search(text or ""))


def parse_verdict(raw: str) -> SessionRequest | None:
    """Read the model's one line of JSON, or ``None`` for anything else.

    Unparseable output is not an error to report — it is the model failing to
    answer a yes/no question, and the caller's fallback is the quick chat that
    was going to happen anyway.
    """
    for line in raw.splitlines():
        line = line.strip().strip("`").strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("start") is not True:
            return None
        folder = data.get("folder")
        task = data.get("task")
        if not isinstance(folder, str) or not folder.strip():
            return None
        return SessionRequest(
            folder=folder.strip(),
            task=task.strip() if isinstance(task, str) else "",
        )
    return None


async def read_session_request(
    text: str,
    *,
    folders: list[str],
    claude_command: str = "claude",
    env: dict[str, str] | None = None,
) -> SessionRequest | None:
    """Whether *text* asks to open a session, and in which folder.

    Returns ``None`` whenever the answer is not a confident yes — including
    every failure mode — so the caller falls back to a quick chat.
    """
    if not mentions_a_session(text):
        return None
    prompt = _PROMPT_TEMPLATE.format(
        folders=", ".join(folders[:40]) or "(none known)",
        text=text[:2000],
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_command,
            "-p",
            "--model",
            "haiku",
            "--",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **NO_WINDOW,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.debug("session-request read timed out; falling back to a quick chat")
            return None
        if proc.returncode != 0:
            logger.debug("session-request read exited %d", proc.returncode)
            return None
        return parse_verdict(stdout.decode(errors="replace"))
    except Exception:
        logger.debug("session-request read failed; falling back to a quick chat", exc_info=True)
        return None
