"""Short spoken tags for threads, because a folder name is not a handle.

Matching a spoken thread name against a Discord title works until the title is
"📂 ebi-agent-chat-relay". Said out loud that is five words, two of which
("chat") are also the word the grammar uses to end a name, and speech
recognition inserts the spaces wherever it likes. The first live command found
exactly that: the sentence was heard perfectly and still split in the wrong
place.

So every thread gets a one-word tag instead, and the tag is drawn from the NATO
phonetic alphabet — a list whose entire design purpose is to survive a bad
channel. "Bravo" cannot be misheard as "delta"; "ebi agent chat relay" can be
misheard as almost anything.

Two properties matter more than elegance:

*Stability* — a tag is stored, never recomputed from position, so the thread
called `bravo` this morning is still `bravo` tonight. A tag that shifted when a
new thread appeared would be worse than no tag, because the speaker would not
know it had shifted.

*Recycling* — the pool is 26 long and a busy machine has hundreds of archived
threads, so a tag is released when its thread leaves the visible set and handed
to the next one that needs it. Without that, tags would run out in a week.
"""

from __future__ import annotations

import re

#: The tag pool: One Piece characters, Drew's own choice of words.
#:
#: The pool is swappable and its contents are taste, but one property is not
#: negotiable: two tags must not be one mishearing apart, because a tag decides
#: which repository a spoken instruction lands in. So no two of these share
#: their first two letters, and none of them is a word that turns up in ordinary
#: speech — "law", "ace", "brook" and "smoker" are all One Piece characters and
#: all disqualified for exactly that reason.
#:
#: The crew comes first because tags are handed out in order and those are the
#: names Drew sees most.
SPOKEN_LABELS: tuple[str, ...] = (
    "luffy",
    "zoro",
    "nami",
    "sanji",
    "chopper",
    "franky",
    "jinbe",
    "usopp",
    "shanks",
    "mihawk",
    "doflamingo",
    "oden",
    "kaido",
    "marco",
    "yamato",
    "rayleigh",
    "hancock",
    "bonney",
    "perona",
    "kinemon",
    "momonosuke",
    "vivi",
    "buggy",
    "garp",
    "ivankov",
    "tashigi",
)

#: What the recogniser writes instead, mapped back to the tag it meant.
#:
#: "Aldus" came through as "oldest" and always will — the word is not in the
#: model's vocabulary, so it substitutes one that is (see ``folders.mjs``). A
#: character name is outside that vocabulary in exactly the same way, and the
#: fix cannot be a fuzzy match: the consonant skeleton of a four-letter name is
#: two characters long, so fuzzy matching on tags would route "nami" and
#: "kaido" to each other. These are matched exactly, and the list grows from
#: what the transcript log actually shows rather than from guesses about
#: phonetics.
LABEL_ALIASES: dict[str, str] = {
    "lucy": "luffy",
    "loofy": "luffy",
    "luffie": "luffy",
    "laffy": "luffy",
    "zorro": "zoro",
    "soro": "zoro",
    "naomi": "nami",
    "nammy": "nami",
    "sanjay": "sanji",
    "sangi": "sanji",
    "jimbe": "jinbe",
    "jimbei": "jinbe",
    "ginbe": "jinbe",
    "usop": "usopp",
    "mihalk": "mihawk",
    "myhawk": "mihawk",
    "odin": "oden",
    "olden": "oden",
    "flamingo": "doflamingo",
    "doflamingos": "doflamingo",
    "kaidou": "kaido",
    "cairo": "kaido",
    "raleigh": "rayleigh",
    "rayly": "rayleigh",
    "momonoske": "momonosuke",
    "kinnemon": "kinemon",
    "parona": "perona",
    "veevee": "vivi",
    "vivian": "vivi",
    "garth": "garp",
    "ivancov": "ivankov",
    "tashigee": "tashigi",
}


#: Membership test for the pool, used on every utterance.
_POOL = frozenset(SPOKEN_LABELS)


def heard_as(spoken: str) -> str | None:
    """The tag ``spoken`` means, or None when it is not one.

    Punctuation and case are the recogniser's business, not the speaker's, so
    they are stripped before comparing. Everything else is exact.
    """
    word = "".join(ch for ch in str(spoken or "").lower() if ch.isalpha())
    if not word:
        return None
    if word in _POOL:
        return word
    return LABEL_ALIASES.get(word)


def aliases_for(label: str | None) -> tuple[str, ...]:
    """The mishearings that resolve to ``label``, sorted for a stable payload.

    Sent alongside the tag in the session view so the voice layer can treat
    them as wake words without keeping its own copy of the table.
    """
    if not label:
        return ()
    return tuple(sorted(a for a, tag in LABEL_ALIASES.items() if tag == label))


def assign_labels(
    thread_ids: list[int],
    existing: dict[int, str],
) -> tuple[dict[int, str], dict[int, str], set[int]]:
    """Give every thread in ``thread_ids`` a tag, keeping the ones it has.

    A tag is a word the speaker has learned, so it is held for as long as it can
    be: a thread keeps its tag after it scrolls out of the visible set, and gets
    the same one back if it comes round again.  Only when all 26 are spoken for
    does one get taken away, and then from the *oldest* thread that is not
    currently visible — Discord IDs are snowflakes, so the smallest id is the
    thread whose tag the speaker is least likely to still have in mind.

    Args:
        thread_ids: The visible threads, in the order tags should be handed out
            (most relevant first — those are the ones most likely to be spoken).
        existing: Stored thread_id → tag, including threads no longer visible.

    Returns:
        ``(labels, new, released)`` — the mapping for the visible threads, the
        assignments that were not already stored, and the threads whose tag was
        taken away, so the caller writes and deletes only what changed.
    """
    visible = set(thread_ids)
    # A tag that is no longer in the pool is not held. Without this, changing
    # the pool would leave every already-tagged thread on the old naming scheme
    # for as long as it lives, and the two schemes would coexist indefinitely.
    existing = {tid: label for tid, label in existing.items() if label in _POOL}
    labels = {tid: label for tid, label in existing.items() if tid in visible}
    taken = dict(existing)  # every tag still promised to some thread
    free = [label for label in SPOKEN_LABELS if label not in set(taken.values())]
    released: set[int] = set()

    # Oldest first: the thread least likely to be spoken to gives up its tag.
    reclaimable = sorted(tid for tid in taken if tid not in visible)

    for thread_id in thread_ids:
        if thread_id in labels:
            continue
        if not free:
            if not reclaimable:
                # Every tag belongs to a thread that is visible right now. The
                # remainder stay untagged and are still reachable by name;
                # reusing a live tag would send a command to the wrong thread,
                # which is the one unacceptable outcome here.
                break
            donor = reclaimable.pop(0)
            free.append(taken.pop(donor))
            released.add(donor)
        label = free.pop(0)
        labels[thread_id] = label
        taken[thread_id] = label
    new = {tid: label for tid, label in labels.items() if existing.get(tid) != label}
    return labels, new, released


#: A tag shown at the front of a Discord thread title, e.g. "[bravo] 📂 repo".
#: Matched loosely on read so a hand-edited title still round-trips.
_TITLE_TAG_RE = re.compile(r"^\s*\[([a-z]{3,10})\]\s*")

#: Discord's thread-name ceiling.
MAX_THREAD_NAME = 100


def strip_title_tag(title: str) -> str:
    """Return ``title`` without a leading ``[tag] `` prefix."""
    return _TITLE_TAG_RE.sub("", str(title or "")).strip()


def title_tag(title: str) -> str | None:
    """Return the tag already shown in ``title``, if any."""
    # Coerced rather than typed strictly: the argument is a Discord thread
    # name, and a title is never worth raising over.
    match = _TITLE_TAG_RE.match(str(title or ""))
    return match.group(1) if match else None


def tagged_title(title: str, label: str | None) -> str:
    """Put ``label`` at the front of ``title``, replacing any tag already there.

    The tag has to be readable straight off the Discord sidebar — a roster in
    another channel answers "which tag is that thread?" but not the question
    actually being asked, which is "what do I say to *this* one?". The prefix is
    idempotent so re-applying it never stacks, and the *name* is truncated
    rather than the tag, because a title missing its last word is still usable
    and a tag missing a letter is not.
    """
    base = strip_title_tag(title)
    if not label:
        return base[:MAX_THREAD_NAME]
    prefix = f"[{label}] "
    return (prefix + base[: MAX_THREAD_NAME - len(prefix)]).strip()


#: Settings key prefix a thread's tag is stored under.
#:
#: The tag outlives the thread that earned it — a continuation thread inherits
#: it (``cogs/context_nudge.py``) so the word the speaker learned still reaches
#: the live conversation. That means more than one module reads and writes this
#: key, so it is spelled once, here, next to the tags themselves.
VOICE_LABEL_PREFIX = "voice_label:"


def label_key(thread_id: int) -> str:
    """The settings key holding ``thread_id``'s tag."""
    return f"{VOICE_LABEL_PREFIX}{thread_id}"


def thread_id_from_key(key: str) -> int | None:
    """The thread a tag key belongs to, or None when ``key`` is not one.

    The settings table holds unrelated keys, and a hand-edited row is not worth
    raising over, so anything that is not ``voice_label:<digits>`` is simply not
    a tag.
    """
    text = str(key or "")
    if not text.startswith(VOICE_LABEL_PREFIX):
        return None
    try:
        return int(text[len(VOICE_LABEL_PREFIX) :])
    except ValueError:
        return None
