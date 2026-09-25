"""Find a folder from a few typed letters, with no Discord and no I/O in the ranking.

Picking a folder used to mean walking a menu: favorites, then recents, then a
browser one directory at a time. That is fine once and slow every day, so the
fast path is a slash command whose one argument is autocompleted — the same
motion as a shell `cd`, except the list is already there before the first
keystroke.

Two functions, split on purpose. :func:`scan_project_folders` touches the disk
and is cached by its caller; :func:`rank_folders` is pure, so "does typing
`echat` reach `ebi-agent-chat-relay`?" is a unit test and not a Discord
session. Matching is deliberately forgiving — separators and case are dropped,
and scattered letters still match — because the user is typing from memory of a
name, not transcribing a path.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

#: Folders that are never a working directory worth offering.
SKIP_NAMES = frozenset(
    {
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        "target",
        "site-packages",
        "coverage",
    }
)

#: Discord's ceiling for both a choice label and a choice value.
LABEL_LIMIT = 100


def _normalize(value: str) -> str:
    """Casefold and drop the separators people do not type consistently."""
    return "".join(char for char in value.casefold() if char.isalnum())


def _is_offerable(path: Path) -> bool:
    return not path.name.startswith(".") and path.name not in SKIP_NAMES


def scan_project_folders(
    roots: Iterable[str],
    *,
    depth: int = 2,
    cap: int = 400,
) -> list[str]:
    """Folders under ``roots``, shallowest first, bounded by ``cap``.

    Blocking: call it in a thread. An unreadable or missing root is skipped
    rather than raising, because one bad entry in ``CCDB_PROJECT_ROOTS`` must
    not take the whole autocomplete down.
    """
    found: list[str] = []
    seen: set[str] = set()
    level = [Path(root).expanduser() for root in roots if str(root).strip()]
    roots_seen = list(level)
    for _ in range(max(depth, 0)):
        if len(found) >= cap:
            break
        children: list[Path] = []
        for parent in level:
            try:
                entries = sorted(
                    (
                        child
                        for child in parent.iterdir()
                        if child.is_dir() and _is_offerable(child)
                    ),
                    key=lambda child: (child.name.casefold(), child.name),
                )
            except (OSError, ValueError):
                continue
            children.extend(entries)
        for child in children:
            text = str(child)
            if text in seen:
                continue
            seen.add(text)
            found.append(text)
            if len(found) >= cap:
                break
        level = children
    # The roots themselves are legitimate working folders, but never the point.
    for root in roots_seen:
        text = str(root)
        if len(found) >= cap:
            break
        if text not in seen and root.is_dir():
            seen.add(text)
            found.append(text)
    return found[:cap]


def _score(query: str, path: str) -> tuple[int, int] | None:
    """``(tier, runs)`` for ``path``, or ``None`` when it does not match.

    ``tier`` prefers the folder's own name over its path; ``runs`` is how many
    separate pieces the typed letters landed in, so `echat` reaches
    `ebi-agent-chat-relay` (two pieces) ahead of `epic-health` (four).
    """
    name = _normalize(Path(path).name)
    whole = _normalize(path)
    if not query:
        return 0, 1
    if name == query:
        return 0, 1
    if name.startswith(query):
        return 1, 1
    if query in name:
        return 2, 1
    runs = _fuzzy_runs(query, name)
    if runs is not None:
        return 3, runs
    if query in whole:
        return 4, 1
    runs = _fuzzy_runs(query, whole)
    if runs is not None:
        return 5, runs
    return None


def _fuzzy_runs(query: str, text: str) -> int | None:
    """How many contiguous pieces ``query`` occupies in ``text``; ``None`` if absent."""
    position = 0
    runs = 0
    previous = -2
    for char in query:
        position = text.find(char, position)
        if position < 0:
            return None
        if position != previous + 1:
            runs += 1
        previous = position
        position += 1
    return runs


#: Index of the disk-scan group in :func:`rank_folders`. Everything before it
#: is this operator's own history and therefore has a recency to sort by.
_SCAN_GROUP = 2


def rank_folders(
    query: str,
    *,
    candidates: Sequence[str],
    recents: Sequence[str] = (),
    favorites: Sequence[str] = (),
    limit: int = 25,
) -> list[str]:
    """The folders to offer for ``query``, best first.

    With no query this is "what you used last", which is the common case: the
    list is useful before anything is typed. With a query, a folder the user
    has actually opened wins ties against one merely found on disk.
    """
    groups: dict[str, int] = {}
    order: dict[str, int] = {}
    for group, paths in enumerate((recents, favorites, candidates)):
        for path in paths:
            if path not in groups:
                groups[path] = group
                order[path] = len(order)
    needle = _normalize(query)
    scored: list[tuple[tuple[int, int, int, int, int, int, int], str]] = []
    for path in groups:
        score = _score(needle, path)
        if score is None:
            continue
        tier, runs = score
        if not needle:
            # Nothing typed: history order is the answer, not the filesystem's.
            scored.append(((0, groups[path], order[path], 0, 0, 0, order[path]), path))
            continue
        entry = Path(path)
        # Among equally good matches, history is ordered by *recency* and the
        # disk scan by path shape. Ranking a folder the person just worked in
        # behind a shorter path answers a question about the filesystem when
        # they asked "which of these did I have open". Scan results have no
        # recency to sort by, so they keep the shape tiebreakers.
        history = order[path] if groups[path] < _SCAN_GROUP else 0
        scored.append(
            (
                (tier, groups[path], history, runs, len(entry.parts), len(entry.name), order[path]),
                path,
            )
        )
    scored.sort()
    return [path for _, path in scored[:limit]]


def choice_label(path: str) -> str:
    """``name — where it lives``, inside Discord's 100-character ceiling."""
    entry = Path(path)
    name = entry.name or str(entry)
    parent = str(entry.parent)
    home = str(Path.home())
    if parent.startswith(home):
        parent = "~" + parent[len(home) :]
    label = f"{name} — {parent}" if entry.parent != entry else name
    if len(label) <= LABEL_LIMIT:
        return label
    name = name[: LABEL_LIMIT - 4]
    room = LABEL_LIMIT - len(name) - 4
    return f"{name} — …{parent[-room:]}" if room > 1 else name[:LABEL_LIMIT]
