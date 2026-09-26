"""A message the user said out loud, delivered as their own words.

Thread-to-thread relay (:mod:`claude_discord.relay`) exists for one agent
talking to another, and its framing says so in as many words: "This is NOT
from your human."  A voice surface needs the opposite guarantee.  The person
who owns the bot spoke, and the session has to act on it exactly as if they
had typed it — so this cannot be a flag on the relay prompt.  Every spoken
instruction would arrive disclaimed, and ``RelayGuard``, which exists to stop
two agents answering each other forever, would throttle a human mid-sentence
(one message per pair per minute is a conversation-ending rule to apply to
someone talking).

What a spoken message *does* have to declare is how it arrived.  Speech
recognition mishears names, paths, filenames and flags; a session that takes
a garbled token as a literal identifier spends its turn on an artefact of the
microphone.  The marker says the words came through a transcript, so an
odd-looking one is read for intent and named rather than queried.

The gate is the caller's own: the control-plane secret, plus the owner check
in the endpoint.  There is deliberately no rate limit here.
"""

from __future__ import annotations

#: The only transcription surface today.  A new one (a phone bridge, a
#: dictation client) adds a member here rather than a second endpoint.
VOICE = "voice"
VALID_SOURCES = (VOICE,)

#: Matches the relay ceiling: one utterance, not a pasted document.
MAX_SPOKEN_TEXT_CHARS = 4000


def build_spoken_prompt(*, text: str, source: str = VOICE) -> str:
    """Wrap an utterance so the receiver treats it as its human's own words."""
    return (
        f"[SPOKEN BY YOUR HUMAN — live {source} transcript]\n"
        "These are their own words, said out loud just now. This is NOT a relay "
        "from another session: act on it exactly as you would a message they typed.\n"
        "It reached you through speech recognition, so names, paths, filenames, "
        "flags and command syntax may be misheard. Read an odd-looking word for "
        "intent, say in one line which word you reinterpreted and as what, and "
        "carry on — do not stop and ask about what is probably a transcription "
        "artefact.\n"
        "---\n"
        f"{text}"
    )
