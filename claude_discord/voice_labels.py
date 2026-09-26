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

#: The NATO phonetic alphabet, chosen for being mutually unmistakable when
#: spoken over a poor channel — which is what a room microphone is.
SPOKEN_LABELS: tuple[str, ...] = (
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
    "xray",
    "yankee",
    "zulu",
)


def assign_labels(
    thread_ids: list[int],
    existing: dict[int, str],
) -> tuple[dict[int, str], dict[int, str]]:
    """Give every thread in ``thread_ids`` a tag, keeping the ones it has.

    Args:
        thread_ids: The visible threads, in the order tags should be handed out
            (most relevant first — those are the ones most likely to be spoken).
        existing: Stored thread_id → tag, including threads no longer visible.

    Returns:
        ``(labels, new)`` — the full mapping for the visible threads, and just
        the assignments that were not already stored, so the caller writes only
        what changed.
    """
    visible = set(thread_ids)
    # A tag still held by a visible thread is taken; one held by a thread that
    # has scrolled out of the set is free to reissue.
    kept = {tid: label for tid, label in existing.items() if tid in visible}
    taken = set(kept.values())
    free = [label for label in SPOKEN_LABELS if label not in taken]

    labels = dict(kept)
    new: dict[int, str] = {}
    for thread_id in thread_ids:
        if thread_id in labels:
            continue
        if not free:
            # More visible threads than tags. The remainder stay untagged and
            # are still reachable by name; silently reusing a tag would send a
            # command to the wrong thread, which is the one unacceptable
            # outcome here.
            break
        label = free.pop(0)
        labels[thread_id] = label
        new[thread_id] = label
    return labels, new
